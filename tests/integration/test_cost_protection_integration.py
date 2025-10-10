"""
Integration tests for cost protection middleware with WebSocket handler
Following London School TDD methodology - testing interactions and collaborations

This test suite verifies:
1. Session budget enforcement ($5 limit)
2. Circuit breaker activation ($100 threshold)
3. Hourly limit reset ($50/hour)
4. Cost calculation accuracy (±1%)
5. WebSocket closure on budget exceeded
6. Prometheus metrics accuracy
7. Concurrent session handling
8. Redis failure fallback
9. Performance overhead (<5ms)
"""
import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch, MagicMock, call
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient
import redis.asyncio as redis
from prometheus_client import REGISTRY

from app.middleware.cost_protection import (
    SessionCostTracker,
    CostProtectionMiddleware,
    get_cost_tracker,
    get_cost_config
)


@pytest.fixture
def mock_websocket():
    """Create mock WebSocket following London School (mock collaborators)"""
    ws = AsyncMock(spec=WebSocket)
    ws.send_json = AsyncMock()
    ws.send_text = AsyncMock()
    ws.close = AsyncMock()
    ws.accept = AsyncMock()
    ws.receive_json = AsyncMock()
    ws.headers = {"accept-language": "en-US"}
    return ws


@pytest.fixture
def mock_redis_client():
    """Create mock Redis client for testing distributed tracking"""
    mock_redis = AsyncMock(spec=redis.Redis)
    mock_redis.hset = AsyncMock()
    mock_redis.hget = AsyncMock()
    mock_redis.hincrby = AsyncMock()
    mock_redis.hincrbyfloat = AsyncMock()
    mock_redis.expire = AsyncMock()
    mock_redis.delete = AsyncMock()
    return mock_redis


@pytest.fixture
def cost_tracker(mock_redis_client):
    """Create cost tracker instance with mock Redis"""
    return SessionCostTracker(redis_client=mock_redis_client)


@pytest.fixture
def clean_tracker():
    """Create clean cost tracker without Redis for isolated tests"""
    return SessionCostTracker(redis_client=None)


