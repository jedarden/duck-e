"""Identity helpers for explicitly trusted reverse proxies."""

from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from typing import Mapping


ProxyNetwork = IPv4Network | IPv6Network


def parse_trusted_proxy_sources(sources: str) -> tuple[ProxyNetwork, ...]:
    """Parse comma-separated proxy IPs/CIDRs; invalid configuration fails closed."""
    networks: list[ProxyNetwork] = []
    for source in sources.split(","):
        source = source.strip()
        if source:
            networks.append(ip_network(source, strict=False))
    return tuple(networks)


def is_trusted_proxy(
    peer_host: str | None,
    trusted_networks: tuple[ProxyNetwork, ...],
) -> bool:
    """Return whether the socket peer belongs to a configured proxy network."""
    if not peer_host:
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
    values = getlist(name) if callable(getlist) else [headers.get(name)]
    if len(values) != 1 or values[0] is None:
        return None
    value = values[0].strip()
    if not value or any(char in value for char in ",\r\n"):
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
