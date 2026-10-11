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
    """Return x-forwarded-user/email only when the socket peer is trusted."""
    if not is_trusted_proxy(peer_host, trusted_networks):
        return None, None
    return (
        _single_header(headers, "x-forwarded-user"),
        _single_header(headers, "x-forwarded-email"),
    )


def resolve_user_identity(
    jwt_user_info: dict | None,
    proxy_user: str | None,
    proxy_email: str | None,
) -> tuple[str | None, str | None]:
    """Resolve memory identity and display name, preferring signed JWT claims."""
    if jwt_user_info:
        identity = jwt_user_info.get("email") or jwt_user_info.get("sub")
        if identity:
            return identity, jwt_user_info.get("name") or identity
    if proxy_email:
        return proxy_email, proxy_email
    if proxy_user:
        return proxy_user, proxy_user
    return None, None
