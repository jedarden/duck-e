"""
Content-Type Validation Middleware
OWASP API8: Security Misconfiguration

Prevents:
- Missing Content-Type headers
- Invalid Content-Type headers
- Header injection attacks
- MIME type confusion attacks
"""
import logging
from typing import List, Optional
from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ContentTypeValidator:
    """
    Validate Content-Type headers on incoming requests
    """

    # Default allowed content types
    DEFAULT_ALLOWED_TYPES = [
        "application/json",
        "application/x-www-form-urlencoded",
        "multipart/form-data",
        "text/plain",
    ]

    def __init__(self, allowed_types: Optional[List[str]] = None):
        """
        Initialize validator

        Args:
            allowed_types: List of allowed Content-Type values
        """
        self.allowed_types = allowed_types or self.DEFAULT_ALLOWED_TYPES

    async def validate(self, request: Request) -> Optional[JSONResponse]:
        """
        Validate request Content-Type

        Returns:
            Error response if invalid, None if valid
        """
        # Only validate for methods that typically have a body
        if request.method not in ["POST", "PUT", "PATCH"]:
            return None

        content_type = request.headers.get("content-type")

        # Require Content-Type for body requests
        if not content_type:
            logger.warning(
                f"Request rejected: missing Content-Type header from "
                f"{request.client.host if request.client else 'unknown'}"
            )
            return JSONResponse(
                status_code=415,
                content={
                    "error": "Unsupported Media Type",
                    "message": "Content-Type header is required",
                    "allowed_types": self.allowed_types
                }
            )

        # Check for header injection (newline characters)
        if '\r' in content_type or '\n' in content_type:
            logger.error(
                f"Header injection attempt detected in Content-Type: {content_type}"
            )
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid Content-Type header"}
            )

        # Extract base content type (ignore parameters like charset)
        base_type = content_type.split(';')[0].strip().lower()

        # Validate against allowed types
        if base_type not in [t.lower() for t in self.allowed_types]:
            logger.warning(
                f"Request rejected: unsupported Content-Type '{base_type}' from "
                f"{request.client.host if request.client else 'unknown'}"
            )
            return JSONResponse(
                status_code=415,
                content={
                    "error": "Unsupported Media Type",
                    "message": f"Content-Type '{base_type}' is not allowed",
                    "allowed_types": self.allowed_types
                }
            )

        return None


class ContentTypeMiddleware:
    """
    Middleware wrapper for Content-Type validation
    """

    def __init__(self, app, allowed_types: Optional[List[str]] = None):
        self.app = app
        self.validator = ContentTypeValidator(allowed_types)

    async def __call__(self, request: Request, call_next):
        """
        Middleware handler
        """
        # Validate Content-Type
        validation_error = await self.validator.validate(request)

        if validation_error:
            return validation_error

        response = await call_next(request)
        return response
