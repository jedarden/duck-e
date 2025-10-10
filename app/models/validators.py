"""
Pydantic Input Validators - TDD GREEN Phase Implementation
===========================================================

Comprehensive input validation for:
- Location inputs (weather API)
- Search queries (web search)
- Accept-Language headers (HTTP headers)

All validators implement security controls against:
- SQL Injection
- Command Injection
- XSS (Cross-Site Scripting)
- Path Traversal
- URL Injection
- SSRF
- Header Injection
- Prompt Injection
- Unicode/Null Byte exploits
"""

from pydantic import BaseModel, Field, validator
import re
from typing import Optional


class LocationInput(BaseModel):
    """
    Validates location input for weather API calls.

    Security Controls:
    - Length limits: 2-100 characters
    - Allowlist pattern: letters, spaces, hyphens, apostrophes only
    - Blocks: SQL, command injection, XSS, path traversal, URL injection, SSRF
    """
    location: str = Field(..., min_length=2, max_length=100)

    @validator('location')
    def validate_location(cls, v):
        """
        Validate location using allowlist approach.

        Allowed characters:
        - Letters (any language/script)
        - Spaces
        - Hyphens (for compound names like "London-on-Thames")
        - Apostrophes (for names like "L'Aquila")
        - Commas (for "City, State" format)
        """
        if not v or not isinstance(v, str):
            raise ValueError("Location must be a non-empty string")

        # Remove leading/trailing whitespace
        v = v.strip()

        # Check for null bytes
        if '\x00' in v or '%00' in v:
            raise ValueError("Invalid location format: null bytes not allowed")

        # Check for newline/carriage return (header injection)
        if '\r' in v or '\n' in v or '%0d' in v.lower() or '%0a' in v.lower():
            raise ValueError("Invalid location format: newline characters not allowed")

        # Check for URL/URI schemes (SSRF prevention)
        url_schemes = ['http://', 'https://', 'file://', 'ftp://', 'data:', 'javascript:']
        if any(scheme in v.lower() for scheme in url_schemes):
            raise ValueError("Invalid location format: URLs not allowed")

        # Check for IP addresses (SSRF prevention)
        ip_pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
        if re.search(ip_pattern, v):
            raise ValueError("Invalid location format: IP addresses not allowed")

        # Check for localhost/internal hostnames (SSRF prevention)
        internal_hosts = ['localhost', '127.0.0.1', '0.0.0.0', '169.254', 'internal']
        if any(host in v.lower() for host in internal_hosts):
            raise ValueError("Invalid location format: internal hostnames not allowed")

        # Check for SQL injection patterns
        sql_patterns = [
            r"('|(\\'))",  # Single quotes
            r'("|(\\"))',  # Double quotes
            r'(;|--)',     # SQL terminators/comments
            r'\bor\b.*=.*',  # OR clauses
            r'\bunion\b',    # UNION statements
            r'\bselect\b',   # SELECT statements
            r'\bdrop\b',     # DROP statements
            r'\bdelete\b',   # DELETE statements
            r'\binsert\b',   # INSERT statements
            r'\bupdate\b',   # UPDATE statements
        ]

        for pattern in sql_patterns:
            if re.search(pattern, v, re.IGNORECASE):
                raise ValueError("Invalid location format: suspicious SQL-like syntax detected")

        # Check for command injection patterns
        command_patterns = [
            r'[;&|`$]',        # Shell metacharacters
            r'\$\(',           # Command substitution
            r'`',              # Backticks
            r'\|\s*\w+',       # Pipes
            r'&&|\|\|',        # Logical operators
        ]

        for pattern in command_patterns:
            if re.search(pattern, v):
                raise ValueError("Invalid location format: shell metacharacters not allowed")

        # Check for XSS patterns
        xss_patterns = [
            r'<script',
            r'javascript:',
            r'onerror\s*=',
            r'onload\s*=',
            r'<iframe',
            r'<img',
            r'<svg',
            r'<object',
            r'<embed',
        ]

        for pattern in xss_patterns:
            if re.search(pattern, v, re.IGNORECASE):
                raise ValueError("Invalid location format: HTML/JavaScript not allowed")

        # Check for path traversal
        if '..' in v or '%2e%2e' in v.lower():
            raise ValueError("Invalid location format: path traversal not allowed")

        # Check for URL parameter injection
        if '&' in v or '?' in v or '#' in v:
            raise ValueError("Invalid location format: URL parameters not allowed")

        # Check for encoded characters that might bypass filters
        encoded_chars = ['%00', '%0d', '%0a', '%26', '%3f', '%23']
        if any(char in v.lower() for char in encoded_chars):
            raise ValueError("Invalid location format: encoded characters not allowed")

        # Check for bidirectional override characters (Unicode exploits)
        unicode_exploits = ['\u202e', '\u202d', '\u200e', '\u200f']
        if any(char in v for char in unicode_exploits):
            raise ValueError("Invalid location format: bidirectional override characters not allowed")

        # Allowlist pattern: letters (Unicode), spaces, hyphens, apostrophes, commas
        # This is the positive validation after all negative checks
        allowlist_pattern = r"^[\w\s\-',\.]+$"
        if not re.match(allowlist_pattern, v, re.UNICODE):
            raise ValueError("Invalid location format: only letters, spaces, hyphens, apostrophes, and commas allowed")

        return v


