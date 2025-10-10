"""
Security Headers Middleware for DUCK-E
Implements OWASP recommended security headers for public-facing applications
"""
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from typing import Callable
import os


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware to add security headers to all HTTP responses.

    Implements OWASP recommended headers:
    - Strict-Transport-Security (HSTS)
    - X-Content-Type-Options
    - X-Frame-Options
    - X-XSS-Protection
    - Content-Security-Policy
    - Permissions-Policy
    - Referrer-Policy
    """

    def __init__(
        self,
        app: ASGIApp,
        enable_hsts: bool = True,
        hsts_max_age: int = 31536000,  # 1 year in seconds
        hsts_include_subdomains: bool = True,
        hsts_preload: bool = False,
        csp_report_uri: str = None,
        custom_csp: str = None
    ):
        """
        Initialize security headers middleware.

        Args:
            app: FastAPI application instance
            enable_hsts: Enable HSTS header (default: True)
            hsts_max_age: HSTS max-age in seconds (default: 1 year)
            hsts_include_subdomains: Include subdomains in HSTS (default: True)
            hsts_preload: Enable HSTS preload (default: False)
            csp_report_uri: CSP report URI for violation reporting
            custom_csp: Custom CSP policy (overrides default)
        """
        super().__init__(app)
        self.enable_hsts = enable_hsts
        self.hsts_max_age = hsts_max_age
        self.hsts_include_subdomains = hsts_include_subdomains
        self.hsts_preload = hsts_preload
        self.csp_report_uri = csp_report_uri
        self.custom_csp = custom_csp

    def _build_hsts_header(self) -> str:
        """Build HSTS header value."""
        hsts = f"max-age={self.hsts_max_age}"
        if self.hsts_include_subdomains:
            hsts += "; includeSubDomains"
        if self.hsts_preload:
            hsts += "; preload"
        return hsts

    def _build_csp_header(self) -> str:
        """
        Build Content Security Policy header.

        Default policy:
        - Scripts: Only from same origin and specific CDN
        - Styles: Same origin and inline styles (for static files)
        - Images: Same origin and data URIs
        - Connect: Same origin and WebSocket
        - Default: Same origin only
        """
        if self.custom_csp:
            return self.custom_csp

        # Default CSP for DUCK-E application
        csp_directives = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",  # Allow inline scripts for WebRTC
            "style-src 'self' 'unsafe-inline'",   # Allow inline styles
            "img-src 'self' data: https:",         # Allow images from same origin, data URIs, HTTPS
            "connect-src 'self' wss: https:",      # Allow WebSocket and HTTPS connections
            "font-src 'self' data:",               # Allow fonts from same origin and data URIs
            "object-src 'none'",                   # Disable plugins
            "base-uri 'self'",                     # Restrict base tag
            "form-action 'self'",                  # Restrict form submissions
            "frame-ancestors 'none'",              # Prevent framing (same as X-Frame-Options)
            "upgrade-insecure-requests"            # Upgrade HTTP to HTTPS
        ]

        if self.csp_report_uri:
            csp_directives.append(f"report-uri {self.csp_report_uri}")

        return "; ".join(csp_directives)

    def _build_permissions_policy(self) -> str:
        """
        Build Permissions Policy header.

        Restricts browser features to prevent abuse:
        - Microphone: Only same origin (required for voice chat)
        - Camera: Disabled
        - Geolocation: Disabled
        - Payment: Disabled
        """
        permissions = [
            "microphone=(self)",     # Allow microphone for voice chat
            "camera=()",             # Disable camera
            "geolocation=()",        # Disable geolocation
            "payment=()",            # Disable payment API
            "usb=()",                # Disable USB
            "magnetometer=()",       # Disable magnetometer
            "gyroscope=()",          # Disable gyroscope
            "accelerometer=()",      # Disable accelerometer
        ]
        return ", ".join(permissions)

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        """
        Process request and add security headers to response.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware in chain

        Returns:
            Response with security headers added
        """
        response = await call_next(request)

        # HSTS - Force HTTPS connections
        if self.enable_hsts and request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = self._build_hsts_header()

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # XSS Protection (legacy, but still useful for older browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Content Security Policy
        response.headers["Content-Security-Policy"] = self._build_csp_header()

        # Permissions Policy (Feature Policy)
        response.headers["Permissions-Policy"] = self._build_permissions_policy()

        # Referrer Policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Remove Server header to avoid information disclosure
        if "Server" in response.headers:
            del response.headers["Server"]

        # Remove X-Powered-By if present
        if "X-Powered-By" in response.headers:
            del response.headers["X-Powered-By"]

        return response


def create_security_headers_middleware(
    enable_hsts: bool = None,
    hsts_max_age: int = None,
    csp_report_uri: str = None,
    custom_csp: str = None
) -> type:
    """
    Factory function to create security headers middleware with environment-based configuration.

    Args:
        enable_hsts: Override HSTS setting (reads from ENABLE_HSTS env var if None)
        hsts_max_age: Override HSTS max-age (reads from HSTS_MAX_AGE env var if None)
        csp_report_uri: Override CSP report URI (reads from CSP_REPORT_URI env var if None)
        custom_csp: Override CSP policy (reads from CUSTOM_CSP env var if None)

    Returns:
        Configured SecurityHeadersMiddleware class
    """
    # Read from environment variables with defaults
    if enable_hsts is None:
        enable_hsts = os.getenv("ENABLE_HSTS", "true").lower() == "true"

    if hsts_max_age is None:
        hsts_max_age = int(os.getenv("HSTS_MAX_AGE", "31536000"))

    if csp_report_uri is None:
        csp_report_uri = os.getenv("CSP_REPORT_URI")

    if custom_csp is None:
        custom_csp = os.getenv("CUSTOM_CSP")

    return lambda app: SecurityHeadersMiddleware(
        app,
        enable_hsts=enable_hsts,
        hsts_max_age=hsts_max_age,
        csp_report_uri=csp_report_uri,
        custom_csp=custom_csp
    )
