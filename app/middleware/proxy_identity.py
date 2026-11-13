"""Identity helpers for explicitly trusted reverse proxies."""

from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from typing import Mapping


ProxyNetwork = IPv4Network | IPv6Network


def parse_trusted_proxy_sources(sources: str) -> tuple[ProxyNetwork, ...]:
    """Parse comma-separated proxy IPs/CIDRs; invalid configuration fails closed."""
    if not isinstance(sources, str):
        raise ValueError("trusted proxy sources must be a string")

    # An empty environment variable means that no proxy is trusted. Once a
    # value is supplied, however, every comma-separated entry must be valid;
    # silently dropping an empty entry could hide a broken deployment setting.
    if not sources.strip():
        return ()

    networks: list[ProxyNetwork] = []
    for raw_source in sources.split(","):
        source = raw_source.strip()
        if not source:
            raise ValueError("trusted proxy sources cannot contain empty entries")
        try:
            networks.append(ip_network(source, strict=False))
        except ValueError as exc:
            raise ValueError(f"invalid trusted proxy source: {source!r}") from exc
    return tuple(networks)


def is_trusted_proxy(
    peer_host: str | None,
    trusted_networks: tuple[ProxyNetwork, ...],
) -> bool:
    """Return whether the socket peer belongs to a configured proxy network."""
    if not isinstance(peer_host, str) or not peer_host:
        return False
    try:
        peer = ip_address(peer_host)
    except ValueError:
        return False
    return any(
        peer.version == network.version and peer in network
        for network in trusted_networks
    )


def _single_header(headers: Mapping[str, str], name: str) -> str | None:
    """Read an unambiguous, nonempty identity header value."""
    getlist = getattr(headers, "getlist", None)
    if callable(getlist):
        try:
            values = list(getlist(name))
        except (TypeError, ValueError):
            return None
    else:
        # Keep plain mappings case-insensitive like an HTTP header collection,
        # while treating case-variant duplicates as ambiguous too.
        values = [
            value
            for key, value in headers.items()
            if isinstance(key, str) and key.casefold() == name.casefold()
        ]

    if len(values) != 1 or not isinstance(values[0], str):
        return None
    value = values[0].strip()
    if not value or "," in value or any(
        ord(char) < 0x20 or ord(char) == 0x7F for char in value
    ):
        return None
    return value


def get_trusted_proxy_identity(
    headers: Mapping[str, str],
    peer_host: str | None,
    trusted_networks: tuple[ProxyNetwork, ...],
) -> tuple[str | None, str | None]:
    """Read Authentik or forwarded identity only from a trusted socket peer.

    Authentik values take precedence per field. Conflicting valid values from
    the two header families make the whole proxy identity ambiguous.
    """
    if not is_trusted_proxy(peer_host, trusted_networks):
        return None, None

    authentik_user = _single_header(headers, "x-authentik-username")
    forwarded_user = _single_header(headers, "x-forwarded-user")
    authentik_email = _single_header(headers, "x-authentik-email")
    forwarded_email = _single_header(headers, "x-forwarded-email")

    if (
        authentik_user is not None
        and forwarded_user is not None
        and authentik_user != forwarded_user
    ) or (
        authentik_email is not None
        and forwarded_email is not None
        and authentik_email != forwarded_email
    ):
        return None, None

    return (
        authentik_user or forwarded_user,
        authentik_email or forwarded_email,
    )


def _usable_identity(value: object) -> str | None:
    """Return identity text only for a non-blank string claim, trimmed.

    Identity values key per-user memory, so they must be plain strings:
    non-string claims (a malformed signed token can carry any JSON type)
    and blank values are skipped instead of crashing the store or keying
    memory on whitespace.
    """
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def resolve_user_identity(
    jwt_user_info: dict | None,
    proxy_user: str | None,
    proxy_email: str | None,
) -> tuple[str | None, str | None]:
    """Resolve the memory identity and display name from identity inputs.

    This is the single precedence point for authentication identity:

    1. An authenticated JWT wins over any forwarded identity. OAuth-issued
       tokens (``auth_method: google_oauth``) carry identity claims and
       resolve through this same path — they are not special-cased. Within
       a JWT, a usable ``email`` claim precedes the ``sub`` claim.
    2. Without a JWT identity, trusted proxy email precedes username.
       ``get_trusted_proxy_identity`` accepts ``x-authentik-email`` and
       ``x-authentik-username`` with ``x-forwarded-email`` and
       ``x-forwarded-user`` fallbacks, and rejects conflicting values from
       the two families. Untrusted headers never reach this function.
    3. With neither, the caller is anonymous and receives ``(None, None)``.

    Resolution is deterministic when identity inputs conflict or when a JWT
    has no email: every claim must be a non-blank string, otherwise it is
    skipped and resolution falls through to the next source in the order
    above. The same inputs always produce the same identity.
    """
    if jwt_user_info:
        identity = _usable_identity(
            jwt_user_info.get("email")
        ) or _usable_identity(jwt_user_info.get("sub"))
        if identity:
            display = _usable_identity(jwt_user_info.get("name")) or identity
            return identity, display
    identity = _usable_identity(proxy_email) or _usable_identity(proxy_user)
    if identity:
        return identity, identity
    return None, None
