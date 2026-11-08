"""Integration tests for the shared HTTP and WebSocket JWT contract."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from starlette.datastructures import Headers
from starlette.websockets import WebSocketDisconnect


TEST_SECRET = "auth-contract-test-secret"  # pragma: allowlist secret


def make_token(**claims):
    return jwt.encode(claims, TEST_SECRET, algorithm="HS256")


@pytest.fixture
def client(monkeypatch):
    from app.middleware import auth

    monkeypatch.setattr(auth, "JWT_SECRET_KEY", TEST_SECRET)
    monkeypatch.setattr(auth, "_redis_client", None)
    from app.main import app, cost_tracker

    # Earlier cost-protection tests can leave the app-wide circuit breaker
    # active, which would mask these authentication responses with HTTP 503.
    cost_tracker.circuit_breaker_active = False
    cost_tracker.circuit_breaker_reset_time = None

    return TestClient(app)


@pytest.fixture
def tier_client(monkeypatch):
    """Exercise the optional-auth tier resolver through an HTTP request."""
    from fastapi import FastAPI, Request
    from app.middleware import auth

    monkeypatch.setattr(auth, "JWT_SECRET_KEY", TEST_SECRET)
    monkeypatch.setattr(auth, "_redis_client", None)

    app = FastAPI()

    @app.get("/tier")
    async def get_tier(request: Request):
        return {"tier": auth.get_user_tier(request)}

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


def test_optional_http_request_without_credentials_uses_free_tier(tier_client):
    response = tier_client.get("/tier")

    assert response.status_code == 200
    assert response.json() == {"tier": "free"}


@pytest.mark.parametrize("tier", ["free", "premium", "enterprise"])
def test_auth_me_accepts_supported_tier_claims(client, tier):
    token = make_token(**valid_claims(tier=tier))

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["user_info"]["tier"] == tier


def test_auth_me_maps_legacy_token_without_tier_to_premium(client):
    claims = valid_claims()
    claims.pop("tier")
    token = make_token(**claims)

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["user_info"]["tier"] == "premium"


def test_auth_me_accepts_legacy_access_token_without_token_type(client):
    claims = valid_claims()
    claims.pop("token_type")
    token = make_token(**claims)

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "invalid_claim",
    [
        "missing-expiration",
        "expired",
        "numeric-string-expiration",
        "infinite-expiration",
        "missing-subject",
        "empty-subject",
        "unsupported-tier",
        "refresh-token",
    ],
)
def test_auth_me_rejects_invalid_access_tokens_with_401(client, invalid_claim):
    claims = valid_claims()
    if invalid_claim == "missing-expiration":
        claims.pop("exp")
    elif invalid_claim == "expired":
        claims["exp"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif invalid_claim == "numeric-string-expiration":
        claims["exp"] = str(int(datetime.now(timezone.utc).timestamp()) + 300)
    elif invalid_claim == "infinite-expiration":
        claims["exp"] = float("inf")
    elif invalid_claim == "missing-subject":
        claims.pop("sub")
    elif invalid_claim == "empty-subject":
        claims["sub"] = "  "
    elif invalid_claim == "unsupported-tier":
        claims["tier"] = "ultra"
    elif invalid_claim == "refresh-token":
        claims["token_type"] = "refresh"
    token = make_token(**claims)

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "detail" in response.json()


@pytest.mark.parametrize(
    ("transport", "claims"),
    [
        ("query", valid_claims(exp=datetime.now(timezone.utc) - timedelta(seconds=1))),
        ("bearer", valid_claims(exp=datetime.now(timezone.utc) - timedelta(seconds=1))),
        ("query", valid_claims(token_type="refresh")),
        ("bearer", valid_claims(token_type="refresh")),
        ("query", valid_claims(tier="ultra")),
        ("bearer", valid_claims(tier="ultra")),
        ("query", valid_claims(sub="")),
        ("bearer", valid_claims(sub="")),
    ],
    ids=[
        "query-expired",
        "bearer-expired",
        "query-refresh-token",
        "bearer-refresh-token",
        "query-unsupported-tier",
        "bearer-unsupported-tier",
        "query-empty-subject",
        "bearer-empty-subject",
    ],
)
def test_websocket_rejects_credentials_that_http_rejects(client, transport, claims):
    token = make_token(**claims)
    path = f"/session?token={token}" if transport == "query" else "/session"
    headers = {"Origin": "http://localhost:8000"}
    if transport == "bearer":
        headers["Authorization"] = f"Bearer {token}"

    with client.websocket_connect(path, headers=headers) as websocket:
        with pytest.raises(WebSocketDisconnect) as disconnect:
            websocket.receive_text()

    assert disconnect.value.code == 1008
    assert disconnect.value.reason == "Invalid or expired JWT"


def test_auth_me_rejects_malformed_jwt_with_401(client):
    response = client.get(
        "/auth/me", headers={"Authorization": "Bearer not-a-jwt"}
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "invalid_claim",
    ["expired", "refresh-token", "unsupported-tier"],
)
def test_optional_http_request_rejects_invalid_credentials_instead_of_downgrading(
    tier_client, invalid_claim
):
    claims = valid_claims()
    if invalid_claim == "expired":
        claims["exp"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif invalid_claim == "refresh-token":
        claims["token_type"] = "refresh"
    else:
        claims["tier"] = "ultra"
    token = make_token(**claims)

    response = tier_client.get("/tier", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


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


def test_websocket_ignores_untrusted_client_supplied_proxy_identity(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "TRUSTED_PROXY_NETWORKS", ())
    with patch(
        "app.main.cost_tracker.can_start_new_session",
        new=AsyncMock(return_value={
            "allowed": False,
            "message": "test stop after identity resolution",
            "reason": "test",
        }),
    ), patch("app.main.UserMemoryStore") as memory_store:
        with client.websocket_connect(
            "/session",
            headers={
                "Origin": "http://localhost:8000",
                "X-Forwarded-User": "victim",
                "X-Forwarded-Email": "victim@example.com",
                "X-Forwarded-For": "10.20.1.2",
            },
        ) as websocket:
            assert websocket.receive_json()["type"] == "service_unavailable"

        memory_store.assert_not_called()


def test_valid_jwt_identity_precedes_proxy_identity(client, monkeypatch):
    import app.main as main

    token = make_token(**valid_claims(
        sub="jwt-subject",
        email="jwt@example.com",
        name="JWT User",
        auth_method="google_oauth",
    ))
    monkeypatch.setattr(
        main,
        "get_trusted_proxy_identity",
        lambda headers, peer_host, networks: ("proxy-user", "proxy@example.com"),
    )

    with patch(
        "app.main.cost_tracker.can_start_new_session",
        new=AsyncMock(return_value={
            "allowed": False,
            "message": "test stop after identity resolution",
            "reason": "test",
        }),
    ), patch("app.main.UserMemoryStore") as memory_store:
        with client.websocket_connect(
            f"/session?token={token}",
            headers={"Origin": "http://localhost:8000"},
        ) as websocket:
            assert websocket.receive_json()["type"] == "service_unavailable"

        memory_store.assert_called_once_with("jwt@example.com")


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


def test_websocket_rejects_duplicate_authorization_headers():
    from app.middleware.auth import extract_websocket_token
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        extract_websocket_token(
            "Bearer one",
            None,
            authorization_header_count=2,
        )

    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_websocket_handler_rejects_duplicate_authorization_headers(monkeypatch):
    import app.main as main

    websocket = Mock()
    websocket.headers = Headers(
        raw=[
            (b"origin", b"http://localhost:8000"),
            (b"authorization", b"Bearer first"),
            (b"authorization", b"Bearer second"),
        ]
    )
    websocket.client = None
    websocket.query_params = Mock()
    websocket.query_params.getlist.return_value = []
    websocket.scope = {}
    websocket.close = AsyncMock()
    monkeypatch.setattr(
        main.ws_security,
        "validate_connection",
        AsyncMock(return_value=True),
    )

    await main.handle_media_stream(websocket)

    websocket.close.assert_awaited_once_with(
        code=1008,
        reason="Invalid or expired JWT",
    )


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
