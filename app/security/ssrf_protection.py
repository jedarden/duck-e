"""
SSRF Protection Module
OWASP API7: Server Side Request Forgery Prevention

Blocks:
- Localhost and loopback addresses (127.0.0.1, ::1)
- Private IP ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
- Cloud metadata endpoints (AWS, GCP, Azure)
- Link-local addresses (169.254.0.0/16)
- DNS rebinding attacks
- Non-HTTP(S) schemes
- URLs with embedded credentials
"""
import ipaddress
import socket
from urllib.parse import urlparse
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class SSRFProtection:
    """SSRF protection with comprehensive IP and hostname validation"""

    # Blocked private IP ranges (RFC 1918)
    PRIVATE_IP_RANGES = [
        ipaddress.ip_network('10.0.0.0/8'),         # Class A private
        ipaddress.ip_network('172.16.0.0/12'),      # Class B private
        ipaddress.ip_network('192.168.0.0/16'),     # Class C private
        ipaddress.ip_network('127.0.0.0/8'),        # Loopback
        ipaddress.ip_network('169.254.0.0/16'),     # Link-local
        ipaddress.ip_network('::1/128'),            # IPv6 loopback
        ipaddress.ip_network('fe80::/10'),          # IPv6 link-local
        ipaddress.ip_network('fc00::/7'),           # IPv6 unique local
    ]

    # Cloud metadata endpoints
    BLOCKED_HOSTNAMES = [
        'localhost',
        'metadata.google.internal',
        'metadata',
        '169.254.169.254',  # AWS/Azure metadata
    ]

    # Allowed URL schemes
    ALLOWED_SCHEMES = ['http', 'https']

    def validate_url(self, url: str) -> bool:
        """
        Validate URL for SSRF safety

        Returns:
            True if URL is safe, False if blocked
        """
        try:
            parsed = urlparse(url)

            # Check URL scheme
            if parsed.scheme not in self.ALLOWED_SCHEMES:
                logger.warning(f"Blocked URL with invalid scheme: {parsed.scheme}")
                return False

            # Check for embedded credentials
            if parsed.username or parsed.password:
                logger.warning(f"Blocked URL with embedded credentials: {url}")
                return False

            hostname = parsed.hostname or parsed.netloc

            # Check hostname against blocklist
            if not self.validate_hostname(hostname):
                return False

            # Resolve hostname to IP and validate
            if not self._validate_resolved_ip(hostname):
                return False

            return True

        except Exception as e:
            logger.error(f"Error validating URL {url}: {e}")
            return False

    def validate_hostname(self, hostname: str) -> bool:
        """
        Validate hostname against blocklist

        Returns:
            True if hostname is safe, False if blocked
        """
        if not hostname:
            return False

        hostname_lower = hostname.lower()

        # Check against blocked hostnames
        for blocked in self.BLOCKED_HOSTNAMES:
            if hostname_lower == blocked or hostname_lower.endswith(f'.{blocked}'):
                logger.warning(f"Blocked hostname: {hostname}")
                return False

        # Check if hostname is an IP address
        try:
            ip = ipaddress.ip_address(hostname)
            if self._is_private_ip(ip):
                logger.warning(f"Blocked private IP in hostname: {hostname}")
                return False
        except ValueError:
            # Not an IP address, continue with DNS resolution
            pass

        return True

    def _is_unspecified_address(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        """
        Check if IP is an unspecified address (0.0.0.0 or ::)

        Returns:
            True if unspecified
        """
        return ip.is_unspecified

    def _validate_resolved_ip(self, hostname: str) -> bool:
        """
        Resolve hostname to IP and validate it's not private
        Prevents DNS rebinding attacks

        Returns:
            True if resolved IP is safe, False if blocked
        """
        try:
            # Resolve hostname to IP addresses
            addr_info = socket.getaddrinfo(hostname, None)

            for info in addr_info:
                ip_str = info[4][0]

                try:
                    ip = ipaddress.ip_address(ip_str)

                    if self._is_private_ip(ip):
                        logger.warning(
                            f"DNS rebinding attempt detected: {hostname} resolves to private IP {ip_str}"
                        )
                        return False

                except ValueError:
                    logger.error(f"Invalid IP address from DNS resolution: {ip_str}")
                    return False

            return True

        except socket.gaierror as e:
            logger.error(f"DNS resolution failed for {hostname}: {e}")
            return False

    def _is_private_ip(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        """
        Check if IP address is in private/blocked ranges

        Returns:
            True if IP is private/blocked, False if public
        """
        # Check for unspecified addresses (0.0.0.0, ::)
        if ip.is_unspecified:
            return True

        # Check against private ranges
        for network in self.PRIVATE_IP_RANGES:
            if ip in network:
                return True

        return False

    def fetch_url(self, url: str, timeout: int = 10) -> Optional[str]:
        """
        Safely fetch URL with SSRF protection

        Args:
            url: URL to fetch
            timeout: Request timeout in seconds

        Returns:
            Response content or None if blocked/failed
        """
        import requests

        if not self.validate_url(url):
            raise ValueError(f"URL blocked by SSRF protection: {url}")

        try:
            # Disable redirects to prevent redirect-based SSRF
            response = requests.get(
                url,
                timeout=timeout,
                allow_redirects=False,
                verify=True
            )

            response.raise_for_status()
            return response.text

        except requests.RequestException as e:
            logger.error(f"Error fetching URL {url}: {e}")
            raise
