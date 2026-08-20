"""
Pytest configuration and fixtures for authentication tests
"""
import os
import sys
import pytest
from datetime import datetime, timedelta, timezone
from jose import jwt
from typing import Dict, Any


# Test JWT configuration
TEST_SECRET_KEY = "test-secret-key-for-jwt-validation-only"  # pragma: allowlist secret
TEST_ALGORITHM = "HS256"


@pytest.fixture(autouse=True)
def set_jwt_secret_key(monkeypatch, request):
    """
    Set JWT_SECRET_KEY environment variable for all authentication tests.
    This ensures tests don't rely on the insecure default secret.
    Skips TestDefaultSecretRejection tests which need the default/secret to test rejection.
    """
    # Skip setting the secret for tests that specifically test default secret rejection
    # Check if the test class name contains "DefaultSecretRejection"
    test_class_name = request.node.cls.__name__ if request.node.cls else ""
    if "DefaultSecretRejection" in test_class_name:
        yield
        return

    monkeypatch.setenv("JWT_SECRET_KEY", TEST_SECRET_KEY)
    # Force reload of auth module to pick up new environment variable
    import importlib
    if "app.middleware.auth" in sys.modules:
        importlib.reload(sys.modules["app.middleware.auth"])
    if "app.middleware.google_oauth" in sys.modules:
        importlib.reload(sys.modules["app.middleware.google_oauth"])
    yield


@pytest.fixture
def jwt_secret():
    """JWT secret key for testing"""
    return TEST_SECRET_KEY


@pytest.fixture
def jwt_algorithm():
    """JWT algorithm for testing"""
    return TEST_ALGORITHM


@pytest.fixture
def create_test_token():
    """Factory fixture for creating test JWT tokens"""
    def _create_token(payload: Dict[str, Any], secret: str = TEST_SECRET_KEY) -> str:
        """Create a test JWT token with given payload"""
        # Add expiration if not present
        if "exp" not in payload:
            payload["exp"] = datetime.now(timezone.utc) + timedelta(hours=2)

        return jwt.encode(payload, secret, algorithm=TEST_ALGORITHM)

    return _create_token


@pytest.fixture
def valid_premium_token(create_test_token):
    """Valid premium tier JWT token"""
    payload = {
        "sub": "test_premium_user",
        "tier": "premium",
        "exp": datetime.now(timezone.utc) + timedelta(hours=2)
    }
    return create_test_token(payload)


@pytest.fixture
def valid_enterprise_token(create_test_token):
    """Valid enterprise tier JWT token"""
    payload = {
        "sub": "test_enterprise_user",
        "tier": "enterprise",
        "exp": datetime.now(timezone.utc) + timedelta(hours=8)
    }
    return create_test_token(payload)


@pytest.fixture
def expired_token(create_test_token):
    """Expired JWT token"""
    payload = {
        "sub": "expired_user",
        "tier": "premium",
        "exp": datetime.now(timezone.utc) - timedelta(hours=1)
    }
    return create_test_token(payload)


@pytest.fixture
def token_with_invalid_signature():
    """Token with invalid signature (signed with wrong key)"""
    payload = {
        "sub": "attacker",
        "tier": "enterprise",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1)
    }
    wrong_secret = "wrong-secret-key"  # pragma: allowlist secret
    return jwt.encode(payload, wrong_secret, algorithm=TEST_ALGORITHM)


@pytest.fixture
def mock_redis_client():
    """Mock Redis client for testing token revocation"""
    from unittest.mock import AsyncMock, Mock

    mock = Mock()
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock(return_value=True)
    mock.setex = AsyncMock(return_value=True)
    mock.delete = AsyncMock(return_value=1)

    return mock
