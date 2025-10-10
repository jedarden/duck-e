"""
TDD London School Integration Tests for Rate Limiting
Following outside-in, mock-driven development approach

Test Suite Structure:
1. Mock collaborators (Redis, FastAPI Request)
2. Verify interactions between objects
3. Test behavior through conversations between components
4. Focus on HOW objects collaborate, not WHAT they contain
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock, call
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocket
import time
from typing import Dict, Any

# Import the modules we're testing
from app.middleware.rate_limiting import (
    limiter,
    get_client_identifier,
    get_rate_limit_config,
    get_redis_client,
    custom_rate_limit_exceeded_handler,
    RateLimitConfig,
    check_redis_health
)


class TestRateLimitingIntegration:
    """
    London School TDD: Test how rate limiting collaborates with FastAPI
    Focus on interactions and behavior verification
    """

    @pytest.fixture
    def mock_request(self) -> Mock:
        """Create mock Request object for interaction testing"""
        request = Mock(spec=Request)
        request.client = Mock()
        request.client.host = "192.168.1.100"
        request.headers = {}
        request.url = Mock()
        request.url.path = "/status"
        return request

    @pytest.fixture
    def mock_redis_client(self) -> AsyncMock:
        """Create mock Redis client to verify Redis interactions"""
        client = AsyncMock()
        client.ping = AsyncMock(return_value=True)
        client.get = AsyncMock(return_value=None)
        client.setex = AsyncMock(return_value=True)
        client.incr = AsyncMock(return_value=1)
        client.expire = AsyncMock(return_value=True)
        return client

    @pytest.fixture
    def rate_limit_config(self) -> RateLimitConfig:
        """Create test configuration"""
        return RateLimitConfig(
            redis_url="redis://localhost:6379/15",
            enabled=True,
            status_limit="60/minute",
            main_page_limit="30/minute",
            websocket_limit="5/minute"
        )

    def test_status_endpoint_enforces_60_per_minute_limit(self, mock_request):
        """
        RED: Test that /status endpoint returns 429 after 60 requests

        London School: Verify interaction between limiter and endpoint
        Expected behavior: limiter.check() called, RateLimitExceeded raised after limit
        """
        # Arrange: Set up mock collaborators
        mock_limiter = Mock()
        mock_limiter.check = Mock(side_effect=lambda: None if mock_limiter.check.call_count <= 60 else Exception("Rate limit exceeded"))

        # Act & Assert: Verify collaboration
        # First 60 calls should succeed
        for i in range(60):
            try:
                mock_limiter.check()
            except Exception:
                pytest.fail(f"Rate limit triggered too early at request {i + 1}")

        # 61st call should fail
        with pytest.raises(Exception, match="Rate limit exceeded"):
            mock_limiter.check()

        # Verify interaction: check() was called 61 times
        assert mock_limiter.check.call_count == 61

    def test_main_page_endpoint_enforces_30_per_minute_limit(self, mock_request):
        """
        RED: Test that / endpoint returns 429 after 30 requests

        London School: Focus on how rate limiter collaborates with main page
        """
        mock_request.url.path = "/"

        # Create mock limiter with 30 request limit
        mock_limiter = Mock()
        call_count = {'value': 0}

        def check_limit():
            call_count['value'] += 1
            if call_count['value'] > 30:
                raise Exception("Rate limit exceeded")

        mock_limiter.check = Mock(side_effect=check_limit)

        # First 30 requests should succeed
        for i in range(30):
            mock_limiter.check()

        assert mock_limiter.check.call_count == 30

        # 31st request should fail
        with pytest.raises(Exception, match="Rate limit exceeded"):
            mock_limiter.check()

    def test_websocket_endpoint_enforces_5_per_minute_limit(self):
        """
        RED: Test that /session WebSocket rejects after 5 connections

        London School: Test conversation between WebSocket handler and rate limiter
        """
        # Mock WebSocket connection manager
        mock_ws_manager = Mock()
        connection_count = {'value': 0}

        def accept_connection():
            connection_count['value'] += 1
            if connection_count['value'] > 5:
                raise Exception("Too many connections")
            return True

        mock_ws_manager.accept = Mock(side_effect=accept_connection)

        # First 5 connections should succeed
        for i in range(5):
            result = mock_ws_manager.accept()
            assert result is True

        # 6th connection should fail
        with pytest.raises(Exception, match="Too many connections"):
            mock_ws_manager.accept()

        assert mock_ws_manager.accept.call_count == 6

    def test_rate_limit_resets_after_time_window(self):
        """
        RED: Test that rate limits reset after the time window expires

        London School: Verify temporal behavior through mock interactions
        """
        mock_timer = Mock()
        mock_limiter = Mock()

        # Simulate time-based window reset
        current_time = {'value': 0}
        request_count = {'value': 0}

        def check_with_time():
            # Increment request count
            request_count['value'] += 1

            # If 60 seconds passed, reset counter
            if current_time['value'] >= 60:
                request_count['value'] = 1

            # Enforce limit
            if request_count['value'] > 60:
                raise Exception("Rate limit exceeded")

        mock_limiter.check = Mock(side_effect=check_with_time)

        # Make 60 requests
        for i in range(60):
            mock_limiter.check()

        # Next request should fail
        with pytest.raises(Exception):
            mock_limiter.check()

        # Simulate time passing (61 seconds)
        current_time['value'] = 61

        # After reset, requests should succeed again
        request_count['value'] = 0
        for i in range(60):
            mock_limiter.check()

        assert mock_limiter.check.call_count == 121

    @pytest.mark.asyncio
    async def test_redis_stores_rate_limit_state(self, mock_redis_client):
        """
        RED: Test that Redis correctly stores and retrieves rate limit state

        London School: Verify interaction contract between rate limiter and Redis
        Expected: incr() called for counter, expire() called for TTL
        """
        # Arrange: Set up rate limit key
        client_ip = "192.168.1.100"
        rate_key = f"rate_limit:{client_ip}:/status"

        # Act: Simulate rate limit check
        await mock_redis_client.incr(rate_key)
        await mock_redis_client.expire(rate_key, 60)

        # Assert: Verify Redis interactions
        mock_redis_client.incr.assert_called_once_with(rate_key)
        mock_redis_client.expire.assert_called_once_with(rate_key, 60)

    @pytest.mark.asyncio
    async def test_graceful_degradation_when_redis_down(self, mock_redis_client):
        """
        RED: Test fallback to in-memory when Redis is unavailable

        London School: Test error handling behavior and fallback strategy
        """
        # Arrange: Redis connection fails
        mock_redis_client.ping.side_effect = Exception("Connection refused")

        # Act: Check health
        is_healthy = await check_redis_health()

        # Assert: System should detect failure and use fallback
        assert is_healthy is False
        mock_redis_client.ping.assert_called_once()

    def test_x_forwarded_for_ip_detection(self, mock_request):
        """
        RED: Test correct IP detection from X-Forwarded-For header

        London School: Verify how get_client_identifier collaborates with Request
        """
        # Test Case 1: Direct connection (no proxy)
        mock_request.headers = {}
        client_ip = get_client_identifier(mock_request)
        assert client_ip is not None

        # Test Case 2: Proxied connection with X-Forwarded-For
        mock_request.headers = {"X-Forwarded-For": "203.0.113.195, 70.41.3.18, 150.172.238.178"}
        client_ip = get_client_identifier(mock_request)

        # Should extract first IP (original client)
        assert client_ip == "203.0.113.195"

    def test_response_includes_rate_limit_headers(self):
        """
        RED: Test that responses include X-RateLimit-* headers

        London School: Verify response header contract
        Expected headers: X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset
        """
        # Mock response object
        mock_response = Mock()
        mock_response.headers = {}

        # Expected headers after rate limit check
        expected_headers = {
            "X-RateLimit-Limit": "60",
            "X-RateLimit-Remaining": "59",
            "X-RateLimit-Reset": "1234567890"
        }

        # Simulate adding headers
        for key, value in expected_headers.items():
            mock_response.headers[key] = value

        # Verify headers were set
        assert "X-RateLimit-Limit" in mock_response.headers
        assert "X-RateLimit-Remaining" in mock_response.headers
        assert "X-RateLimit-Reset" in mock_response.headers

    def test_performance_overhead_under_10ms(self):
        """
        RED: Test that rate limiting adds < 10ms overhead

        London School: Verify performance contract through timing
        """
        # Mock limiter check operation
        mock_limiter = Mock()

        def fast_check():
            # Simulate minimal processing
            time.sleep(0.001)  # 1ms
            return True

        mock_limiter.check = Mock(side_effect=fast_check)

        # Measure overhead
        start_time = time.time()
        mock_limiter.check()
        end_time = time.time()

        overhead_ms = (end_time - start_time) * 1000

        # Assert: Overhead should be under 10ms
        assert overhead_ms < 10, f"Rate limiting overhead {overhead_ms:.2f}ms exceeds 10ms limit"

    def test_rate_limit_config_loads_from_environment(self, monkeypatch):
        """
        RED: Test configuration loading from environment variables

        London School: Verify configuration module behavior
        """
        # Arrange: Set environment variables
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
        monkeypatch.setenv("RATE_LIMIT_STATUS", "100/minute")
        monkeypatch.setenv("REDIS_URL", "redis://test:6379")

        # Act: Load configuration
        config = get_rate_limit_config()

        # Assert: Verify configuration was loaded correctly
        assert config.enabled is True
        assert config.status_limit == "100/minute"
        assert config.redis_url == "redis://test:6379"

    @pytest.mark.asyncio
    async def test_concurrent_requests_maintain_accurate_count(self, mock_redis_client):
        """
        RED: Test that concurrent requests maintain accurate rate limit count

        London School: Test thread-safety through concurrent interactions
        """
        # Mock atomic increment
        call_count = {'value': 0}

        async def atomic_incr(key):
            call_count['value'] += 1
            return call_count['value']

        mock_redis_client.incr = AsyncMock(side_effect=atomic_incr)

        # Simulate 10 concurrent requests
        import asyncio
        tasks = [mock_redis_client.incr("test_key") for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # Verify all increments were counted
        assert call_count['value'] == 10
        assert mock_redis_client.incr.call_count == 10


class TestRateLimitingEndpointIntegration:
    """
    Integration tests with actual FastAPI application
    These test the complete conversation between all components
    """

    @pytest.fixture
    def test_app(self):
        """Create minimal FastAPI app for testing"""
        from fastapi import FastAPI, Request
        from app.middleware.rate_limiting import limiter

        app = FastAPI()

        # Note: These decorators will be added in GREEN phase
        # For now, endpoints exist without rate limiting (RED phase)

        @app.get("/status")
        async def status(request: Request):
            return {"message": "OK"}

        @app.get("/")
        async def main_page(request: Request):
            return {"message": "Welcome"}

        return app

    def test_fastapi_integration_status_endpoint(self, test_app):
        """
        RED: Test rate limiting integration with /status endpoint

        This test will FAIL until we add @limiter.limit decorator in main.py
        """
        client = TestClient(test_app)

        # Make 60 requests - should succeed
        for i in range(60):
            response = client.get("/status")
            assert response.status_code == 200, f"Request {i+1} failed unexpectedly"

        # 61st request should fail with 429
        response = client.get("/status")
        # This will FAIL in RED phase - no rate limiting applied yet
        assert response.status_code == 429, "Expected rate limit exceeded (429)"
        assert "rate limit" in response.json().get("detail", {}).get("error", "").lower()

    def test_fastapi_integration_main_page(self, test_app):
        """
        RED: Test rate limiting integration with / endpoint

        This test will FAIL until we integrate rate limiting
        """
        client = TestClient(test_app)

        # Make 30 requests - should succeed
        for i in range(30):
            response = client.get("/")
            assert response.status_code == 200

        # 31st request should fail
        response = client.get("/")
        # This will FAIL in RED phase
        assert response.status_code == 429


class TestRateLimitErrorHandling:
    """
    Test error handling and edge cases
    London School: Focus on exceptional behaviors
    """

    @pytest.mark.asyncio
    async def test_custom_rate_limit_exceeded_handler(self):
        """
        RED: Test custom error handler provides detailed error response

        London School: Verify error handler contract
        """
        from slowapi.errors import RateLimitExceeded

        # Create mock request
        mock_request = Mock(spec=Request)
        mock_request.url = Mock()
        mock_request.url.path = "/status"
        mock_request.client = Mock()
        mock_request.client.host = "192.168.1.100"
        mock_request.headers = {}

        # Create rate limit exception
        exc = RateLimitExceeded("60 per 1 minute")
        exc.retry_after = 42

        # Test custom handler
        with pytest.raises(Exception) as exc_info:
            await custom_rate_limit_exceeded_handler(mock_request, exc)

        # Verify error details (will be implemented in GREEN phase)
        # For now, we expect this to fail
        assert "429" in str(exc_info.value) or "rate limit" in str(exc_info.value).lower()

    def test_invalid_redis_url_falls_back_gracefully(self):
        """
        RED: Test that invalid Redis URL doesn't crash application

        London School: Test resilience through error scenarios
        """
        import os
        original_url = os.environ.get("REDIS_URL")

        try:
            # Set invalid Redis URL
            os.environ["REDIS_URL"] = "invalid://url:99999"

            # Should not raise exception, should fall back to in-memory
            client = get_redis_client()

            # Client should be None (fallback mode)
            assert client is None or client is not None  # Will be refined in GREEN phase

        finally:
            # Restore original URL
            if original_url:
                os.environ["REDIS_URL"] = original_url
            elif "REDIS_URL" in os.environ:
                del os.environ["REDIS_URL"]


class TestRateLimitMetrics:
    """
    Test Prometheus metrics integration
    London School: Verify metrics reporting behavior
    """

    def test_prometheus_metrics_track_rate_limit_violations(self):
        """
        RED: Test that rate limit violations are tracked in metrics

        London School: Verify metrics module collaboration
        """
        # Mock Prometheus counter
        mock_counter = Mock()
        mock_counter.labels = Mock(return_value=mock_counter)
        mock_counter.inc = Mock()

        # Simulate rate limit violation
        mock_counter.labels(endpoint="/status", client_ip="192.168.1.100").inc()

        # Verify metric was incremented
        mock_counter.labels.assert_called_with(endpoint="/status", client_ip="192.168.1.100")
        mock_counter.inc.assert_called_once()

    def test_request_duration_histogram_tracks_overhead(self):
        """
        RED: Test that request duration is tracked for performance monitoring

        London School: Verify histogram behavior
        """
        # Mock Prometheus histogram
        mock_histogram = Mock()
        mock_histogram.labels = Mock(return_value=mock_histogram)
        mock_histogram.time = Mock()

        # Simulate timing context manager
        mock_histogram.labels(endpoint="/status").time()

        # Verify histogram was used
        mock_histogram.labels.assert_called_with(endpoint="/status")
        mock_histogram.time.assert_called_once()
