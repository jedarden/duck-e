"""
Integration tests for WebSocket origin validation.
"""
import pytest
from fastapi import FastAPI, WebSocket, status
from fastapi.testclient import TestClient
from app.middleware import get_websocket_security_middleware, WebSocketOriginValidator


@pytest.fixture
def app_with_ws_validation():
    """Create FastAPI app with WebSocket validation for testing."""
    app = FastAPI()

    ws_security = WebSocketOriginValidator(
        allowed_origins=["https://example.com", "http://localhost:3000"],
        require_origin=True
    )

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        if not await ws_security.validate_and_accept(websocket):
            return

        # Echo messages
        try:
            while True:
                data = await websocket.receive_text()
                await websocket.send_text(f"Echo: {data}")
        except Exception:
            pass

    return app


@pytest.fixture
def client(app_with_ws_validation):
    """Create test client."""
    return TestClient(app_with_ws_validation)


class TestWebSocketOriginValidation:
    """Test WebSocket origin validation."""

    def test_valid_origin_accepted(self, client):
        """Test WebSocket connection with valid origin is accepted."""
        with client.websocket_connect("/ws", headers={"Origin": "https://example.com"}) as websocket:
            websocket.send_text("test")
            data = websocket.receive_text()
            assert data == "Echo: test"

    def test_localhost_origin_accepted(self, client):
        """Test WebSocket connection from localhost is accepted."""
        with client.websocket_connect("/ws", headers={"Origin": "http://localhost:3000"}) as websocket:
            websocket.send_text("hello")
            data = websocket.receive_text()
            assert data == "Echo: hello"

    def test_invalid_origin_rejected(self, client):
        """Test WebSocket connection with invalid origin is rejected."""
        with pytest.raises(Exception):
            # This should raise an exception due to connection rejection
            with client.websocket_connect("/ws", headers={"Origin": "https://evil.com"}):
                pass

    def test_missing_origin_rejected(self, client):
        """Test WebSocket connection without origin is rejected."""
        # When require_origin is True, missing origin should be rejected
        with pytest.raises(Exception):
            with client.websocket_connect("/ws"):
                pass


class TestWebSocketWildcardOrigins:
    """Test WebSocket validation with wildcard origins."""

    def test_wildcard_subdomain_accepted(self):
        """Test wildcard subdomain pattern matching."""
        app = FastAPI()

        ws_security = WebSocketOriginValidator(
            allowed_origins=["https://*.example.com"],
            require_origin=True
        )

        @app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            if not await ws_security.validate_and_accept(websocket):
                return

            try:
                while True:
                    data = await websocket.receive_text()
                    await websocket.send_text(f"Echo: {data}")
            except Exception:
                pass

        client = TestClient(app)

        # Should accept subdomain
        with client.websocket_connect("/ws", headers={"Origin": "https://app.example.com"}) as websocket:
            websocket.send_text("test")
            data = websocket.receive_text()
            assert data == "Echo: test"

        # Should accept different subdomain
        with client.websocket_connect("/ws", headers={"Origin": "https://api.example.com"}) as websocket:
            websocket.send_text("test")
            data = websocket.receive_text()
            assert data == "Echo: test"


class TestWebSocketSecurityMiddleware:
    """Test WebSocket security middleware."""

    def test_security_middleware_validation(self):
        """Test WebSocket security middleware validates connections."""
        from app.middleware import WebSocketSecurityMiddleware

        app = FastAPI()

        ws_security = WebSocketSecurityMiddleware(
            allowed_origins=["https://example.com"],
            connection_timeout=300
        )

        @app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            if not await ws_security.validate_connection(websocket):
                return

            try:
                while True:
                    data = await websocket.receive_text()
                    await websocket.send_text(f"Echo: {data}")
            except Exception:
                pass

        client = TestClient(app)

        # Valid origin should connect
        with client.websocket_connect("/ws", headers={"Origin": "https://example.com"}) as websocket:
            websocket.send_text("test")
            data = websocket.receive_text()
            assert data == "Echo: test"


class TestOriginPatternMatching:
    """Test origin pattern matching logic."""

    def test_exact_match(self):
        """Test exact origin matching."""
        validator = WebSocketOriginValidator(
            allowed_origins=["https://example.com"],
            require_origin=True
        )

        assert validator._is_origin_allowed("https://example.com") is True
        assert validator._is_origin_allowed("https://other.com") is False

    def test_wildcard_subdomain_match(self):
        """Test wildcard subdomain matching."""
        validator = WebSocketOriginValidator(
            allowed_origins=["https://*.example.com"],
            require_origin=True
        )

        assert validator._is_origin_allowed("https://app.example.com") is True
        assert validator._is_origin_allowed("https://api.example.com") is True
        assert validator._is_origin_allowed("https://example.com") is False
        assert validator._is_origin_allowed("https://evil.com") is False

    def test_multiple_origins(self):
        """Test multiple allowed origins."""
        validator = WebSocketOriginValidator(
            allowed_origins=[
                "https://example.com",
                "https://app.example.com",
                "http://localhost:3000"
            ],
            require_origin=True
        )

        assert validator._is_origin_allowed("https://example.com") is True
        assert validator._is_origin_allowed("https://app.example.com") is True
        assert validator._is_origin_allowed("http://localhost:3000") is True
        assert validator._is_origin_allowed("https://evil.com") is False
