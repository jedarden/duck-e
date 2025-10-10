"""
CORS Configuration for DUCK-E
Implements secure CORS policy for public-facing application
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Union
import os
import re


class CORSConfig:
    """
    CORS configuration manager for DUCK-E application.

    Supports:
    - Environment-based origin configuration
    - Wildcard subdomain support
    - Localhost development support
    - Strict production defaults
    """

    def __init__(
        self,
        allowed_origins: Union[List[str], str] = None,
        allow_credentials: bool = True,
        allow_methods: List[str] = None,
        allow_headers: List[str] = None,
        expose_headers: List[str] = None,
        max_age: int = 600
    ):
        """
        Initialize CORS configuration.

        Args:
            allowed_origins: List of allowed origins or "*" for all (not recommended in production)
            allow_credentials: Allow cookies and authentication headers
            allow_methods: Allowed HTTP methods
            allow_headers: Allowed HTTP headers
            expose_headers: Headers exposed to browser
            max_age: Preflight cache duration in seconds
        """
        self.allowed_origins = self._parse_origins(allowed_origins)
        self.allow_credentials = allow_credentials
        self.allow_methods = allow_methods or ["GET", "POST", "OPTIONS"]
        self.allow_headers = allow_headers or ["*"]
        self.expose_headers = expose_headers or []
        self.max_age = max_age

    def _parse_origins(self, origins: Union[List[str], str, None]) -> List[str]:
        """
        Parse allowed origins from configuration or environment.

        Args:
            origins: Origins to parse

        Returns:
            List of allowed origin patterns
        """
        # If origins provided directly, use them
        if origins:
            if isinstance(origins, str):
                if origins == "*":
                    return ["*"]
                return [o.strip() for o in origins.split(",") if o.strip()]
            return origins

        # Read from environment variable
        env_origins = os.getenv("ALLOWED_ORIGINS", "")

        if env_origins:
            # Parse comma-separated origins
            parsed = [o.strip() for o in env_origins.split(",") if o.strip()]
            if parsed:
                return parsed

        # Default: localhost for development
        # In production, you MUST set ALLOWED_ORIGINS environment variable
        is_production = os.getenv("ENVIRONMENT", "development") == "production"

        if is_production:
            # Strict: No origins allowed by default in production
            # This forces explicit configuration
            return []
        else:
            # Development: Allow localhost on common ports
            return [
                "http://localhost:3000",
                "http://localhost:8000",
                "http://localhost:5173",  # Vite default
                "http://127.0.0.1:3000",
                "http://127.0.0.1:8000",
                "http://127.0.0.1:5173"
            ]

    def is_origin_allowed(self, origin: str) -> bool:
        """
        Check if an origin is allowed.

        Args:
            origin: Origin to check

        Returns:
            True if origin is allowed, False otherwise
        """
        if "*" in self.allowed_origins:
            return True

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

    def get_middleware_kwargs(self) -> dict:
        """
        Get kwargs for FastAPI CORSMiddleware.

        Returns:
            Dictionary of middleware configuration
        """
        return {
            "allow_origins": self.allowed_origins,
            "allow_credentials": self.allow_credentials,
            "allow_methods": self.allow_methods,
            "allow_headers": self.allow_headers,
            "expose_headers": self.expose_headers,
            "max_age": self.max_age
        }


def configure_cors(
    app: FastAPI,
    allowed_origins: Union[List[str], str] = None,
    allow_credentials: bool = True,
    allow_methods: List[str] = None,
    allow_headers: List[str] = None
) -> None:
    """
    Configure CORS middleware for FastAPI application.

    Args:
        app: FastAPI application instance
        allowed_origins: List of allowed origins or "*" for all
        allow_credentials: Allow cookies and authentication
        allow_methods: Allowed HTTP methods
        allow_headers: Allowed HTTP headers

    Example:
        >>> from fastapi import FastAPI
        >>> app = FastAPI()
        >>> configure_cors(app, allowed_origins=["https://example.com"])
    """
    config = CORSConfig(
        allowed_origins=allowed_origins,
        allow_credentials=allow_credentials,
        allow_methods=allow_methods,
        allow_headers=allow_headers
    )

    app.add_middleware(
        CORSMiddleware,
        **config.get_middleware_kwargs()
    )


def get_cors_config() -> CORSConfig:
    """
    Get CORS configuration from environment variables.

    Reads from:
    - ALLOWED_ORIGINS: Comma-separated list of allowed origins
    - ALLOW_CREDENTIALS: "true" or "false"
    - CORS_MAX_AGE: Preflight cache duration in seconds

    Returns:
        Configured CORSConfig instance
    """
    allowed_origins = os.getenv("ALLOWED_ORIGINS")
    allow_credentials = os.getenv("ALLOW_CREDENTIALS", "true").lower() == "true"
    max_age = int(os.getenv("CORS_MAX_AGE", "600"))

    return CORSConfig(
        allowed_origins=allowed_origins,
        allow_credentials=allow_credentials,
        max_age=max_age
    )