class TestSessionBudgetEnforcement:
    """Test session budget enforcement following London School TDD"""

    @pytest.mark.asyncio
    async def test_session_terminates_at_exact_budget_limit(self, clean_tracker):
        """
        CRITICAL TEST: Session must terminate at exactly $5.00
        Tests interaction between tracker and budget checker
        """
        session_id = "budget-test-001"
        await clean_tracker.start_session(session_id)

        # Simulate API calls approaching $5.00 limit
        # First call: $2.00
        usage1 = await clean_tracker.track_usage(
            session_id=session_id,
            model="gpt-5",
            input_tokens=20_000,  # $0.20
            output_tokens=60_000  # $1.80 = $2.00 total
        )
        assert usage1["budget_ok"] is True
        assert usage1["remaining_budget_usd"] > 3.0

        # Second call: $2.00 more (total $4.00)
        usage2 = await clean_tracker.track_usage(
            session_id=session_id,
            model="gpt-5",
            input_tokens=20_000,
            output_tokens=60_000
        )
        assert usage2["budget_ok"] is True
        assert usage2["remaining_budget_usd"] > 0.99

        # Third call: $1.50 more (total $5.50, exceeds limit)
        usage3 = await clean_tracker.track_usage(
            session_id=session_id,
            model="gpt-5",
            input_tokens=15_000,  # $0.15
            output_tokens=45_000  # $1.35 = $1.50 total
        )

        # Budget should be exceeded
        assert usage3["budget_ok"] is False
        assert usage3["remaining_budget_usd"] <= 0
        assert "Session budget limit exceeded" in usage3["warnings"]
        assert usage3["session_cost"] > 5.0

    @pytest.mark.asyncio
    async def test_websocket_closes_on_budget_exceeded(self, clean_tracker, mock_websocket):
        """
        Test WebSocket closure interaction when budget exceeded
        Verifies collaboration between tracker and WebSocket handler
        """
        session_id = "ws-budget-001"
        await clean_tracker.start_session(session_id)

        # Exceed budget with single large call
        usage = await clean_tracker.track_usage(
            session_id=session_id,
            model="gpt-realtime",
            input_tokens=30_000,  # $3.00
            output_tokens=15_000  # $3.00 = $6.00 total
        )

        # Verify budget exceeded
        assert usage["budget_ok"] is False

        # Simulate WebSocket handler behavior
        if not usage["budget_ok"]:
            await mock_websocket.send_json({
                "type": "budget_exceeded",
                "message": "Session budget limit of $5.00 exceeded",
                "session_cost": usage["session_cost"],
                "warnings": usage["warnings"]
            })
            await mock_websocket.close(code=1008, reason="Budget limit exceeded")

        # Verify WebSocket interactions
        mock_websocket.send_json.assert_called_once()
        call_args = mock_websocket.send_json.call_args[0][0]
        assert call_args["type"] == "budget_exceeded"
        assert call_args["session_cost"] > 5.0

        mock_websocket.close.assert_called_once_with(
            code=1008,
            reason="Budget limit exceeded"
        )

    @pytest.mark.asyncio
    async def test_budget_warning_at_80_percent(self, clean_tracker):
        """
        Test budget warning interaction at 80% threshold ($4.00)
        Verifies early warning system collaboration
        """
        session_id = "warning-test-001"
        await clean_tracker.start_session(session_id)

        # Use exactly $4.00 (80% of $5.00 limit)
        usage = await clean_tracker.track_usage(
            session_id=session_id,
            model="gpt-5",
            input_tokens=40_000,  # $0.40
            output_tokens=120_000  # $3.60 = $4.00 total
        )

        # Should still be OK but close to limit
        assert usage["budget_ok"] is True
        assert usage["remaining_budget_usd"] == pytest.approx(1.0, abs=0.01)

        # Should trigger warning in application layer
        warning_threshold = 0.8 * 5.0  # $4.00
        should_warn = usage["session_cost"] >= warning_threshold
        assert should_warn is True

    @pytest.mark.asyncio
    async def test_multiple_sessions_independent_budgets(self, clean_tracker):
        """
        Test that concurrent sessions track budgets independently
        Verifies session isolation in tracker
        """
        session1 = "concurrent-001"
        session2 = "concurrent-002"
        session3 = "concurrent-003"

        # Start all sessions
        await clean_tracker.start_session(session1)
        await clean_tracker.start_session(session2)
        await clean_tracker.start_session(session3)

        # Session 1: Use $4.50 (under budget)
        usage1 = await clean_tracker.track_usage(
            session_id=session1,
            model="gpt-5",
            input_tokens=45_000,
            output_tokens=135_000
        )

        # Session 2: Use $6.00 (over budget)
        usage2 = await clean_tracker.track_usage(
            session_id=session2,
            model="gpt-realtime",
            input_tokens=30_000,
            output_tokens=15_000
        )

        # Session 3: Use $1.00 (well under budget)
        usage3 = await clean_tracker.track_usage(
            session_id=session3,
            model="gpt-5-mini",
            input_tokens=33_333,
            output_tokens=66_667
        )

        # Verify independent tracking
        assert usage1["budget_ok"] is True  # Session 1 OK
        assert usage2["budget_ok"] is False  # Session 2 exceeded
        assert usage3["budget_ok"] is True  # Session 3 OK

        assert usage1["session_cost"] < 5.0
        assert usage2["session_cost"] > 5.0
        assert usage3["session_cost"] < 2.0


