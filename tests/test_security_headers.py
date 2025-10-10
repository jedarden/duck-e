"""
Integration tests for security headers middleware.
"""
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from app.middleware import create_security_headers_middleware, configure_cors


@pytest.fixture
def app_with_security():
    """Create FastAPI app with security middleware for testing."""
    app = FastAPI()

    # Configure CORS
    configure_cors(app, allowed_origins=["https://example.com", "http://localhost:3000"])

    # Add security headers
    security_middleware = create_security_headers_middleware(
        enable_hsts=True,
        hsts_max_age=31536000
    )
    app.add_middleware(security_middleware)

    @app.get("/test")
    async def test_endpoint():
        return {"message": "test"}

    return app


@pytest.fixture
def client(app_with_security):
    """Create test client."""
    return TestClient(app_with_security)


class TestSecurityHeaders:
    """Test security headers are properly set."""

    def test_hsts_header_present(self, client):
        """Test HSTS header is present on HTTPS requests."""
        response = client.get("/test", base_url="https://testserver")

        assert "strict-transport-security" in response.headers
        assert "max-age=31536000" in response.headers["strict-transport-security"]

    def test_hsts_header_absent_on_http(self, client):
        """Test HSTS header is not present on HTTP requests."""
        response = client.get("/test")

        # HSTS should only be set on HTTPS
        assert "strict-transport-security" not in response.headers

    def test_x_content_type_options(self, client):
        """Test X-Content-Type-Options header is present."""
        response = client.get("/test")

        assert response.headers["x-content-type-options"] == "nosniff"

    def test_x_frame_options(self, client):
        """Test X-Frame-Options header is present."""
        response = client.get("/test")

        assert response.headers["x-frame-options"] == "DENY"

    def test_x_xss_protection(self, client):
        """Test X-XSS-Protection header is present."""
        response = client.get("/test")

        assert response.headers["x-xss-protection"] == "1; mode=block"

    def test_csp_header_present(self, client):
        """Test Content-Security-Policy header is present."""
        response = client.get("/test")

        assert "content-security-policy" in response.headers

        csp = response.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        assert "object-src 'none'" in csp

    def test_permissions_policy(self, client):
        """Test Permissions-Policy header is present."""
        response = client.get("/test")

        assert "permissions-policy" in response.headers

        policy = response.headers["permissions-policy"]
        assert "microphone=(self)" in policy
        assert "camera=()" in policy
        assert "geolocation=()" in policy

    def test_referrer_policy(self, client):
        """Test Referrer-Policy header is present."""
        response = client.get("/test")

        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"

    def test_server_header_removed(self, client):
        """Test Server header is removed for security."""
        response = client.get("/test")

        # Server header should be removed or not present
        # Note: uvicorn may add it, but middleware should remove it
        assert response.headers.get("server", "").lower() != "uvicorn"


class TestCORS:
    """Test CORS configuration."""

    def test_cors_valid_origin(self, client):
        """Test CORS allows valid origin."""
        response = client.options(
            "/test",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET"
            }
        )

        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "https://example.com"

    def test_cors_localhost_origin(self, client):
        """Test CORS allows localhost origin."""
        response = client.options(
            "/test",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET"
            }
        )

        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"

    def test_cors_invalid_origin_rejected(self, client):
        """Test CORS rejects invalid origin."""
        response = client.options(
            "/test",
            headers={
                "Origin": "https://evil.com",
                "Access-Control-Request-Method": "GET"
            }
        )

        # Invalid origin should not receive CORS headers
        # FastAPI CORSMiddleware returns null for invalid origins
        assert response.headers.get("access-control-allow-origin") != "https://evil.com"

    def test_cors_credentials_allowed(self, client):
        """Test CORS allows credentials."""
        response = client.options(
            "/test",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET"
            }
        )

        assert response.headers.get("access-control-allow-credentials") == "true"


class TestCustomCSP:
    """Test custom CSP configuration."""

    def test_custom_csp_policy(self):
        """Test custom CSP policy is applied."""
        app = FastAPI()

        custom_csp = "default-src 'none'; script-src 'self'"
        security_middleware = create_security_headers_middleware(custom_csp=custom_csp)
        app.add_middleware(security_middleware)

        @app.get("/test")
        async def test_endpoint():
            return {"message": "test"}

        client = TestClient(app)
        response = client.get("/test")

        assert response.headers["content-security-policy"] == custom_csp

    def test_csp_report_uri(self):
        """Test CSP report URI is added."""
        app = FastAPI()

        security_middleware = create_security_headers_middleware(
            csp_report_uri="https://example.com/csp-report"
        )
        app.add_middleware(security_middleware)

        @app.get("/test")
        async def test_endpoint():
            return {"message": "test"}

        client = TestClient(app)
        response = client.get("/test")

        csp = response.headers["content-security-policy"]
        assert "report-uri https://example.com/csp-report" in csp
