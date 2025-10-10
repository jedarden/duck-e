"""
Cache Control Middleware
OWASP API8: Security Misconfiguration - Cache Headers

Prevents:
- Sensitive data caching
- Browser caching of private information
- Proxy caching of user data
"""
import logging
from typing import List, Optional
from fastapi import Request, Response

logger = logging.getLogger(__name__)


class CacheControlMiddleware:
    """
    Add appropriate Cache-Control headers based on endpoint sensitivity
    """

    def __init__(
        self,
        app=None,
        sensitive_paths: Optional[List[str]] = None,
        public_paths: Optional[List[str]] = None
    ):
        """
        Initialize cache control middleware

        Args:
            app: FastAPI application
            sensitive_paths: Paths that should never be cached
            public_paths: Paths that can be publicly cached
        """
        self.app = app
        self.sensitive_paths = sensitive_paths or [
            '/api/user',
            '/api/auth',
            '/api/account',
            '/api/payment',
            '/session',
        ]
        self.public_paths = public_paths or [
            '/api/public',
            '/static',
            '/status',
        ]

    def is_sensitive_path(self, path: str) -> bool:
        """
        Check if path contains sensitive data

        Args:
            path: Request path

        Returns:
            True if path is sensitive
        """
        for sensitive in self.sensitive_paths:
            if path.startswith(sensitive):
                return True
        return False

    def is_public_path(self, path: str) -> bool:
        """
        Check if path is public (cacheable)

        Args:
            path: Request path

        Returns:
            True if path is public
        """
        for public in self.public_paths:
            if path.startswith(public):
                return True
        return False

    async def add_cache_headers(
        self,
        response: Response,
        path: str
    ) -> None:
        """
        Add appropriate cache headers to response

        Args:
            response: Response object
            path: Request path
        """
        if self.is_sensitive_path(path):
            # Sensitive paths: no caching at all
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, private, max-age=0"
            )
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"

        elif self.is_public_path(path):
            # Public paths: allow caching
            response.headers["Cache-Control"] = "public, max-age=3600"  # 1 hour

        else:
            # Default: private caching only
            response.headers["Cache-Control"] = "private, max-age=300"  # 5 minutes

    async def __call__(self, request: Request, call_next):
        """
        Middleware handler
        """
        response = await call_next(request)

        # Add cache control headers
        await self.add_cache_headers(response, request.url.path)

        return response