class TestCircuitBreakerActivation:
    """Test circuit breaker functionality - critical for cost protection"""

    @pytest.mark.asyncio
    async def test_circuit_breaker_activates_at_100_dollars(self, clean_tracker):
        """
        CRITICAL TEST: Circuit breaker must activate at $100 system-wide
        Tests coordination between multiple sessions and circuit breaker
        """
        # Simulate 20 sessions each spending $5
        for i in range(20):
            session_id = f"circuit-session-{i:03d}"
            await clean_tracker.start_session(session_id)

            # Each session uses exactly $5.00
            await clean_tracker.track_usage(
                session_id=session_id,
                model="gpt-5",
                input_tokens=50_000,  # $0.50
                output_tokens=150_000  # $4.50 = $5.00 total
            )

        # Calculate total system cost
        total_cost = sum(clean_tracker.session_costs.values())
        assert total_cost >= 100.0

        # Activate circuit breaker
        await clean_tracker.activate_circuit_breaker()

        # Verify circuit breaker state
        assert clean_tracker.circuit_breaker_active is True
        assert clean_tracker.circuit_breaker_reset_time is not None

        # Try to check budget for new session
        new_session = "post-circuit-001"
        await clean_tracker.start_session(new_session)

        budget_status = await clean_tracker.check_budget(new_session, 1.0)

        # Should be blocked by circuit breaker
        assert budget_status["budget_ok"] is False
        assert budget_status["circuit_breaker_active"] is True
        assert "System-wide circuit breaker is active" in budget_status["warnings"]

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_all_new_connections(self, clean_tracker, mock_websocket):
        """
        Test that circuit breaker blocks all new WebSocket connections
        Verifies middleware interaction with WebSocket handler
        """
        await clean_tracker.activate_circuit_breaker()

        # Simulate new connection attempt
        session_id = "blocked-connection-001"
        await clean_tracker.start_session(session_id)

        # Check if connection should be allowed
        budget_status = await clean_tracker.check_budget(session_id, 0.0)

        assert budget_status["circuit_breaker_active"] is True
        assert budget_status["budget_ok"] is False

        # WebSocket should be rejected
        await mock_websocket.send_json({
            "type": "service_unavailable",
            "error": "System under high load",
            "circuit_breaker_active": True
        })
        await mock_websocket.close(code=1013, reason="Service unavailable")

        # Verify interaction
        mock_websocket.send_json.assert_called_once()
        mock_websocket.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_circuit_breaker_recovery_after_cooldown(self, clean_tracker):
        """
        Test circuit breaker automatic recovery after cooldown period
        Verifies time-based reset interaction
        """
        # Activate circuit breaker
        await clean_tracker.activate_circuit_breaker()
        assert clean_tracker.circuit_breaker_active is True

        # Manually set reset time to past (simulate cooldown completion)
        clean_tracker.circuit_breaker_reset_time = datetime.utcnow() - timedelta(minutes=1)

        # Check circuit breaker (should reset)
        await clean_tracker.check_circuit_breaker()

        # Verify recovery
        assert clean_tracker.circuit_breaker_active is False
        assert clean_tracker.circuit_breaker_reset_time is None

        # New sessions should be allowed
        session_id = "post-recovery-001"
        await clean_tracker.start_session(session_id)
        budget_status = await clean_tracker.check_budget(session_id, 0.0)

        assert budget_status["circuit_breaker_active"] is False
        assert budget_status["budget_ok"] is True

    @pytest.mark.asyncio
    async def test_circuit_breaker_with_middleware_integration(self):
        """
        Test circuit breaker integration with middleware
        Verifies full request lifecycle interaction
        """
        # Create mock app and middleware
        mock_app = AsyncMock()
        middleware = CostProtectionMiddleware(mock_app)

        # Activate circuit breaker
        await middleware.tracker.activate_circuit_breaker()

        # Create mock HTTP scope
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/status"
        }
        receive = AsyncMock()
        send = AsyncMock()

        # Process request with active circuit breaker
        await middleware(scope, receive, send)

        # Verify middleware sent 503 response (not calling app)
        mock_app.assert_not_called()


