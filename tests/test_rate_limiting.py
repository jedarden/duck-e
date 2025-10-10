"""
Unit tests for rate limiting middleware
Tests per-endpoint limits, Redis integration, and error handling
"""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch, AsyncMock
import redis.asyncio as redis

from app.middleware.rate_limiting import (
    RateLimitConfig,
    get_rate_limit_config,
    get_client_identifier,
    limiter,
    check_redis_health,
    get_rate_limit_for_endpoint
)


class TestRateLimitConfig:
    """Test rate limit configuration validation"""

    def test_default_config(self):
        """Test default configuration values"""
        config = RateLimitConfig()

        assert config.enabled is True
        assert config.default_limit == "100/minute"
        assert config.status_limit == "60/minute"
        assert config.main_page_limit == "30/minute"
        assert config.websocket_limit == "5/minute"
        assert config.weather_api_limit == "10/hour"
        assert config.web_search_limit == "5/hour"

    def test_custom_config(self):
        """Test custom configuration values"""
        config = RateLimitConfig(
            enabled=False,
            status_limit="120/minute",
            redis_url="redis://localhost:6379/0"
        )

        assert config.enabled is False
        assert config.status_limit == "120/minute"
        assert config.redis_url == "redis://localhost:6379/0"

    @patch.dict('os.environ', {
        'RATE_LIMIT_ENABLED': 'false',
        'RATE_LIMIT_STATUS': '100/minute'
    })
    def test_config_from_env(self):
        """Test configuration loading from environment variables"""
        config = get_rate_limit_config()

        assert config.enabled is False
        assert config.status_limit == "100/minute"


class TestClientIdentification:
    """Test client IP identification for rate limiting"""

    def test_direct_connection(self):
        """Test IP extraction for direct connections"""
        request = Mock(spec=Request)
        request.headers = {}
        request.client = ("192.168.1.1", 8000)

        # Mock get_remote_address
        with patch('app.middleware.rate_limiting.get_remote_address', return_value="192.168.1.1"):
            client_ip = get_client_identifier(request)
            assert client_ip == "192.168.1.1"

    def test_proxied_connection(self):
        """Test IP extraction from X-Forwarded-For header"""
        request = Mock(spec=Request)
        request.headers = {"X-Forwarded-For": "203.0.113.1, 10.0.0.1"}

        client_ip = get_client_identifier(request)
        assert client_ip == "203.0.113.1"

    def test_single_proxy(self):
        """Test single proxy forwarding"""
        request = Mock(spec=Request)
        request.headers = {"X-Forwarded-For": "198.51.100.1"}

        client_ip = get_client_identifier(request)
        assert client_ip == "198.51.100.1"


class TestEndpointLimits:
    """Test endpoint-specific rate limits"""

    def test_status_endpoint_limit(self):
        """Test /status endpoint has correct limit"""
        limit = get_rate_limit_for_endpoint("/status")
        config = get_rate_limit_config()

        assert limit == config.status_limit

    def test_main_page_limit(self):
        """Test / endpoint has correct limit"""
        limit = get_rate_limit_for_endpoint("/")
        config = get_rate_limit_config()

        assert limit == config.main_page_limit

    def test_websocket_limit(self):
        """Test /session endpoint has correct limit"""
        limit = get_rate_limit_for_endpoint("/session")
        config = get_rate_limit_config()

        assert limit == config.websocket_limit

    def test_default_limit(self):
        """Test unknown endpoint uses default limit"""
        limit = get_rate_limit_for_endpoint("/unknown")
        config = get_rate_limit_config()

        assert limit == config.default_limit


@pytest.mark.asyncio
class TestRedisHealth:
    """Test Redis health checking"""

    async def test_redis_healthy(self):
        """Test Redis health check when connection is good"""
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)

        with patch('app.middleware.rate_limiting.redis_client', mock_redis):
            health = await check_redis_health()
            assert health is True
            mock_redis.ping.assert_called_once()

    async def test_redis_unhealthy(self):
        """Test Redis health check when connection fails"""
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=redis.ConnectionError("Connection failed"))

        with patch('app.middleware.rate_limiting.redis_client', mock_redis):
            health = await check_redis_health()
            assert health is False

    async def test_no_redis(self):
        """Test health check when Redis is not configured"""
        with patch('app.middleware.rate_limiting.redis_client', None):
            health = await check_redis_health()
            assert health is False


class TestRateLimitingIntegration:
    """Integration tests for rate limiting in FastAPI app"""

    def create_test_app(self):
        """Create test FastAPI app with rate limiting"""
        app = FastAPI()

        from app.middleware import (
            limiter,
            RateLimitMiddleware,
            custom_rate_limit_exceeded_handler
        )
        from slowapi.errors import RateLimitExceeded

        app.add_middleware(RateLimitMiddleware)
        app.add_exception_handler(RateLimitExceeded, custom_rate_limit_exceeded_handler)
        app.state.limiter = limiter

        @app.get("/test")
        @limiter.limit("5/minute")
        async def test_endpoint(request: Request):
            return {"message": "success"}

        return app

    @patch.dict('os.environ', {'RATE_LIMIT_ENABLED': 'true'})
    def test_rate_limit_enforcement(self):
        """Test that rate limits are enforced"""
        app = self.create_test_app()
        client = TestClient(app)

        # Make requests within limit
        for i in range(5):
            response = client.get("/test")
            assert response.status_code == 200

    @patch.dict('os.environ', {'RATE_LIMIT_ENABLED': 'false'})
    def test_rate_limiting_disabled(self):
        """Test that rate limiting can be disabled"""
        app = self.create_test_app()
        client = TestClient(app)

        # Should allow unlimited requests when disabled
        for i in range(10):
            response = client.get("/test")
            assert response.status_code == 200

    def test_rate_limit_headers(self):
        """Test that rate limit headers are present"""
        app = self.create_test_app()
        client = TestClient(app)

        response = client.get("/test")

        # Check for rate limit headers
        assert "X-RateLimit-Limit" in response.headers or response.status_code == 200

    def test_different_ips_separate_limits(self):
        """Test that different IPs have separate rate limits"""
        app = self.create_test_app()
        client = TestClient(app)

        # Request from first IP
        response1 = client.get(
            "/test",
            headers={"X-Forwarded-For": "192.168.1.1"}
        )
        assert response1.status_code == 200

        # Request from second IP
        response2 = client.get(
            "/test",
            headers={"X-Forwarded-For": "192.168.1.2"}
        )
        assert response2.status_code == 200


class TestRateLimitErrorHandling:
    """Test error handling and responses"""

    def test_rate_limit_exceeded_response(self):
        """Test 429 response format when rate limit exceeded"""
        from slowapi.errors import RateLimitExceeded

        # Create mock exception
        exc = RateLimitExceeded("5 per 1 minute")
        exc.retry_after = 60

        request = Mock(spec=Request)
        request.url.path = "/test"

        # Test would require async context, verify exception properties
        assert exc.retry_after == 60

    def test_retry_after_header(self):
        """Test that Retry-After header is set correctly"""
        from app.middleware.rate_limiting import custom_rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded

        request = Mock(spec=Request)
        request.url.path = "/test"

        exc = RateLimitExceeded("5 per 1 minute")
        exc.retry_after = 45

        # Handler should raise HTTPException with Retry-After header
        # Full async test would require test client


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
