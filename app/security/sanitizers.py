"""
Input Sanitization Functions - TDD GREEN Phase
==============================================

Sanitization utilities for:
- URL parameter encoding
- API response sanitization
- HTML/script tag removal
"""

import re
import html
from urllib.parse import quote_plus, quote
from typing import Any, Dict, Union


def sanitize_url_parameter(value: str) -> str:
    """
    Sanitize a string for safe use in URL parameters.

    Args:
        value: Raw string value to encode

    Returns:
        URL-safe encoded string

    Security:
        - Prevents parameter injection via &, ?, #
        - URL encodes all special characters
        - Prevents path traversal via ../
    """
    if not value:
        return ""

    # Use quote_plus to encode special characters and convert spaces to +
    # This prevents parameter injection and ensures safe URL usage
    encoded = quote_plus(value, safe='')

    return encoded


def sanitize_api_response(response: Union[Dict[str, Any], str]) -> Union[Dict[str, Any], str]:
    """
    Sanitize API response data to remove potential XSS and injection attempts.

    Args:
        response: API response (dict or string)

    Returns:
        Sanitized response with dangerous content removed

    Security:
        - Removes script tags
        - Escapes HTML entities
        - Removes SQL injection patterns
        - Removes event handlers (onerror, onload, etc.)
    """
    if isinstance(response, str):
        return _sanitize_string(response)
    elif isinstance(response, dict):
        return {k: sanitize_api_response(v) for k, v in response.items()}
    elif isinstance(response, list):
        return [sanitize_api_response(item) for item in response]
    else:
        return response


def _sanitize_string(value: str) -> str:
    """
    Sanitize a string value.

    Args:
        value: String to sanitize

    Returns:
        Sanitized string
    """
    if not isinstance(value, str):
        return value

    # Remove script tags
    value = re.sub(r'<script[^>]*>.*?</script>', '', value, flags=re.IGNORECASE | re.DOTALL)

    # Remove iframe tags
    value = re.sub(r'<iframe[^>]*>.*?</iframe>', '', value, flags=re.IGNORECASE | re.DOTALL)

    # Remove object/embed tags
    value = re.sub(r'<(object|embed)[^>]*>.*?</\1>', '', value, flags=re.IGNORECASE | re.DOTALL)

    # Remove event handlers
    value = re.sub(r'\s*on\w+\s*=\s*["\'][^"\']*["\']', '', value, flags=re.IGNORECASE)
    value = re.sub(r'\s*on\w+\s*=\s*\S+', '', value, flags=re.IGNORECASE)

    # Remove javascript: protocol
    value = re.sub(r'javascript:', '', value, flags=re.IGNORECASE)

    # Remove SQL injection patterns (aggressive)
    sql_patterns = [
        r"'\s*or\s*'1'\s*=\s*'1",
        r';\s*drop\s+table',
        r';\s*delete\s+from',
        r'union\s+select',
        r'--\s*$',
    ]
    for pattern in sql_patterns:
        value = re.sub(pattern, '', value, flags=re.IGNORECASE)

    # Escape remaining HTML entities for safety
    value = html.escape(value, quote=False)

    return value


def validate_url_safe(url: str, allowed_domains: list = None) -> bool:
    """
    Validate that a URL is safe to request (SSRF prevention).

    Args:
        url: URL to validate
        allowed_domains: List of allowed domain names (optional)

    Returns:
        True if URL is safe, False otherwise

    Security:
        - Prevents requests to localhost/internal IPs
        - Prevents file:// and other dangerous protocols
        - Optionally restricts to allowlist of domains
    """
    if not url or not isinstance(url, str):
        return False

    # Convert to lowercase for comparison
    url_lower = url.lower()

    # Block dangerous protocols
    dangerous_protocols = ['file://', 'gopher://', 'data:', 'ftp://', 'jar:', 'dict://']
    if any(proto in url_lower for proto in dangerous_protocols):
        return False

    # Block localhost and internal IPs
    internal_hosts = [
        'localhost',
        '127.0.0.1',
        '0.0.0.0',
        '::1',
        '169.254',  # Link-local
        '10.',      # Private network
        '192.168',  # Private network
        '172.16',   # Private network (172.16-172.31)
    ]

    for host in internal_hosts:
        if host in url_lower:
            return False

    # If allowlist provided, check domain
    if allowed_domains:
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()

            # Check if domain matches any allowed domain
            if not any(allowed in domain for allowed in allowed_domains):
                return False
        except Exception:
            return False

    return True


def sanitize_header_value(value: str, max_length: int = 100) -> str:
    """
    Sanitize HTTP header value to prevent header injection.

    Args:
        value: Header value to sanitize
        max_length: Maximum allowed length

    Returns:
        Sanitized header value

    Security:
        - Removes CRLF characters
        - Removes null bytes
        - Enforces length limit
    """
    if not value or not isinstance(value, str):
        return ""

    # Truncate to max length
    value = value[:max_length]

    # Remove CRLF
    value = value.replace('\r', '').replace('\n', '')
    value = value.replace('%0d', '').replace('%0a', '')
    value = value.replace('%0D', '').replace('%0A', '')

    # Remove null bytes
    value = value.replace('\x00', '').replace('%00', '')

    return value.strip()
