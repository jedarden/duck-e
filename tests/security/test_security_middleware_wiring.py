"""Regression tests for security controls on the production FastAPI app."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.responses import Response
from fastapi.testclient import TestClient


LARGE_RESPONSE_PATH = "/__security_regression__/large-response"
RATE_LIMIT_ERROR_PATH = "/__security_regression__/rate-limit-error"


@pytest.fixture(scope="module")
def production_app():
    from app.main import app

    existing_paths = {getattr(route, "path", None) for route in app.routes}
    if LARGE_RESPONSE_PATH not in existing_paths:

        async def large_response():
            return Response(
                content=b"x" * (10 * 1024 * 1024 + 1),
                media_type="application/octet-stream",
            )

        app.add_api_route(
            LARGE_RESPONSE_PATH,
            large_response,
            methods=["GET"],
            include_in_schema=False,
        )

    if RATE_LIMIT_ERROR_PATH not in existing_paths:

        async def rate_limit_error():
            from slowapi.errors import RateLimitExceeded

            error = RateLimitExceeded(
                SimpleNamespace(error_message=None, limit="5/minute")
            )
            error.retry_after = 45
            raise error

        app.add_api_route(
            RATE_LIMIT_ERROR_PATH,
            rate_limit_error,
            methods=["GET"],
            include_in_schema=False,
        )

    return app


@pytest.fixture
def client(production_app, monkeypatch):
    from app.main import cost_tracker

    monkeypatch.setattr(cost_tracker, "circuit_breaker_active", False)
    monkeypatch.setattr(cost_tracker, "circuit_breaker_reset_time", None)
    return TestClient(production_app, raise_server_exceptions=False)


def _assert_payload_too_large(response):
    assert response.status_code == 413
    assert response.json()["error"] == "Payload Too Large"
    assert response.json()["max_size_mb"] == 1.0


def test_production_app_enforces_declared_request_size(client):
    response = client.post("/status", content=b"x" * (1024 * 1024 + 1))

    _assert_payload_too_large(response)
    assert response.headers["x-content-type-options"] == "nosniff"


def test_production_app_enforces_streamed_request_size_without_content_length(client):
    def chunks():
        yield b"a" * (512 * 1024)
        yield b"b" * (512 * 1024 + 1)

    response = client.post(
        "/status",
        content=chunks(),
        headers={"content-type": "application/octet-stream"},
    )

    _assert_payload_too_large(response)


def test_production_app_rejects_deep_json_before_route_parsing(client):
    body = b'{"a":' * 51 + b"1" + b"}" * 51

    response = client.post(
        "/status", content=body, headers={"content-type": "application/json"}
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": "JSON payload too deeply nested",
        "max_depth": 50,
        "detected_depth": 51,
    }


def test_production_app_rejects_json_deeper_than_parser_recursion_limit(client):
    body = b'{"a":' * 1100 + b"1" + b"}" * 1100

    response = client.post(
        "/status", content=body, headers={"content-type": "application/json"}
    )

    assert response.status_code == 400
    assert response.json()["error"] == "JSON payload too deeply nested"
    assert response.json()["max_depth"] == 50


def test_production_app_rejects_xml_entity_expansion(client):
    xml_bomb = b"""<?xml version="1.0"?>
    <!DOCTYPE lolz [
      <!ENTITY lol "lol">
      <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
      <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
    ]>
    <lolz>&lol2;</lolz>"""

    response = client.post(
        "/status", content=xml_bomb, headers={"content-type": "application/xml"}
    )

    assert response.status_code == 400
    assert response.json()["error"] == "Invalid XML"
    assert "DTD" in response.json()["message"] or "entity" in response.json()["message"]


def test_production_app_enforces_response_size(client):
    response = client.get(LARGE_RESPONSE_PATH)

    assert response.status_code == 413
    assert response.json() == {
        "error": "Response Too Large",
        "message": "The requested resource is too large to return",
        "max_size_mb": 10.0,
    }


def test_production_app_adds_security_headers_on_https(production_app):
    response = TestClient(
        production_app, base_url="https://testserver"
    ).get("/status")

    assert response.status_code == 200
    assert response.headers["strict-transport-security"].startswith("max-age=")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" in response.headers
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_production_app_applies_configured_cors(client, production_app):
    from starlette.middleware.cors import CORSMiddleware

    cors = next(item for item in production_app.user_middleware if item.cls is CORSMiddleware)
    configured_origins = cors.kwargs["allow_origins"]
    origin = next(
        (value for value in configured_origins if value != "*"),
        "https://cors-regression.example",
    )
    response = client.options(
        "/status",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    if configured_origins:
        assert response.headers["access-control-allow-origin"] == origin
        assert response.headers["access-control-allow-credentials"] == "true"
    else:
        assert "access-control-allow-origin" not in response.headers


def test_production_app_rejects_unapproved_websocket_origin(client, monkeypatch):
    from app.main import websocket_connection_limiter, ws_security

    monkeypatch.setattr(ws_security.validator, "allowed_origins", ["http://localhost:8000"])
    acquire = AsyncMock()
    monkeypatch.setattr(websocket_connection_limiter, "acquire", acquire)

    with pytest.raises(Exception):
        with client.websocket_connect(
            "/session", headers={"Origin": "https://evil.example"}
        ):
            pass

    acquire.assert_not_awaited()


def test_production_app_returns_documented_authentication_error(client):
    response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"detail": "Authentication required"}


def test_production_app_returns_documented_rate_limit_error_contract(client):
    response = client.get(RATE_LIMIT_ERROR_PATH)

    assert response.status_code == 429
    assert response.headers["retry-after"] == "45"
    assert response.json() == {
        "error": "Rate limit exceeded",
        "message": "Too many requests. Please try again later.",
        "retry_after_seconds": 45,
        "endpoint": RATE_LIMIT_ERROR_PATH,
        "limit": "5/minute",
    }


def test_weather_tool_sanitizes_upstream_response_through_production_websocket(
    client, monkeypatch
):
    import app.main as main
    from app.middleware.rate_limiting import WebSocketAdmission

    monkeypatch.setattr(
        main.ws_security.validator,
        "allowed_origins",
        ["http://localhost:8000"],
    )
    monkeypatch.setattr(
        main.websocket_connection_limiter,
        "acquire",
        AsyncMock(
            return_value=WebSocketAdmission(
                allowed=True,
                reason="allowed",
                tier="free",
                rate_limit="5/minute",
                max_concurrent_sessions=1,
                current_connections=1,
            )
        ),
    )
    monkeypatch.setattr(main.websocket_connection_limiter, "release", AsyncMock())
    monkeypatch.setattr(
        main.cost_tracker,
        "can_start_new_session",
        AsyncMock(return_value={"allowed": True, "message": "ok"}),
    )
    monkeypatch.setattr(main.cost_tracker, "start_session", AsyncMock())
    monkeypatch.setattr(main.cost_tracker, "check_circuit_breaker", AsyncMock())
    monkeypatch.setattr(main.cost_tracker, "end_session", AsyncMock())
    monkeypatch.setattr(main, "realtime_llm_config", {
        "config_list": [{"model": "test-realtime", "api_key": "test-key"}]
    })

    captured = {}

    class FakeRealtimeSession:
        def __init__(self, websocket, **kwargs):
            self.websocket = websocket
            self.handlers = {}

        def register_tool(self, name, description, handler, parameters):
            self.handlers[name] = handler

        async def run(self):
            result = await self.handlers["get_current_weather"]("London")
            captured["result"] = result
            await self.websocket.send_json({"type": "test_result", "result": result})

    monkeypatch.setattr(main, "RealtimeSession", FakeRealtimeSession)

    def fake_get(url, **kwargs):
        response = Mock(status_code=200, text="")
        response.raise_for_status = Mock()
        if url.startswith("https://geocoding-api.open-meteo.com/"):
            response.json.return_value = {
                "results": [{"latitude": 51.5, "longitude": -0.12, "timezone": "UTC"}]
            }
        else:
            response.json.return_value = {
                "current": {"summary": "<script>alert('xss')</script>Sunny"}
            }
        return response

    monkeypatch.setattr(main.requests, "get", fake_get)

    with client.websocket_connect(
        "/session", headers={"Origin": "http://localhost:8000"}
    ) as websocket:
        message = websocket.receive_json()

    assert message["type"] == "test_result"
    assert "<script>" not in message["result"]
    assert "alert('xss')" not in message["result"]
    assert "Sunny" in message["result"]
