"""
API Versioning Middleware
OWASP API9: Improper Inventory Management

Enforces:
- API version headers
- Deprecated version handling (410 Gone)
- Unsupported version rejection
- API inventory management
"""
import logging
from typing import List, Optional
from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class APIVersionMiddleware:
    """
    Enforce API versioning and handle deprecated versions
    """

    def __init__(
        self,
        app,
        supported_versions: Optional[List[str]] = None,
        deprecated_versions: Optional[List[str]] = None,
        require_version: bool = True,
        version_header: str = "X-API-Version"
    ):
        """
        Initialize versioning middleware

        Args:
            app: FastAPI application
            supported_versions: List of supported API versions (e.g., ["v3", "v4"])
            deprecated_versions: List of deprecated versions (e.g., ["v1", "v2"])
            require_version: Whether to require version header
            version_header: Name of the version header
        """
        self.app = app
        self.supported_versions = supported_versions or ["v1"]
        self.deprecated_versions = deprecated_versions or []
        self.require_version = require_version
        self.version_header = version_header

    def validate_version(self, request: Request) -> Optional[JSONResponse]:
        """
        Validate API version from request

        Returns:
            Error response if invalid, None if valid
        """
        version = request.headers.get(self.version_header)

        # Check if version is required
        if self.require_version and not version:
            logger.warning(
                f"Request rejected: missing {self.version_header} header from "
                f"{request.client.host if request.client else 'unknown'}"
            )
            return JSONResponse(
                status_code=400,
                content={
                    "error": "Bad Request",
                    "message": f"{self.version_header} header is required",
                    "supported_versions": self.supported_versions
                }
            )

        if not version:
            return None

        # Check if version is deprecated
        if version in self.deprecated_versions:
            logger.info(
                f"Deprecated API version '{version}' requested from "
                f"{request.client.host if request.client else 'unknown'} for {request.url.path}"
            )
            return JSONResponse(
                status_code=410,
                content={
                    "error": "Gone",
                    "message": f"API version '{version}' is deprecated and no longer supported",
                    "deprecated_version": version,
                    "supported_versions": self.supported_versions,
                    "migration_guide": "https://docs.example.com/api/migration"
                }
            )

        # Check if version is supported
        if version not in self.supported_versions:
            logger.warning(
                f"Unsupported API version '{version}' requested from "
                f"{request.client.host if request.client else 'unknown'}"
            )
            return JSONResponse(
                status_code=400,
                content={
                    "error": "Bad Request",
                    "message": f"API version '{version}' is not supported",
                    "requested_version": version,
                    "supported_versions": self.supported_versions
                }
            )

        # Version is valid
        return None

    async def __call__(self, request: Request, call_next):
        """
        Middleware handler
        """
        # Validate version
        version_error = self.validate_version(request)

        if version_error:
            return version_error

        response = await call_next(request)

        # Add version info to response headers
        version = request.headers.get(self.version_header)
        if version:
            response.headers["X-API-Version"] = version

        return response