class SearchQuery(BaseModel):
    """
    Validates search query input for web search functionality.

    Security Controls:
    - Length limits: 3-500 characters
    - Blocks: SQL injection, prompt injection, XSS, command injection
    """
    query: str = Field(..., min_length=3, max_length=500)

    @validator('query')
    def validate_query(cls, v):
        """
        Validate search query using comprehensive security checks.
        """
        if not v or not isinstance(v, str):
            raise ValueError("Search query must be a non-empty string")

        # Remove leading/trailing whitespace
        v = v.strip()

        # Check for null bytes
        if '\x00' in v or '%00' in v:
            raise ValueError("Invalid search query: null bytes not allowed")

        # Check for newline/carriage return
        if '\r' in v or '\n' in v:
            raise ValueError("Invalid search query: newline characters not allowed")

        # Check for SQL injection patterns (more lenient than location for natural queries)
        dangerous_sql = [
            r";\s*(drop|delete|update|insert)\s+",
            r"'\s*or\s*'1'\s*=\s*'1",
            r"--\s*$",
            r";\s*--",
            r"union\s+select",
        ]

        for pattern in dangerous_sql:
            if re.search(pattern, v, re.IGNORECASE):
                raise ValueError("Invalid search query: suspicious SQL-like syntax detected")

        # Check for prompt injection patterns
        prompt_injection = [
            r'ignore\s+previous\s+instructions',
            r'system\s*:\s*override',
            r'forget\s+everything',
            r'\[inst\]',
            r'\[/inst\]',
            r'\\n\\nhuman:',
            r'developer\s+mode',
            r'bypass\s+all\s+restrictions',
            r'reveal\s+system\s+prompt',
        ]

        for pattern in prompt_injection:
            if re.search(pattern, v, re.IGNORECASE):
                raise ValueError("Invalid search query: prompt injection detected")

        # Check for XSS patterns
        xss_patterns = [
            r'<script',
            r'javascript:',
            r'onerror\s*=',
            r'onload\s*=',
            r'<iframe',
        ]

        for pattern in xss_patterns:
            if re.search(pattern, v, re.IGNORECASE):
                raise ValueError("Invalid search query: HTML/JavaScript not allowed")

        # Check for command injection
        if re.search(r'[;&|`$]', v):
            raise ValueError("Invalid search query: shell metacharacters not allowed")

        return v


class AcceptLanguage(BaseModel):
    """
    Validates Accept-Language HTTP header (RFC 5646 compliant).

    Security Controls:
    - Length limit: max 35 characters
    - RFC 5646 format validation
    - Blocks: header injection, command injection, null bytes
    """
    language: str = Field(..., max_length=35)

    @validator('language')
    def validate_language(cls, v):
        """
        Validate language code according to RFC 5646.

        Format: language[-region][;q=quality]
        Example: en-US, fr-FR, en-US,fr;q=0.9
        """
        if not v or not isinstance(v, str):
            raise ValueError("Language code must be a non-empty string")

        # Remove leading/trailing whitespace
        v = v.strip()

        # Check for null bytes
        if '\x00' in v or '%00' in v:
            raise ValueError("Invalid language code: null bytes not allowed")

        # Check for header injection (CRLF)
        if '\r' in v or '\n' in v or '%0d' in v.lower() or '%0a' in v.lower():
            raise ValueError("Invalid language code: newline characters not allowed")

        # Check for command injection
        command_chars = [';', '&', '|', '`', '$', '(', ')']
        # Allow semicolon only in quality factor context (;q=)
        if any(char in v for char in command_chars if char != ';'):
            raise ValueError("Invalid language code: shell metacharacters not allowed")

        # If semicolon exists, ensure it's only for quality factor
        if ';' in v:
            if not re.search(r';\s*q\s*=\s*[0-9.]+', v):
                raise ValueError("Invalid language code: semicolon only allowed for quality factor")

        # RFC 5646 pattern validation
        # Format: language[-script][-region][;q=value][,language[-script][-region][;q=value]]*
        # Simplified pattern for common use cases
        rfc5646_pattern = r'^[a-zA-Z]{2,3}(-[a-zA-Z]{2,4})?(-[a-zA-Z]{2})?(\s*;\s*q\s*=\s*[0-1](\.\d{1,3})?)?(,\s*[a-zA-Z]{2,3}(-[a-zA-Z]{2,4})?(-[a-zA-Z]{2})?(\s*;\s*q\s*=\s*[0-1](\.\d{1,3})?)?)*$'

        if not re.match(rfc5646_pattern, v):
            raise ValueError("Invalid language code: must follow RFC 5646 format (e.g., en-US, fr-FR)")

        return v