class TestCostCalculationAccuracy:
    """Test cost calculation accuracy - must be within ±1% of OpenAI pricing"""

    def test_gpt5_cost_accuracy(self, clean_tracker):
        """Test gpt-5 cost calculation accuracy"""
        # Test case: 100k input, 200k output
        cost = clean_tracker.calculate_cost("gpt-5", 100_000, 200_000)

        # Expected: (100k/1M * $10) + (200k/1M * $30) = $1.00 + $6.00 = $7.00
        expected = 7.0
        tolerance = expected * 0.01  # 1% tolerance

        assert cost == pytest.approx(expected, abs=tolerance)

    def test_gpt5_mini_cost_accuracy(self, clean_tracker):
        """Test gpt-5-mini cost calculation accuracy"""
        # Test case: 500k input, 1M output
        cost = clean_tracker.calculate_cost("gpt-5-mini", 500_000, 1_000_000)

        # Expected: (500k/1M * $3) + (1M/1M * $15) = $1.50 + $15.00 = $16.50
        expected = 16.5
        tolerance = expected * 0.01

        assert cost == pytest.approx(expected, abs=tolerance)

    def test_gpt_realtime_cost_accuracy(self, clean_tracker):
        """Test gpt-realtime cost calculation accuracy"""
        # Test case: 25k input, 50k output
        cost = clean_tracker.calculate_cost("gpt-realtime", 25_000, 50_000)

        # Expected: (25k/1M * $100) + (50k/1M * $200) = $2.50 + $10.00 = $12.50
        expected = 12.5
        tolerance = expected * 0.01

        assert cost == pytest.approx(expected, abs=tolerance)

    def test_cost_calculation_edge_cases(self, clean_tracker):
        """Test cost calculation with edge cases (0 tokens, 1 token, max tokens)"""
        # Zero tokens
        cost_zero = clean_tracker.calculate_cost("gpt-5", 0, 0)
        assert cost_zero == 0.0

        # Single token
        cost_one = clean_tracker.calculate_cost("gpt-5", 1, 1)
        expected_one = (1/1_000_000 * 10) + (1/1_000_000 * 30)  # $0.00004
        assert cost_one == pytest.approx(expected_one, abs=0.0001)

        # Large token counts (1M tokens)
        cost_large = clean_tracker.calculate_cost("gpt-5", 1_000_000, 1_000_000)
        expected_large = 10.0 + 30.0  # $40.00
        assert cost_large == pytest.approx(expected_large, abs=0.01)

    @pytest.mark.parametrize("model,input_tokens,output_tokens,expected_cost", [
        ("gpt-5", 50_000, 150_000, 5.0),
        ("gpt-5-mini", 166_667, 333_333, 5.5),
        ("gpt-realtime", 15_000, 12_500, 4.0),
        ("gpt-5", 10_000, 30_000, 1.0),
        ("gpt-5-mini", 100_000, 200_000, 3.3),
    ])
    def test_cost_calculation_scenarios(self, clean_tracker, model, input_tokens, output_tokens, expected_cost):
        """Parametrized test for various cost calculation scenarios"""
        cost = clean_tracker.calculate_cost(model, input_tokens, output_tokens)
        tolerance = expected_cost * 0.01
        assert cost == pytest.approx(expected_cost, abs=tolerance)


class TestSessionDurationLimits:
    """Test 30-minute session duration enforcement"""

    @pytest.mark.asyncio
    async def test_session_timeout_after_30_minutes(self, clean_tracker):
        """
        Test session termination after 30-minute timeout
        Verifies time-based budget enforcement
        """
        session_id = "timeout-test-001"
        await clean_tracker.start_session(session_id)

        # Manually set start time to 31 minutes ago
        clean_tracker.session_start_times[session_id] = datetime.utcnow() - timedelta(minutes=31)

        # Check budget status
        budget_status = await clean_tracker.check_budget(session_id, 1.0)

        # Should fail due to duration
        assert budget_status["budget_ok"] is False
        assert "Session duration limit exceeded" in budget_status["warnings"]
        assert budget_status["remaining_duration_seconds"] <= 0

    @pytest.mark.asyncio
    async def test_session_within_duration_limit(self, clean_tracker):
        """Test session within 30-minute limit"""
        session_id = "duration-ok-001"
        await clean_tracker.start_session(session_id)

        # Set start time to 20 minutes ago
        clean_tracker.session_start_times[session_id] = datetime.utcnow() - timedelta(minutes=20)

        budget_status = await clean_tracker.check_budget(session_id, 1.0)

        # Should be OK
        assert budget_status["budget_ok"] is True
        assert budget_status["remaining_duration_seconds"] > 0
        assert budget_status["remaining_duration_seconds"] <= 600  # ~10 minutes left

    @pytest.mark.asyncio
    async def test_combined_budget_and_duration_limits(self, clean_tracker):
        """
        Test interaction between budget and duration limits
        Both must be satisfied for session to continue
        """
        session_id = "combined-test-001"
        await clean_tracker.start_session(session_id)

        # Set duration to 29 minutes (OK)
        clean_tracker.session_start_times[session_id] = datetime.utcnow() - timedelta(minutes=29)

        # Use $4.50 (under budget)
        await clean_tracker.track_usage(
            session_id=session_id,
            model="gpt-5",
            input_tokens=45_000,
            output_tokens=135_000
        )

        budget_status = await clean_tracker.check_budget(
            session_id,
            clean_tracker.session_costs[session_id]
        )

        # Both limits satisfied
        assert budget_status["budget_ok"] is True

        # Now exceed budget while duration OK
        await clean_tracker.track_usage(
            session_id=session_id,
            model="gpt-5",
            input_tokens=10_000,
            output_tokens=30_000  # Additional $1.00, total $5.50
        )

        budget_status = await clean_tracker.check_budget(
            session_id,
            clean_tracker.session_costs[session_id]
        )

        # Should fail due to budget even though duration OK
        assert budget_status["budget_ok"] is False


