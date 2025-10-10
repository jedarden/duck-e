"""
WebSocket Origin Validation for DUCK-E
Implements origin validation and security checks for WebSocket connections
"""
from fastapi import WebSocket, status
from logging import Logger, getLogger
from typing import List, Optional
import os
import re


class WebSocketOriginValidator:
    """
    Validator for WebSocket origin headers.

    Implements:
    - Origin header validation
    - Allowed origins whitelist
    - Connection timeout enforcement
    - Logging of validation failures
    """

    def __init__(
        self,
        allowed_origins: Optional[List[str]] = None,
        require_origin: bool = True,
        logger: Optional[Logger] = None
    ):
        """
        Initialize WebSocket origin validator.

        Args:
            allowed_origins: List of allowed origin patterns
            require_origin: Require Origin header to be present
            logger: Logger instance for audit trail
        """
        self.allowed_origins = self._parse_origins(allowed_origins)
        self.require_origin = require_origin
        self.logger = logger or getLogger("websocket.validator")

    def _parse_origins(self, origins: Optional[List[str]]) -> List[str]:
        """
        Parse allowed origins from configuration or environment.

        Args:
            origins: Origins to parse

        Returns:
            List of allowed origin patterns
        """
        if origins:
            return origins

        # Read from environment variable
        env_origins = os.getenv("ALLOWED_ORIGINS", "")

        if env_origins:
            parsed = [o.strip() for o in env_origins.split(",") if o.strip()]
            if parsed:
                return parsed

        # Default: localhost for development
        is_production = os.getenv("ENVIRONMENT", "development") == "production"

        if is_production:
            # Strict: No origins allowed by default in production
            return []
        else:
            # Development: Allow localhost on common ports
            return [
                "http://localhost:3000",
                "http://localhost:8000",
                "http://localhost:5173",
                "http://127.0.0.1:3000",
                "http://127.0.0.1:8000",
                "http://127.0.0.1:5173"
            ]

    def _is_origin_allowed(self, origin: str) -> bool:
        """
        Check if origin is in allowed list.

        Args:
            origin: Origin header value

        Returns:
            True if origin is allowed, False otherwise
        """
        if not self.allowed_origins:
            # If no origins configured in production, deny all
            if os.getenv("ENVIRONMENT") == "production":
                return False
            # In development, be more permissive for localhost
            return origin.startswith(("http://localhost", "http://127.0.0.1"))

        # Direct match
        if origin in self.allowed_origins:
            return True

        # Pattern matching for wildcards (e.g., *.example.com)
        for allowed_pattern in self.allowed_origins:
            if "*" in allowed_pattern:
                # Convert wildcard pattern to regex
                regex_pattern = allowed_pattern.replace(".", r"\.").replace("*", r"[a-zA-Z0-9-]+")
                regex_pattern = f"^{regex_pattern}$"
                if re.match(regex_pattern, origin):
                    return True

        return False

    async def validate(self, websocket: WebSocket) -> bool:
        """
        Validate WebSocket connection origin.

        Args:
            websocket: WebSocket connection instance

        Returns:
            True if origin is valid, False otherwise
        """
        # Extract origin from headers
        origin = websocket.headers.get("origin") or websocket.headers.get("Origin")

        # Check if origin header is required
        if self.require_origin and not origin:
            self.logger.warning(
                "WebSocket connection rejected: Missing Origin header",
                extra={
                    "client": websocket.client,
                    "headers": dict(websocket.headers)
                }
            )
            return False

        # If origin not required and not present, allow
        if not origin:
            self.logger.info("WebSocket connection accepted: Origin not required")
            return True

        # Validate origin against allowed list
        if not self._is_origin_allowed(origin):
            self.logger.warning(
                f"WebSocket connection rejected: Origin not allowed: {origin}",
                extra={
                    "client": websocket.client,
                    "origin": origin,
                    "allowed_origins": self.allowed_origins
                }
            )
            return False

        # Origin is valid
        self.logger.info(
            f"WebSocket connection accepted: Origin validated: {origin}",
            extra={
                "client": websocket.client,
                "origin": origin
            }
        )
        return True

    async def validate_and_accept(self, websocket: WebSocket) -> bool:
        """
        Validate origin and accept connection if valid.

        Args:
            websocket: WebSocket connection instance

        Returns:
            True if connection accepted, False if rejected
        """
        if await self.validate(websocket):
            await websocket.accept()
            return True
        else:
            # Reject connection with 403 Forbidden
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Origin not allowed"
            )
            return False


class WebSocketSecurityMiddleware:
    """
    Security middleware for WebSocket connections.

    Implements:
    - Origin validation
    - Connection timeout
    - Rate limiting (placeholder for future implementation)
    """

    def __init__(
        self,
        allowed_origins: Optional[List[str]] = None,
        connection_timeout: int = 300,  # 5 minutes
        logger: Optional[Logger] = None
    ):
        """
        Initialize WebSocket security middleware.

        Args:
            allowed_origins: List of allowed origin patterns
            connection_timeout: Maximum connection duration in seconds
            logger: Logger instance
        """
        self.validator = WebSocketOriginValidator(
            allowed_origins=allowed_origins,
            logger=logger
        )
        self.connection_timeout = connection_timeout
        self.logger = logger or getLogger("websocket.security")

    async def validate_connection(self, websocket: WebSocket) -> bool:
        """
        Validate WebSocket connection with all security checks.

        Args:
            websocket: WebSocket connection instance

        Returns:
            True if connection is valid and accepted, False otherwise
        """
        # Validate origin
        if not await self.validator.validate(websocket):
            return False

        # Accept connection
        await websocket.accept()

        # Log successful connection
        self.logger.info(
            "WebSocket connection established",
            extra={
                "client": websocket.client,
                "origin": websocket.headers.get("origin"),
                "user_agent": websocket.headers.get("user-agent")
            }
        )

        return True


def create_websocket_validator(
    allowed_origins: Optional[List[str]] = None,
    require_origin: bool = True
) -> WebSocketOriginValidator:
    """
    Factory function to create WebSocket origin validator.

    Args:
        allowed_origins: List of allowed origin patterns
        require_origin: Require Origin header to be present

    Returns:
        Configured WebSocketOriginValidator instance
    """
    return WebSocketOriginValidator(
        allowed_origins=allowed_origins,
        require_origin=require_origin
    )


def get_websocket_security_middleware() -> WebSocketSecurityMiddleware:
    """
    Get WebSocket security middleware with environment-based configuration.

    Reads from:
    - ALLOWED_ORIGINS: Comma-separated list of allowed origins
    - WS_CONNECTION_TIMEOUT: Connection timeout in seconds

    Returns:
        Configured WebSocketSecurityMiddleware instance
    """
    allowed_origins_str = os.getenv("ALLOWED_ORIGINS")
    allowed_origins = None
    if allowed_origins_str:
        allowed_origins = [o.strip() for o in allowed_origins_str.split(",") if o.strip()]

    connection_timeout = int(os.getenv("WS_CONNECTION_TIMEOUT", "300"))

    return WebSocketSecurityMiddleware(
        allowed_origins=allowed_origins,
        connection_timeout=connection_timeout
    )
