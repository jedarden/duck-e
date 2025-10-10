"""
Security Logging Middleware
OWASP API8: Security Misconfiguration - Logging

Logs:
- Security events (SSRF, rate limits, auth failures)
- Request/response metadata
- Failed authentication attempts

Redacts:
- Passwords
- API keys
- Tokens
- Credit card numbers
- Social security numbers
"""
import re
import logging
from typing import Dict, Any, Optional
from fastapi import Request

logger = logging.getLogger(__name__)


class SecurityLogger:
    """
    Secure logging with automatic sensitive data redaction
    """

    # Patterns for sensitive data to redact
    SENSITIVE_PATTERNS = [
        (r'(?i)(password|passwd|pwd)[\s:=]+[\w\S]+', '[REDACTED PASSWORD]'),
        (r'(?i)(api[_-]?key|apikey)[\s:=]+[\w-]+', '[REDACTED API_KEY]'),
        (r'(?i)(token|bearer)[\s:=]+[\w.-]+', '[REDACTED TOKEN]'),
        (r'(?i)(secret|secret[_-]?key)[\s:=]+[\w-]+', '[REDACTED SECRET]'),
        (r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', '[REDACTED CARD]'),  # Credit card
        (r'\b\d{3}-\d{2}-\d{4}\b', '[REDACTED SSN]'),  # SSN
        (r'sk-[a-zA-Z0-9]{32,}', '[REDACTED SK_KEY]'),  # OpenAI style keys
    ]

    # Headers to always redact
    SENSITIVE_HEADERS = [
        'authorization',
        'x-api-key',
        'cookie',
        'set-cookie',
    ]

    def _redact_sensitive_data(self, data: str) -> str:
        """
        Redact sensitive data from string

        Args:
            data: String that might contain sensitive data

        Returns:
            String with sensitive data redacted
        """
        redacted = data

        for pattern, replacement in self.SENSITIVE_PATTERNS:
            redacted = re.sub(pattern, replacement, redacted)

        return redacted

    def _redact_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        """
        Redact sensitive headers

        Args:
            headers: Request/response headers

        Returns:
            Headers with sensitive values redacted
        """
        redacted = {}

        for key, value in headers.items():
            if key.lower() in self.SENSITIVE_HEADERS:
                redacted[key] = '[REDACTED]'
            else:
                redacted[key] = self._redact_sensitive_data(str(value))

        return redacted

    async def log_security_event(
        self,
        event_type: str,
        ip_address: str,
        endpoint: str,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log security event

        Args:
            event_type: Type of security event
            ip_address: Client IP address
            endpoint: Endpoint that triggered event
            details: Additional event details
        """
        log_data = {
            "event_type": event_type,
            "ip_address": ip_address,
            "endpoint": endpoint,
            "details": details or {}
        }

        logger.warning(f"Security event: {log_data}")

    async def log_request(
        self,
        path: str,
        headers: Dict[str, str],
        body: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log request with sensitive data redaction

        Args:
            path: Request path
            headers: Request headers
            body: Request body (optional)
        """
        redacted_headers = self._redact_headers(headers)

        redacted_body = None
        if body:
            body_str = str(body)
            redacted_body = self._redact_sensitive_data(body_str)

        log_data = {
            "path": path,
            "headers": redacted_headers,
            "body": redacted_body
        }

        logger.info(f"Request: {log_data}")

    async def log_auth_failure(
        self,
        ip_address: str,
        username: Optional[str],
        reason: str
    ) -> None:
        """
        Log failed authentication attempt

        Args:
            ip_address: Client IP address
            username: Attempted username
            reason: Failure reason
        """
        log_data = {
            "event": "AUTH_FAILURE",
            "ip_address": ip_address,
            "username": username,
            "reason": reason
        }

        logger.warning(f"Authentication failure: {log_data}")


class SecurityLoggingMiddleware:
    """
    Middleware for automatic security logging
    """

    def __init__(self, app):
        self.app = app
        self.logger = SecurityLogger()

    async def __call__(self, request: Request, call_next):
        """
        Middleware handler
        """
        # Log request (excluding sensitive data)
        await self.logger.log_request(
            path=request.url.path,
            headers=dict(request.headers)
        )

        response = await call_next(request)

        return response
