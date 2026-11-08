"""
Unit tests for cost cap enforcement
Tests new functionality: circuit breaker auto-activation, hourly limits, gpt-5.4-nano pricing,
and tier-specific session budgets and timeouts (anonymous/premium/enterprise)
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
import redis.asyncio as redis

from app.middleware.cost_protection import (
    SessionCostTracker,
    get_cost_config
)
from app.models.user import TIER_CONFIGURATIONS, UserTier


@pytest.mark.asyncio
class TestCircuitBreakerAutoActivation:
    """Test circuit breaker activates automatically when threshold is crossed"""

    async def test_circuit_breaker_activates_at_threshold(self):
        """
        CRITICAL TEST: Circuit breaker must auto-activate when total cost crosses $100
        This was previously broken - activate_circuit_breaker had zero callers
        """
        tracker = SessionCostTracker()

        # Verify circuit breaker starts inactive
        assert tracker.circuit_breaker_active is False

        # Simulate 20 sessions each spending $5 (total $100)
        for i in range(20):
            session_id = f"session-{i:03d}"
            await tracker.start_session(session_id)

            # Each session uses exactly $5.00
            await tracker.track_usage(
                session_id=session_id,
                model="gpt-5",
                input_tokens=50_000,  # $0.50
                output_tokens=150_000  # $4.50 = $5.00 total
            )

        # Verify circuit breaker activated automatically
        assert tracker.circuit_breaker_active is True, \
            "Circuit breaker should be active after $100 threshold crossed"
        assert tracker.circuit_breaker_reset_time is not None, \
            "Circuit breaker should have a reset time"

    async def test_circuit_breaker_blocks_new_sessions_after_activation(self):
        """
        Test that new sessions are rejected after circuit breaker activates
        Uses can_start_new_session method
        """
        tracker = SessionCostTracker()

        # First, activate circuit breaker by crossing threshold
        for i in range(20):
            session_id = f"session-{i:03d}"
            await tracker.start_session(session_id)
            await tracker.track_usage(
                session_id=session_id,
                model="gpt-5",
                input_tokens=50_000,
                output_tokens=150_000
            )

        assert tracker.circuit_breaker_active is True

        # Try to start a new session - should be rejected
        can_start = await tracker.can_start_new_session()

        assert can_start["allowed"] is False, \
            "New sessions should be rejected when circuit breaker is active"
        assert can_start["reason"] == "circuit_breaker", \
            "Rejection reason should be circuit_breaker"
        assert "reset_time" in can_start, \
            "Response should include reset time"

    async def test_circuit_breaker_resets_after_cooldown(self):
        """Test that circuit breaker can be reset after cooldown period"""
        tracker = SessionCostTracker()

        # Activate circuit breaker
        await tracker.activate_circuit_breaker()
        assert tracker.circuit_breaker_active is True

        # Set reset time to past (simulate cooldown)
        tracker.circuit_breaker_reset_time = datetime.utcnow() - timedelta(minutes=1)

        # Check circuit breaker (should auto-reset)
        await tracker.check_circuit_breaker()

        assert tracker.circuit_breaker_active is False, \
            "Circuit breaker should reset after cooldown"
        assert tracker.circuit_breaker_reset_time is None, \
            "Reset time should be cleared"


@pytest.mark.asyncio
class TestHourlyCostLimits:
    """Test hourly cost tracking and limits ($50/hour)"""

    async def test_hourly_cost_accumulates(self):
        """
        Test that hourly cost is accumulated across all sessions
        This was previously broken - total_cost_last_hour was never updated
        """
        tracker = SessionCostTracker()

        # Initially zero
        assert tracker.total_cost_last_hour == 0.0

        # Create multiple sessions that add up to hourly cost
        session1 = "hourly-001"
        session2 = "hourly-002"

        await tracker.start_session(session1)
        await tracker.track_usage(
            session_id=session1,
            model="gpt-5",
            input_tokens=100_000,  # $1.00
            output_tokens=300_000  # $9.00 = $10.00 total
        )

        # Verify hourly cost updated
        assert tracker.total_cost_last_hour == 10.0, \
            "Hourly cost should accumulate to $10.00"

        await tracker.start_session(session2)
        await tracker.track_usage(
            session_id=session2,
            model="gpt-5",
            input_tokens=200_000,  # $2.00
            output_tokens=600_000  # $18.00 = $20.00 total
        )

        # Verify hourly cost accumulated to $30.00
        assert tracker.total_cost_last_hour == 30.0, \
            "Hourly cost should accumulate to $30.00 across sessions"

    async def test_hourly_limit_blocks_new_sessions(self):
        """
        Test that hourly limit ($50) blocks new sessions when exceeded
        This was previously broken - max_total_cost_per_hour_usd was never enforced
        """
        tracker = SessionCostTracker()

        # Spend $45 (under $50 limit)
        for i in range(9):
            session_id = f"hourly-{i:03d}"
            await tracker.start_session(session_id)
            await tracker.track_usage(
                session_id=session_id,
                model="gpt-5",
                input_tokens=50_000,  # $0.50
                output_tokens=150_000  # $4.50 = $5.00 each
            )

        assert tracker.total_cost_last_hour == 45.0

        # Should still be allowed (under $50 limit)
        can_start = await tracker.can_start_new_session()
        assert can_start["allowed"] is True, \
            "New sessions should be allowed when hourly cost is $45 (< $50 limit)"

        # Spend another $10 (total $55, exceeds $50 limit)
        session_id = "hourly-over-010"
        await tracker.start_session(session_id)
        await tracker.track_usage(
            session_id=session_id,
            model="gpt-5",
            input_tokens=100_000,  # $1.00
            output_tokens=300_000  # $9.00 = $10.00 total
        )

        assert tracker.total_cost_last_hour == 55.0

        # Should now be rejected (exceeds $50 limit)
        can_start = await tracker.can_start_new_session()
        assert can_start["allowed"] is False, \
            "New sessions should be rejected when hourly cost exceeds $50"
        assert can_start["reason"] == "hourly_limit", \
            "Rejection reason should be hourly_limit"
        assert "$50" in can_start["message"], \
            "Message should mention the $50 limit"


@pytest.mark.asyncio
class TestGPT54NanoPricing:
    """Test gpt-5.4-nano model pricing - this was previously broken (unknown model warning)"""

    def test_gpt_5_4_nano_cost_calculation(self):
        """
        Test that gpt-5.4-nano returns correct pricing without unknown-model warning
        This was previously broken - gpt-5.4-nano fell into unknown-model branch
        """
        tracker = SessionCostTracker()

        # Calculate cost for gpt-5.4-nano
        # Pricing: $0.15/M input, $0.60/M output
        cost = tracker.calculate_cost("gpt-5.4-nano", 100_000, 200_000)

        # Expected: (100k/1M * $0.15) + (200k/1M * $0.60) = $0.015 + $0.12 = $0.135
        expected = 0.135
        tolerance = expected * 0.01  # 1% tolerance

        assert cost == pytest.approx(expected, abs=tolerance), \
            f"gpt-5.4-nano cost should be ${expected:.4f}, got ${cost:.4f}"

    def test_gpt_5_4_nano_no_warning_logged(self, caplog):
        """
        Test that gpt-5.4-nano does not log unknown-model warning
        Previously it would fall into the else branch and log a warning
        """
        import logging
        tracker = SessionCostTracker()

        with caplog.at_level(logging.WARNING):
            cost = tracker.calculate_cost("gpt-5.4-nano", 1000, 2000)

        # Should not log "Unknown model" warning
        unknown_model_warnings = [
            record for record in caplog.records
            if "Unknown model" in record.message
        ]

        assert len(unknown_model_warnings) == 0, \
            "gpt-5.4-nano should not log unknown-model warning"

    def test_gpt_realtime_2_pricing(self):
        """Test gpt-realtime-2 model pricing (also newly added)"""
        tracker = SessionCostTracker()

        # Calculate cost for gpt-realtime-2
        cost = tracker.calculate_cost("gpt-realtime-2", 50_000, 100_000)

        # Expected: (50k/1M * $100) + (100k/1M * $200) = $5 + $20 = $25
        expected = 25.0
        tolerance = expected * 0.01

        assert cost == pytest.approx(expected, abs=tolerance), \
            f"gpt-realtime-2 cost should be ${expected:.2f}, got ${cost:.2f}"


@pytest.mark.asyncio
class TestBudgetExtermination:
    """Test that sessions are terminated when budget is exceeded"""

    async def test_session_exceeds_budget_limit(self):
        """
        Test that when a session exceeds $5 budget, budget_ok becomes False
        This was previously broken - budget_ok was returned but always ignored by callers
        """
        tracker = SessionCostTracker()
        session_id = "budget-exceed-001"

        await tracker.start_session(session_id)

        # Spend $6 (exceeds $5 limit)
        usage = await tracker.track_usage(
            session_id=session_id,
            model="gpt-5",
            input_tokens=60_000,  # $0.60
            output_tokens=180_000  # $5.40 = $6.00 total
        )

        assert usage["budget_ok"] is False, \
            "budget_ok should be False when $5 limit exceeded"
        assert usage["session_cost"] > 5.0, \
            "Session cost should be greater than $5"
        assert "Session budget limit exceeded" in usage["warnings"], \
            "Warnings should mention budget limit exceeded"

    async def test_hourly_limit_included_in_budget_check(self):
        """
        Test that hourly limit is checked in budget_status
        This was previously broken - hourly_ok was not computed
        """
        tracker = SessionCostTracker()
        session_id = "hourly-check-001"

        await tracker.start_session(session_id)

        # Spend enough to exceed hourly limit
        tracker.total_cost_last_hour = 55.0  # Manually set to exceed $50 limit

        budget_status = await tracker.check_budget(session_id, 1.0)

        assert budget_status["budget_ok"] is False, \
            "budget_ok should be False when hourly limit exceeded"
        assert budget_status["hourly_cost_usd"] == 55.0, \
            "hourly_cost_usd should be reported"
        assert budget_status["max_hourly_cost_usd"] == 50.0, \
            "max_hourly_cost_usd should be reported"
        assert "Hourly cost limit exceeded" in budget_status["warnings"], \
            "Warnings should mention hourly limit exceeded"


@pytest.mark.asyncio
class TestTierSessionLimits:
    """Enforce the per-tier session budgets and timeouts advertised in
    docs/API_AUTHENTICATION.md: free $5/30min, premium $20/2h, enterprise
    $100/8h. Anonymous sessions are the free tier."""

    # gpt-5 pricing: $10 per 1M input tokens, so `spend * 100_000` input
    # tokens with zero output tokens costs exactly `spend` dollars.
    @staticmethod
    async def _track_spend(tracker: SessionCostTracker, session_id: str, spend_usd: float):
        return await tracker.track_usage(
            session_id=session_id,
            model="gpt-5",
            input_tokens=int(spend_usd * 100_000),
            output_tokens=0,
        )

    @staticmethod
    def _isolate_tier_budget(tracker: SessionCostTracker) -> None:
        """Raise system-wide controls so only the tier budget is under test.

        Circuit breaker and hourly caps have their own test classes; leaving
        them at defaults would trip before enterprise-sized spends do.
        """
        tracker.config.max_total_cost_per_hour_usd = 10_000.0
        tracker.config.circuit_breaker_threshold_usd = 100_000.0

    @pytest.mark.parametrize(
        ("tier", "budget", "timeout_seconds"),
        [
            ("free", 5.0, 30 * 60),
            ("premium", 20.0, 2 * 60 * 60),
            ("enterprise", 100.0, 8 * 60 * 60),
        ],
    )
    async def test_session_limits_match_advertised_tier_values(
        self, tier, budget, timeout_seconds
    ):
        tracker = SessionCostTracker()
        session_id = f"tier-limits-{tier}"
        await tracker.start_session(session_id, tier)

        limits = tracker.get_session_limits(session_id)

        assert limits == {
            "tier": tier,
            "max_session_cost_usd": budget,
            "max_session_duration_seconds": timeout_seconds,
        }

    @pytest.mark.parametrize(
        ("tier", "budget"),
        [
            ("free", 5.0),
            ("premium", 20.0),
            ("enterprise", 100.0),
        ],
    )
    async def test_tier_session_budget_is_enforced(self, tier, budget):
        """A session survives spend just under its tier budget and is cut
        off once that tier budget is crossed."""
        tracker = SessionCostTracker()
        self._isolate_tier_budget(tracker)
        session_id = f"tier-budget-{tier}"
        await tracker.start_session(session_id, tier)

        usage = await self._track_spend(tracker, session_id, budget - 1.0)

        assert usage["budget_ok"] is True, (
            f"{tier} session must survive ${budget - 1.0:.2f} of its ${budget:.2f} budget"
        )
        assert usage["tier"] == tier
        assert usage["max_session_cost_usd"] == budget
        assert usage["remaining_budget_usd"] == pytest.approx(1.0)

        usage = await self._track_spend(tracker, session_id, 2.0)

        assert usage["session_cost"] >= budget
        assert usage["budget_ok"] is False
        assert "Session budget limit exceeded" in usage["warnings"]

    async def test_anonymous_session_defaults_to_free_budget(self):
        """Anonymous sessions (no tier claim) are the free tier: cut off at
        the $5 budget, not granted premium or enterprise spend."""
        tracker = SessionCostTracker()
        self._isolate_tier_budget(tracker)
        session_id = "anonymous-budget"
        await tracker.start_session(session_id, "free")

        usage = await self._track_spend(tracker, session_id, 6.0)

        assert usage["tier"] == "free"
        assert usage["session_cost"] >= 5.0
        assert usage["budget_ok"] is False
        assert "Session budget limit exceeded" in usage["warnings"]

    async def test_premium_session_not_capped_at_free_budget(self):
        """Regression for the flat $5 cap: a premium session's $10 spend must
        be allowed, while the same spend exceeds a free session's budget."""
        tracker = SessionCostTracker()
        self._isolate_tier_budget(tracker)

        await tracker.start_session("contrast-premium", "premium")
        premium_usage = await self._track_spend(tracker, "contrast-premium", 10.0)
        assert premium_usage["budget_ok"] is True

        await tracker.start_session("contrast-free", "free")
        free_usage = await self._track_spend(tracker, "contrast-free", 10.0)
        assert free_usage["budget_ok"] is False
        assert "Session budget limit exceeded" in free_usage["warnings"]

    async def test_enterprise_session_not_capped_at_premium_budget(self):
        """A $50 spend fits an enterprise budget but exceeds a premium one."""
        tracker = SessionCostTracker()
        self._isolate_tier_budget(tracker)

        await tracker.start_session("contrast-enterprise", "enterprise")
        enterprise_usage = await self._track_spend(tracker, "contrast-enterprise", 50.0)
        assert enterprise_usage["budget_ok"] is True

        await tracker.start_session("contrast-premium", "premium")
        premium_usage = await self._track_spend(tracker, "contrast-premium", 50.0)
        assert premium_usage["budget_ok"] is False
        assert premium_usage["max_session_cost_usd"] == 20.0
        assert "Session budget limit exceeded" in premium_usage["warnings"]

    @pytest.mark.parametrize(
        ("tier", "age_minutes", "within_limit"),
        [
            ("free", 29, True),
            ("free", 31, False),
            ("premium", 31, True),
            ("premium", 121, False),
            ("enterprise", 121, True),
            ("enterprise", 481, False),
        ],
    )
    async def test_tier_session_duration_is_enforced(
        self, tier, age_minutes, within_limit
    ):
        """Session age is judged against the tier's advertised timeout."""
        tracker = SessionCostTracker()
        session_id = f"tier-duration-{tier}"
        await tracker.start_session(session_id, tier)
        tracker.session_start_times[session_id] = (
            datetime.utcnow() - timedelta(minutes=age_minutes)
        )

        status = await tracker.check_budget(session_id, 1.0)

        assert status["tier"] == tier
        assert status["max_session_duration_seconds"] == (
            TIER_CONFIGURATIONS[UserTier(tier)].session_timeout
        )
        assert status["budget_ok"] is within_limit
        if within_limit:
            assert status["remaining_duration_seconds"] > 0
        else:
            assert "Session duration limit exceeded" in status["warnings"]
            assert status["remaining_duration_seconds"] == 0

    async def test_unrecognized_tier_falls_back_to_configured_limits(self):
        """A tier the model does not know must never yield unlimited spend:
        enforcement falls back to the globally configured session limits."""
        tracker = SessionCostTracker()
        session_id = "unrecognized-tier"
        await tracker.start_session(session_id, "platinum")

        limits = tracker.get_session_limits(session_id)

        assert limits["tier"] is None
        assert limits["max_session_cost_usd"] == tracker.config.max_session_cost_usd
        assert limits["max_session_duration_seconds"] == (
            tracker.config.max_session_duration_minutes * 60
        )

        configured_cap = tracker.config.max_session_cost_usd
        usage = await self._track_spend(tracker, session_id, configured_cap + 1.0)
        assert usage["budget_ok"] is False
        assert "Session budget limit exceeded" in usage["warnings"]

    async def test_untagged_session_uses_configured_limits(self):
        """Sessions started without a tier (legacy callers) keep the globally
        configured limits rather than gaining a tier's higher allowance."""
        tracker = SessionCostTracker()
        session_id = "untagged-session"

        await tracker.start_session(session_id)
        limits = tracker.get_session_limits(session_id)

        assert limits["tier"] is None
        assert limits["max_session_cost_usd"] == tracker.config.max_session_cost_usd
        assert limits["max_session_duration_seconds"] == (
            tracker.config.max_session_duration_minutes * 60
        )

    async def test_end_session_clears_tier_limits(self):
        tracker = SessionCostTracker()
        session_id = "ended-tier-session"
        await tracker.start_session(session_id, "enterprise")
        assert tracker.get_session_limits(session_id)["tier"] == "enterprise"

        await tracker.end_session(session_id)

        assert session_id not in tracker.session_tiers
        limits = tracker.get_session_limits(session_id)
        assert limits["tier"] is None
        assert limits["max_session_cost_usd"] == tracker.config.max_session_cost_usd


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
