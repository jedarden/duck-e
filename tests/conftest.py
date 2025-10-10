"""
Pytest configuration and fixtures for DUCK-E tests
"""
import pytest
import os
from typing import Generator


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Set up test environment variables"""
    os.environ["OPENAI_API_KEY"] = "test-key"
    os.environ["WEATHER_API_KEY"] = "test-weather-key"
    os.environ["RATE_LIMIT_ENABLED"] = "true"
    os.environ["COST_PROTECTION_ENABLED"] = "true"


@pytest.fixture
def clean_environment() -> Generator:
    """Clean up environment after each test"""
    yield
    # Reset any test-specific environment variables
    test_vars = [
        "REDIS_URL",
        "RATE_LIMIT_STATUS",
        "COST_PROTECTION_MAX_SESSION_COST_USD"
    ]
    for var in test_vars:
        if var in os.environ:
            del os.environ[var]


@pytest.fixture
def mock_redis_url():
    """Provide mock Redis URL for testing"""
    return "redis://localhost:6379/15"  # Use DB 15 for tests


@pytest.fixture
def sample_session_id():
    """Provide sample session ID for tests"""
    return "test-session-12345"


@pytest.fixture
def mock_redis_for_rate_limiting():
    """Mock Redis client for rate limiting tests"""
    from unittest.mock import AsyncMock

    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.setex = AsyncMock(return_value=True)
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock(return_value=True)
    mock_redis.ttl = AsyncMock(return_value=60)

    return mock_redis


@pytest.fixture
def rate_limit_test_config():
    """Configure environment for rate limiting tests"""
    import os

    # Store original values
    original_values = {
        "RATE_LIMIT_ENABLED": os.environ.get("RATE_LIMIT_ENABLED"),
        "RATE_LIMIT_STATUS": os.environ.get("RATE_LIMIT_STATUS"),
        "RATE_LIMIT_MAIN_PAGE": os.environ.get("RATE_LIMIT_MAIN_PAGE"),
        "RATE_LIMIT_WEBSOCKET": os.environ.get("RATE_LIMIT_WEBSOCKET"),
        "REDIS_URL": os.environ.get("REDIS_URL"),
    }

    # Set test configuration
    os.environ["RATE_LIMIT_ENABLED"] = "true"
    os.environ["RATE_LIMIT_STATUS"] = "60/minute"
    os.environ["RATE_LIMIT_MAIN_PAGE"] = "30/minute"
    os.environ["RATE_LIMIT_WEBSOCKET"] = "5/minute"

    yield

    # Restore original values
    for key, value in original_values.items():
        if value is not None:
            os.environ[key] = value
        elif key in os.environ:
            del os.environ[key]


@pytest.fixture
def fastapi_test_client():
    """Create FastAPI test client with rate limiting"""
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)