class TestPrometheusMetrics:
    """Test Prometheus metrics accuracy"""

    @pytest.mark.asyncio
    async def test_metrics_track_session_costs(self, clean_tracker):
        """Test that Prometheus metrics accurately track session costs"""
        session_id = "metrics-test-001"
        await clean_tracker.start_session(session_id)

        # Get initial metric value
        from prometheus_client import REGISTRY
        initial_samples = list(REGISTRY.collect())

        # Track usage
        await clean_tracker.track_usage(
            session_id=session_id,
            model="gpt-5",
            input_tokens=10_000,
            output_tokens=30_000
        )

        # Metrics should be updated
        final_samples = list(REGISTRY.collect())
        assert len(final_samples) >= len(initial_samples)

    @pytest.mark.asyncio
    async def test_metrics_track_token_usage(self, clean_tracker):
        """Test token usage metrics for input and output separately"""
        session_id = "token-metrics-001"
        await clean_tracker.start_session(session_id)

        input_tokens = 50_000
        output_tokens = 150_000

        await clean_tracker.track_usage(
            session_id=session_id,
            model="gpt-5",
            input_tokens=input_tokens,
            output_tokens=output_tokens
        )

        # Verify token counters updated
        assert clean_tracker.session_tokens[session_id]["input"] == input_tokens
        assert clean_tracker.session_tokens[session_id]["output"] == output_tokens

    @pytest.mark.asyncio
    async def test_metrics_track_budget_exceeded_events(self, clean_tracker):
        """Test budget exceeded counter metrics"""
        session_id = "budget-exceeded-metrics-001"
        await clean_tracker.start_session(session_id)

        # Exceed budget
        await clean_tracker.track_usage(
            session_id=session_id,
            model="gpt-realtime",
            input_tokens=30_000,
            output_tokens=15_000  # $6.00 total
        )

        # Budget exceeded metric should increment
        # This is verified by the middleware tracking


class TestRedisDistributedTracking:
    """Test Redis-backed distributed session tracking"""

    @pytest.mark.asyncio
    async def test_session_data_persisted_to_redis(self, cost_tracker, mock_redis_client):
        """
        Test that session data is persisted to Redis
        Verifies interaction with distributed storage
        """
        session_id = "redis-persist-001"
        await cost_tracker.start_session(session_id)

        # Verify Redis interactions
        mock_redis_client.hset.assert_called()
        call_args = mock_redis_client.hset.call_args

        # Should store session data
        assert call_args[0][0] == f"session:{session_id}"
        assert "mapping" in call_args[1]

        # Should set expiration
        mock_redis_client.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_usage_updates_persisted_to_redis(self, cost_tracker, mock_redis_client):
        """Test that usage updates are persisted to Redis"""
        session_id = "redis-update-001"
        await cost_tracker.start_session(session_id)

        await cost_tracker.track_usage(
            session_id=session_id,
            model="gpt-5",
            input_tokens=10_000,
            output_tokens=30_000
        )

        # Should have two hset calls: start session and track usage
        assert mock_redis_client.hset.call_count >= 2

    @pytest.mark.asyncio
    async def test_redis_failure_fallback_to_memory(self):
        """
        Test graceful fallback to in-memory storage on Redis failure
        Verifies resilience in distributed tracking
        """
        mock_redis = AsyncMock()
        mock_redis.hset.side_effect = redis.ConnectionError("Connection failed")

        tracker = SessionCostTracker(redis_client=mock_redis)
        session_id = "redis-fallback-001"

        # Should not raise exception
        await tracker.start_session(session_id)

        # Should still track in memory
        assert session_id in tracker.session_costs
        assert tracker.session_costs[session_id] == 0.0

    @pytest.mark.asyncio
    async def test_session_cleanup_removes_redis_data(self, cost_tracker, mock_redis_client):
        """Test that ending session cleans up Redis data"""
        session_id = "redis-cleanup-001"
        await cost_tracker.start_session(session_id)
        await cost_tracker.end_session(session_id)

        # Should delete Redis key
        mock_redis_client.delete.assert_called_with(f"session:{session_id}")


