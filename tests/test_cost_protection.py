"""
Unit tests for cost protection middleware
Tests budget enforcement, session tracking, and circuit breaker
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, AsyncMock
import redis.asyncio as redis

from app.middleware.cost_protection import (
    CostProtectionConfig,
    SessionCostTracker,
    get_cost_config,
    get_cost_tracker
)


class TestCostProtectionConfig:
    """Test cost protection configuration"""

    def test_default_config(self):
        """Test default configuration values"""
        config = CostProtectionConfig()

        assert config.enabled is True
        assert config.max_session_cost_usd == 5.0
        assert config.max_session_duration_minutes == 30
        assert config.max_total_cost_per_hour_usd == 50.0
        assert config.circuit_breaker_threshold_usd == 100.0

    def test_token_pricing(self):
        """Test token pricing configuration"""
        config = CostProtectionConfig()

        # Verify pricing per 1M tokens
        assert config.gpt5_input_cost_per_1m == 10.0
        assert config.gpt5_output_cost_per_1m == 30.0
        assert config.gpt5_mini_input_cost_per_1m == 3.0
        assert config.gpt5_mini_output_cost_per_1m == 15.0
        assert config.gpt_realtime_input_cost_per_1m == 100.0
        assert config.gpt_realtime_output_cost_per_1m == 200.0

    @patch.dict('os.environ', {
        'COST_PROTECTION_ENABLED': 'false',
        'COST_PROTECTION_MAX_SESSION_COST_USD': '10.0'
    })
    def test_config_from_env(self):
        """Test configuration loading from environment"""
        config = get_cost_config()

        assert config.enabled is False
        assert config.max_session_cost_usd == 10.0


class TestCostCalculation:
    """Test token cost calculation"""

    def test_gpt5_cost_calculation(self):
        """Test cost calculation for gpt-5"""
        tracker = SessionCostTracker()

        # 100k input, 200k output tokens
        cost = tracker.calculate_cost("gpt-5", 100_000, 200_000)

        # Expected: (100k/1M * $10) + (200k/1M * $30) = $1 + $6 = $7
        assert cost == 7.0

    def test_gpt5_mini_cost_calculation(self):
        """Test cost calculation for gpt-5-mini"""
        tracker = SessionCostTracker()

        # 1M input, 500k output tokens
        cost = tracker.calculate_cost("gpt-5-mini", 1_000_000, 500_000)

        # Expected: (1M/1M * $3) + (500k/1M * $15) = $3 + $7.5 = $10.5
        assert cost == 10.5

    def test_gpt_realtime_cost_calculation(self):
        """Test cost calculation for gpt-realtime"""
        tracker = SessionCostTracker()

        # 50k input, 100k output tokens
        cost = tracker.calculate_cost("gpt-realtime", 50_000, 100_000)

        # Expected: (50k/1M * $100) + (100k/1M * $200) = $5 + $20 = $25
        assert cost == 25.0

    def test_unknown_model_fallback(self):
        """Test that unknown models use gpt-5-mini pricing"""
        tracker = SessionCostTracker()

        # Unknown model should use gpt-5-mini pricing
        cost = tracker.calculate_cost("unknown-model", 100_000, 100_000)

        # Expected: (100k/1M * $3) + (100k/1M * $15) = $0.3 + $1.5 = $1.8
        assert cost == 1.8


@pytest.mark.asyncio
class TestSessionTracking:
    """Test session cost tracking"""

    async def test_start_session(self):
        """Test session initialization"""
        tracker = SessionCostTracker()
        session_id = "test-session-001"

        await tracker.start_session(session_id)

        assert session_id in tracker.session_costs
        assert tracker.session_costs[session_id] == 0.0
        assert session_id in tracker.session_start_times

    async def test_track_usage(self):
        """Test usage tracking and cost accumulation"""
        tracker = SessionCostTracker()
        session_id = "test-session-002"

        await tracker.start_session(session_id)

        # Track first usage
        usage1 = await tracker.track_usage(
            session_id=session_id,
            model="gpt-5-mini",
            input_tokens=10_000,
            output_tokens=20_000
        )

        # Expected cost: (10k/1M * $3) + (20k/1M * $15) = $0.03 + $0.30 = $0.33
        assert usage1["call_cost"] == pytest.approx(0.33, abs=0.01)
        assert usage1["session_cost"] == pytest.approx(0.33, abs=0.01)
        assert usage1["budget_ok"] is True

        # Track second usage
        usage2 = await tracker.track_usage(
            session_id=session_id,
            model="gpt-5-mini",
            input_tokens=5_000,
            output_tokens=10_000
        )

        # Expected additional cost: (5k/1M * $3) + (10k/1M * $15) = $0.015 + $0.15 = $0.165
        # Total: $0.33 + $0.165 = $0.495
        assert usage2["session_cost"] == pytest.approx(0.495, abs=0.01)

    async def test_token_accumulation(self):
        """Test that tokens are accumulated correctly"""
        tracker = SessionCostTracker()
        session_id = "test-session-003"

        await tracker.start_session(session_id)

        await tracker.track_usage(session_id, "gpt-5", 1000, 2000)
        await tracker.track_usage(session_id, "gpt-5", 500, 1000)

        # Total tokens
        assert tracker.session_tokens[session_id]["input"] == 1500
        assert tracker.session_tokens[session_id]["output"] == 3000

    async def test_end_session(self):
        """Test session cleanup"""
        tracker = SessionCostTracker()
        session_id = "test-session-004"

        await tracker.start_session(session_id)
        await tracker.track_usage(session_id, "gpt-5", 1000, 2000)
        await tracker.end_session(session_id)

        # Session should be cleaned up
        assert session_id not in tracker.session_costs
        assert session_id not in tracker.session_start_times
        assert session_id not in tracker.session_tokens


@pytest.mark.asyncio
class TestBudgetEnforcement:
    """Test budget limit enforcement"""

    async def test_budget_within_limit(self):
        """Test budget check when within limit"""
        tracker = SessionCostTracker()
        session_id = "test-session-005"

        await tracker.start_session(session_id)

        # Use $2 out of $5 limit
        usage = await tracker.track_usage(
            session_id=session_id,
            model="gpt-realtime",
            input_tokens=10_000,  # $1
            output_tokens=5_000   # $1
        )

        assert usage["budget_ok"] is True
        assert usage["remaining_budget_usd"] > 0

    async def test_budget_exceeded(self):
        """Test budget check when limit exceeded"""
        tracker = SessionCostTracker()
        session_id = "test-session-006"

        await tracker.start_session(session_id)

        # Use more than $5 limit
        usage = await tracker.track_usage(
            session_id=session_id,
            model="gpt-realtime",
            input_tokens=30_000,  # $3
            output_tokens=15_000  # $3 = total $6
        )

        assert usage["budget_ok"] is False
        assert usage["remaining_budget_usd"] <= 0
        assert len(usage["warnings"]) > 0

    async def test_budget_warnings(self):
        """Test warning messages for budget violations"""
        tracker = SessionCostTracker()
        session_id = "test-session-007"

        await tracker.start_session(session_id)

        # Exceed budget
        usage = await tracker.track_usage(
            session_id=session_id,
            model="gpt-5",
            input_tokens=500_000,   # $5
            output_tokens=100_000   # $3 = total $8
        )

        assert "Session budget limit exceeded" in usage["warnings"]


@pytest.mark.asyncio
class TestSessionDuration:
    """Test session duration limits"""

    async def test_duration_within_limit(self):
        """Test session within duration limit"""
        tracker = SessionCostTracker()
        session_id = "test-session-008"

        await tracker.start_session(session_id)

        # Check immediately (should be OK)
        budget_status = await tracker.check_budget(session_id, 1.0)

        assert budget_status["budget_ok"] is True
        assert budget_status["remaining_duration_seconds"] > 0

    async def test_duration_exceeded(self):
        """Test session exceeding duration limit"""
        tracker = SessionCostTracker()
        session_id = "test-session-009"

        await tracker.start_session(session_id)

        # Manually set start time to 31 minutes ago
        tracker.session_start_times[session_id] = datetime.utcnow() - timedelta(minutes=31)

        # Check budget (should fail due to duration)
        budget_status = await tracker.check_budget(session_id, 1.0)

        assert budget_status["budget_ok"] is False
        assert "Session duration limit exceeded" in budget_status["warnings"]


@pytest.mark.asyncio
class TestCircuitBreaker:
    """Test circuit breaker functionality"""

    async def test_activate_circuit_breaker(self):
        """Test circuit breaker activation"""
        tracker = SessionCostTracker()

        await tracker.activate_circuit_breaker()

        assert tracker.circuit_breaker_active is True
        assert tracker.circuit_breaker_reset_time is not None

    async def test_circuit_breaker_blocks_sessions(self):
        """Test that circuit breaker blocks new sessions"""
        tracker = SessionCostTracker()
        session_id = "test-session-010"

        await tracker.start_session(session_id)
        await tracker.activate_circuit_breaker()

        # Check budget with active circuit breaker
        budget_status = await tracker.check_budget(session_id, 1.0)

        assert budget_status["budget_ok"] is False
        assert budget_status["circuit_breaker_active"] is True

    async def test_circuit_breaker_reset(self):
        """Test circuit breaker automatic reset"""
        tracker = SessionCostTracker()

        await tracker.activate_circuit_breaker()

        # Manually set reset time to past
        tracker.circuit_breaker_reset_time = datetime.utcnow() - timedelta(minutes=1)

        # Check circuit breaker (should reset)
        await tracker.check_circuit_breaker()

        assert tracker.circuit_breaker_active is False
        assert tracker.circuit_breaker_reset_time is None


@pytest.mark.asyncio
class TestRedisIntegration:
    """Test Redis-backed session tracking"""

    async def test_session_storage_in_redis(self):
        """Test that session data is stored in Redis"""
        mock_redis = AsyncMock()
        tracker = SessionCostTracker(redis_client=mock_redis)
        session_id = "test-session-011"

        await tracker.start_session(session_id)

        # Verify Redis calls
        mock_redis.hset.assert_called_once()
        mock_redis.expire.assert_called_once()

    async def test_usage_update_in_redis(self):
        """Test that usage updates are persisted to Redis"""
        mock_redis = AsyncMock()
        tracker = SessionCostTracker(redis_client=mock_redis)
        session_id = "test-session-012"

        await tracker.start_session(session_id)
        await tracker.track_usage(session_id, "gpt-5", 1000, 2000)

        # Should have called hset for start and update
        assert mock_redis.hset.call_count == 2

    async def test_redis_failure_fallback(self):
        """Test that system continues working if Redis fails"""
        mock_redis = AsyncMock()
        mock_redis.hset.side_effect = redis.ConnectionError("Connection failed")

        tracker = SessionCostTracker(redis_client=mock_redis)
        session_id = "test-session-013"

        # Should not raise exception
        await tracker.start_session(session_id)

        # Should still track in memory
        assert session_id in tracker.session_costs


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
