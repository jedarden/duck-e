"""
Safe Error Handler
OWASP API8: Security Misconfiguration - Error Handling

Prevents:
- Stack trace exposure
- File path leakage
- Sensitive data in error messages
- Internal implementation details exposure
"""
import re
import logging
import traceback
from typing import Dict, Any
from fastapi import Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class SafeErrorHandler:
    """
    Sanitize error responses to prevent information disclosure
    """

    # Patterns to redact from error messages (pattern, replacement)
    SENSITIVE_PATTERNS = [
        (r'/[\w/]+\.py', '[REDACTED_FILE]'),                    # File paths
        (r'line \d+', '[REDACTED_LINE]'),                       # Line numbers
        (r'sk-[a-zA-Z0-9-]{20,}', '[REDACTED_SK_KEY]'),  # OpenAI style keys (match shorter keys too)
        (r'(?i)(password|passwd|pwd)[\s:=]+[\w\S]+', '[REDACTED_PASSWORD]'),  # Passwords
        (r'(?i)(api[_-]?key|apikey|key)[\s:=]+[\w-]+', '[REDACTED_API_KEY]'),   # API keys
        (r'(?i)(token|bearer)[\s:=]+[\w.-]+', '[REDACTED_TOKEN]'),  # Tokens
        (r'(?i)(secret|secret[_-]?key)[\s:=]+[\w-]+', '[REDACTED_SECRET]'),  # Secrets
        (r'SELECT .+ FROM', '[REDACTED_SQL]'),                 # SQL queries
        (r'UPDATE .+ SET', '[REDACTED_SQL]'),                  # SQL updates
        (r'DELETE FROM', '[REDACTED_SQL]'),                    # SQL deletes
        (r'INSERT INTO', '[REDACTED_SQL]'),                    # SQL inserts
        (r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', '[REDACTED_CARD]'),  # Credit card
        (r'\b\d{3}-\d{2}-\d{4}\b', '[REDACTED_SSN]'),  # SSN
    ]

    # Generic error messages by status code
    GENERIC_MESSAGES = {
        400: "Bad request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not found",
        405: "Method not allowed",
        413: "Payload too large",
        415: "Unsupported media type",
        429: "Too many requests",
        500: "Internal server error",
        502: "Bad gateway",
        503: "Service unavailable",
    }

    def __init__(self, debug_mode: bool = False):
        """
        Initialize error handler

        Args:
            debug_mode: If True, include more details (dev only)
        """
        self.debug_mode = debug_mode

    async def handle_error(
        self,
        error: Exception,
        status_code: int = 500,
        request: Request = None
    ) -> JSONResponse:
        """
        Handle error and return sanitized response

        Args:
            error: The exception that occurred
            status_code: HTTP status code
            request: The request that caused the error

        Returns:
            Sanitized JSON error response
        """
        # Log the actual error with full details (secure logs only)
        self._log_error_securely(error, status_code, request)

        # Generate safe error response
        error_response = self._create_safe_response(error, status_code)

        return JSONResponse(
            status_code=status_code,
            content=error_response
        )

    def _create_safe_response(self, error: Exception, status_code: int) -> Dict[str, Any]:
        """
        Create sanitized error response

        Returns:
            Safe error response dictionary
        """
        response = {
            "error": True,
            "status_code": status_code,
            "message": self.GENERIC_MESSAGES.get(status_code, "An error occurred"),
        }

        # In debug mode (dev only), include sanitized error details
        if self.debug_mode:
            sanitized_message = self._sanitize_message(str(error))
            response["debug_message"] = sanitized_message

        return response

    def _sanitize_message(self, message: str) -> str:
        """
        Remove sensitive information from error message

        Args:
            message: Original error message

        Returns:
            Sanitized error message
        """
        sanitized = message

        # Apply all redaction patterns
        for pattern, replacement in self.SENSITIVE_PATTERNS:
            sanitized = re.sub(pattern, replacement, sanitized)

        return sanitized

    def _log_error_securely(
        self,
        error: Exception,
        status_code: int,
        request: Request = None
    ) -> None:
        """
        Log error with full details to secure logs
        Redacts sensitive data before logging

        Args:
            error: The exception
            status_code: HTTP status code
            request: The originating request
        """
        # Sanitize error message before logging
        error_message = self._sanitize_message(str(error))

        log_data = {
            "status_code": status_code,
            "error_type": type(error).__name__,
            "error_message": error_message,
        }

        if request:
            log_data.update({
                "method": request.method,
                "path": request.url.path,
                "client_ip": self._get_client_ip(request),
            })

        # Convert to string and sanitize again (defense in depth)
        log_string = str(log_data)
        sanitized_log = self._sanitize_message(log_string)

        # Log at appropriate level
        if status_code >= 500:
            logger.error(f"Server error: {sanitized_log}")
        elif status_code >= 400:
            logger.warning(f"Client error: {sanitized_log}")
        else:
            logger.info(f"Error handled: {sanitized_log}")

        # In debug mode, log stack trace (sanitized)
        if self.debug_mode and status_code >= 500:
            stack_trace = traceback.format_exc()
            sanitized_trace = self._sanitize_message(stack_trace)
            logger.debug(f"Stack trace: {sanitized_trace}")

    def _get_client_ip(self, request: Request) -> str:
        """
        Get client IP from request (handles proxies)

        Returns:
            Client IP address (sanitized)
        """
        # Check for forwarded IP
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            # Take first IP (client)
            return forwarded.split(",")[0].strip()

        # Fall back to direct connection
        client_host = request.client.host if request.client else "unknown"

        # Sanitize internal IPs
        if client_host.startswith("10.") or client_host.startswith("172.") or client_host.startswith("192.168."):
            return "[INTERNAL]"

        return client_host


class GlobalExceptionHandler:
    """
    Global exception handler middleware for FastAPI
    """

    def __init__(self, app, debug_mode: bool = False):
        self.app = app
        self.error_handler = SafeErrorHandler(debug_mode=debug_mode)

    async def __call__(self, request: Request, call_next):
        """
        Middleware handler
        """
        try:
            response = await call_next(request)
            return response

        except ValueError as e:
            return await self.error_handler.handle_error(e, 400, request)

        except PermissionError as e:
            return await self.error_handler.handle_error(e, 403, request)

        except FileNotFoundError as e:
            return await self.error_handler.handle_error(e, 404, request)

        except Exception as e:
            return await self.error_handler.handle_error(e, 500, request)