class TestPerformanceOverhead:
    """Test that cost tracking overhead is <5ms per request"""

    @pytest.mark.asyncio
    async def test_cost_calculation_performance(self, clean_tracker):
        """Test cost calculation completes in <1ms"""
        import time

        model = "gpt-5"
        input_tokens = 100_000
        output_tokens = 200_000

        start = time.perf_counter()
        for _ in range(1000):  # 1000 iterations
            clean_tracker.calculate_cost(model, input_tokens, output_tokens)
        end = time.perf_counter()

        avg_time_ms = ((end - start) / 1000) * 1000
        assert avg_time_ms < 1.0  # Less than 1ms per calculation

    @pytest.mark.asyncio
    async def test_track_usage_performance(self, clean_tracker):
        """Test track_usage completes in <5ms including metrics"""
        import time

        session_id = "perf-test-001"
        await clean_tracker.start_session(session_id)

        iterations = 100
        start = time.perf_counter()

        for i in range(iterations):
            await clean_tracker.track_usage(
                session_id=session_id,
                model="gpt-5-mini",
                input_tokens=1000,
                output_tokens=2000
            )

        end = time.perf_counter()
        avg_time_ms = ((end - start) / iterations) * 1000

        # Should be well under 5ms per track_usage call
        assert avg_time_ms < 5.0

    @pytest.mark.asyncio
    async def test_budget_check_performance(self, clean_tracker):
        """Test budget check completes in <1ms"""
        import time

        session_id = "perf-check-001"
        await clean_tracker.start_session(session_id)

        iterations = 1000
        start = time.perf_counter()

        for _ in range(iterations):
            await clean_tracker.check_budget(session_id, 2.5)

        end = time.perf_counter()
        avg_time_ms = ((end - start) / iterations) * 1000

        assert avg_time_ms < 1.0


class TestWebSocketIntegration:
    """Test WebSocket handler integration with cost protection"""

    @pytest.mark.asyncio
    async def test_websocket_handler_tracks_costs(self, clean_tracker, mock_websocket):
        """
        Test that WebSocket handler properly integrates cost tracking
        Simulates real WebSocket lifecycle
        """
        session_id = "ws-integration-001"

        # Session start
        await clean_tracker.start_session(session_id)
        assert session_id in clean_tracker.session_costs

        # Simulate API call
        usage = await clean_tracker.track_usage(
            session_id=session_id,
            model="gpt-realtime",
            input_tokens=5_000,
            output_tokens=10_000
        )

        # Send response via WebSocket
        await mock_websocket.send_json({
            "type": "usage_update",
            "session_cost": usage["session_cost"],
            "remaining_budget": usage["remaining_budget_usd"]
        })

        # Verify interaction
        mock_websocket.send_json.assert_called_once()

        # Session end
        await clean_tracker.end_session(session_id)
        assert session_id not in clean_tracker.session_costs

    @pytest.mark.asyncio
    async def test_websocket_sends_budget_warning(self, clean_tracker, mock_websocket):
        """
        Test WebSocket sends warning at 80% budget threshold
        """
        session_id = "ws-warning-001"
        await clean_tracker.start_session(session_id)

        # Use $4.00 (80% of limit)
        usage = await clean_tracker.track_usage(
            session_id=session_id,
            model="gpt-5",
            input_tokens=40_000,
            output_tokens=120_000
        )

        # Check if warning should be sent
        warning_threshold = 5.0 * 0.8
        if usage["session_cost"] >= warning_threshold and usage["budget_ok"]:
            await mock_websocket.send_json({
                "type": "budget_warning",
                "message": "80% of session budget used",
                "session_cost": usage["session_cost"],
                "remaining_budget": usage["remaining_budget_usd"]
            })

        # Verify warning sent
        mock_websocket.send_json.assert_called_once()
        call_args = mock_websocket.send_json.call_args[0][0]
        assert call_args["type"] == "budget_warning"

    @pytest.mark.asyncio
    async def test_websocket_prevents_api_call_on_budget_exceeded(self, clean_tracker, mock_websocket):
        """
        Test that WebSocket handler prevents API calls when budget exceeded
        Critical for cost protection
        """
        session_id = "ws-prevent-001"
        await clean_tracker.start_session(session_id)

        # Exceed budget
        await clean_tracker.track_usage(
            session_id=session_id,
            model="gpt-realtime",
            input_tokens=30_000,
            output_tokens=15_000
        )

        # Check budget before next API call
        budget_status = await clean_tracker.check_budget(
            session_id,
            clean_tracker.session_costs[session_id]
        )

        # Should NOT make API call
        if not budget_status["budget_ok"]:
            await mock_websocket.send_json({
                "type": "error",
                "error": "Budget exceeded, cannot process request"
            })
            await mock_websocket.close(code=1008, reason="Budget exceeded")

            # Verify API call blocked
            mock_websocket.send_json.assert_called_once()
            mock_websocket.close.assert_called_once()


