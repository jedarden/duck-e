"""Integration tests for the shared HTTP and WebSocket JWT contract."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from starlette.websockets import WebSocketDisconnect


TEST_SECRET = "auth-contract-test-secret"  # pragma: allowlist secret


def make_token(**claims):
    return jwt.encode(claims, TEST_SECRET, algorithm="HS256")


@pytest.fixture
def client(monkeypatch):
    from app.middleware import auth

    monkeypatch.setattr(auth, "JWT_SECRET_KEY", TEST_SECRET)
    monkeypatch.setattr(auth, "_redis_client", None)
    from app.main import app

    return TestClient(app)


def valid_claims(**overrides):
    claims = {
        "sub": "auth-contract-user",
        "tier": "premium",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "token_type": "access",
    }
    claims.update(overrides)
    return claims


claims_without_expiration = valid_claims()
claims_without_expiration.pop("exp")


def test_auth_me_accepts_valid_bearer_access_token(client):
    token = make_token(**valid_claims(email="user@example.com"))

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["user_info"]["tier"] == "premium"
    assert response.json()["user_info"]["email"] == "user@example.com"


def test_missing_http_credential_uses_free_tier():
    from app.middleware.auth import get_user_tier

    request = Mock()
    request.headers = {}

    assert get_user_tier(request) == "free"


@pytest.mark.parametrize(
    "claims",
    [
        claims_without_expiration,
        valid_claims(exp=datetime.now(timezone.utc) - timedelta(seconds=1)),
        valid_claims(tier="ultra"),
        valid_claims(token_type="refresh"),
    ],
    ids=["missing-expiration", "expired", "unsupported-tier", "refresh-token"],
)
def test_auth_me_rejects_invalid_access_tokens_with_401(client, claims):
    token = make_token(**claims)

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "detail" in response.json()


def test_auth_me_rejects_malformed_bearer_header_with_401(client):
    response = client.get("/auth/me", headers={"Authorization": "Basic abc"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_auth_me_requires_a_credential(client):
    response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_auth_me_rejects_invalid_signature_with_401(client):
    token = jwt.encode(
        valid_claims(), "different-signing-secret", algorithm="HS256"
    )

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_websocket_rejects_expired_query_token_with_policy_close(client):
    expired = make_token(**valid_claims(exp=datetime.now(timezone.utc) - timedelta(seconds=1)))

    with client.websocket_connect(
        f"/session?token={expired}",
        headers={"Origin": "http://localhost:8000"},
    ) as websocket:
        with pytest.raises(WebSocketDisconnect) as disconnect:
            websocket.receive_text()

    assert disconnect.value.code == 1008
    assert disconnect.value.reason == "Invalid or expired JWT"


def test_websocket_accepts_valid_browser_query_token(client):
    token = make_token(**valid_claims())
    with patch(
        "app.main.cost_tracker.can_start_new_session",
        new=AsyncMock(return_value={
            "allowed": False,
            "message": "test stop after authentication",
            "reason": "test",
        }),
    ), patch("app.main.UserMemoryStore") as memory_store:
        with client.websocket_connect(
            f"/session?token={token}",
            headers={"Origin": "http://localhost:8000"},
        ) as websocket:
            assert websocket.receive_json()["type"] == "service_unavailable"
            with pytest.raises(WebSocketDisconnect) as disconnect:
                websocket.receive_text()

        assert disconnect.value.code == 1013
        memory_store.assert_called_once_with("auth-contract-user")


def test_websocket_accepts_valid_bearer_header_token(client):
    token = make_token(**valid_claims())
    with patch(
        "app.main.cost_tracker.can_start_new_session",
        new=AsyncMock(return_value={
            "allowed": False,
            "message": "test stop after authentication",
            "reason": "test",
        }),
    ), patch("app.main.UserMemoryStore") as memory_store:
        with client.websocket_connect(
            "/session",
            headers={
                "Origin": "http://localhost:8000",
                "Authorization": f"Bearer {token}",
            },
        ) as websocket:
            assert websocket.receive_json()["type"] == "service_unavailable"
            with pytest.raises(WebSocketDisconnect) as disconnect:
                websocket.receive_text()

        assert disconnect.value.code == 1013
        memory_store.assert_called_once_with("auth-contract-user")


@pytest.mark.parametrize(
    ("path", "headers"),
    [
        (
            "/session?token=first&token=second",
            {"Origin": "http://localhost:8000"},
        ),
        (
            "/session?token=query-token",
            {
                "Origin": "http://localhost:8000",
                "Authorization": "Bearer header-token",
            },
        ),
    ],
    ids=["repeated-query-token", "header-and-query-token"],
)
def test_websocket_rejects_ambiguous_token_transports(client, path, headers):
    with client.websocket_connect(path, headers=headers) as websocket:
        with pytest.raises(WebSocketDisconnect) as disconnect:
            websocket.receive_text()

    assert disconnect.value.code == 1008
    assert disconnect.value.reason == "Invalid or expired JWT"


def test_browser_websocket_query_transport_uses_the_shared_token_validator(client):
    from app.middleware.auth import extract_websocket_token, validate_access_token

    token = make_token(**valid_claims(tier="enterprise"))
    extracted = extract_websocket_token(None, token)

    assert extracted == token
    assert validate_access_token(extracted)["tier"] == "enterprise"


def test_websocket_rejects_ambiguous_header_and_query_transports(client):
    from app.middleware.auth import extract_websocket_token
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        extract_websocket_token("Bearer one", "two")

    assert error.value.status_code == 401


def test_missing_origin_log_does_not_include_authorization_value(caplog):
    from app.middleware.websocket_validator import WebSocketOriginValidator

    websocket = Mock()
    websocket.headers = {"authorization": "Bearer secret-token"}
    websocket.client = None
    validator = WebSocketOriginValidator(require_origin=True)

    assert asyncio.run(validator.validate(websocket)) is False

    record = next(record for record in caplog.records if "Missing Origin" in record.getMessage())
    assert record.header_names == ["authorization"]
    assert "secret-token" not in caplog.text


@pytest.mark.parametrize("authorization", ["Basic abc", "Bearer malformed-token"])
@pytest.mark.asyncio
async def test_optional_http_auth_rejects_supplied_invalid_credentials(authorization):
    from app.middleware.auth import get_current_user_optional
    from fastapi import HTTPException

    request = Mock(headers={"authorization": authorization})

    with pytest.raises(HTTPException) as error:
        await get_current_user_optional(request)

    assert error.value.status_code == 401
    assert error.value.headers["WWW-Authenticate"] == "Bearer"