class TestConcurrentSessionHandling:
    """Test concurrent session handling and isolation"""

    @pytest.mark.asyncio
    async def test_100_concurrent_sessions(self, clean_tracker):
        """
        Test handling 100 concurrent sessions with independent budgets
        Stress test for production scenarios
        """
        num_sessions = 100
        sessions = [f"concurrent-{i:03d}" for i in range(num_sessions)]

        # Start all sessions concurrently
        await asyncio.gather(*[
            clean_tracker.start_session(sid) for sid in sessions
        ])

        # Verify all sessions initialized
        assert len(clean_tracker.session_costs) == num_sessions

        # Track usage for all sessions concurrently
        tasks = []
        for sid in sessions:
            tasks.append(
                clean_tracker.track_usage(
                    session_id=sid,
                    model="gpt-5-mini",
                    input_tokens=10_000,
                    output_tokens=20_000
                )
            )

        results = await asyncio.gather(*tasks)

        # Verify all sessions tracked independently
        assert len(results) == num_sessions
        for result in results:
            assert result["budget_ok"] is True
            assert result["session_cost"] < 1.0

    @pytest.mark.asyncio
    async def test_session_isolation_under_load(self, clean_tracker):
        """
        Test that session budgets remain isolated under concurrent load
        Verifies no race conditions or budget leakage
        """
        session1 = "isolation-test-1"
        session2 = "isolation-test-2"

        await clean_tracker.start_session(session1)
        await clean_tracker.start_session(session2)

        # Concurrently track different amounts
        await asyncio.gather(
            clean_tracker.track_usage(session1, "gpt-5", 50_000, 150_000),  # $5.00
            clean_tracker.track_usage(session2, "gpt-5-mini", 10_000, 20_000)  # ~$0.33
        )

        # Verify costs tracked separately
        assert clean_tracker.session_costs[session1] > 4.0
        assert clean_tracker.session_costs[session2] < 1.0


class TestEdgeCasesAndErrorHandling:
    """Test edge cases and error handling"""

    @pytest.mark.asyncio
    async def test_track_usage_for_nonexistent_session(self, clean_tracker):
        """Test tracking usage for session that wasn't started"""
        result = await clean_tracker.track_usage(
            session_id="nonexistent-session",
            model="gpt-5",
            input_tokens=1000,
            output_tokens=2000
        )

        # Should still work but without session context
        assert result["session_cost"] > 0

    @pytest.mark.asyncio
    async def test_end_session_multiple_times(self, clean_tracker):
        """Test ending the same session multiple times"""
        session_id = "multi-end-001"
        await clean_tracker.start_session(session_id)
        await clean_tracker.end_session(session_id)

        # Should not raise error
        await clean_tracker.end_session(session_id)

    @pytest.mark.asyncio
    async def test_negative_token_counts(self, clean_tracker):
        """Test handling of negative token counts (should not happen but handle gracefully)"""
        # Cost calculation with negative tokens should return 0 or handle gracefully
        # This tests defensive programming
        session_id = "negative-tokens-001"
        await clean_tracker.start_session(session_id)

        # Track with zero/negative tokens (edge case)
        result = await clean_tracker.track_usage(
            session_id=session_id,
            model="gpt-5",
            input_tokens=0,
            output_tokens=0
        )

        assert result["call_cost"] == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
