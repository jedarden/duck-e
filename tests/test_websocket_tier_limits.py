"""Behavioral tests for tier-specific WebSocket admission."""

import pytest

from app.middleware.rate_limiting import WebSocketConnectionLimiter


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tier", "rate_limit", "concurrent_limit"),
    [
        ("free", 5, 1),
        ("premium", 20, 5),
        ("enterprise", 100, 20),
    ],
)
async def test_each_tier_enforces_rate_and_concurrent_limits(
    tier, rate_limit, concurrent_limit
):
    limiter = WebSocketConnectionLimiter()
    identity = f"jwt:{tier}-user"

    active = []
    for _ in range(concurrent_limit):
        decision = await limiter.acquire(identity, tier)
        assert decision.allowed is True
        active.append(identity)

    concurrent = await limiter.acquire(identity, tier)
    assert concurrent.allowed is False
    assert concurrent.reason == "concurrent_limit"
    assert concurrent.max_concurrent_sessions == concurrent_limit

    for key in active:
        await limiter.release(key)

    # Releases free concurrent slots but do not erase connection-attempt
    # history, so the per-minute limit still applies.
    for _ in range(rate_limit - concurrent_limit):
        decision = await limiter.acquire(identity, tier)
        assert decision.allowed is True
        await limiter.release(identity)

    rate_limited = await limiter.acquire(identity, tier)
    assert rate_limited.allowed is False
    assert rate_limited.reason == "rate_limit"
    assert rate_limited.rate_limit == f"{rate_limit}/minute"


@pytest.mark.asyncio
async def test_user_counters_are_isolated_from_other_users():
    limiter = WebSocketConnectionLimiter()

    first_user = await limiter.acquire("jwt:user-a", "free")
    assert first_user.allowed is True

    same_user = await limiter.acquire("jwt:user-a", "free")
    assert same_user.allowed is False
    assert same_user.reason == "concurrent_limit"

    other_user = await limiter.acquire("jwt:user-b", "free")
    assert other_user.allowed is True
    assert other_user.current_connections == 1

    await limiter.release("jwt:user-a")
    await limiter.release("jwt:user-b")


@pytest.mark.asyncio
async def test_downgraded_token_cannot_bypass_existing_rate_history():
    limiter = WebSocketConnectionLimiter()
    identity = "jwt:user-being-downgraded"

    # A lower-tier token for the same subject must see the same usage history;
    # keying counters by (subject, tier) would incorrectly reset this budget.
    for _ in range(6):
        decision = await limiter.acquire(identity, "enterprise")
        assert decision.allowed is True
        await limiter.release(identity)

    downgraded = await limiter.acquire(identity, "free")
    assert downgraded.allowed is False
    assert downgraded.reason == "rate_limit"
    assert downgraded.tier == "free"
    assert downgraded.rate_limit == "5/minute"
    assert downgraded.max_concurrent_sessions == 1


@pytest.mark.asyncio
async def test_rate_window_expires_without_resetting_other_users():
    clock = FakeClock()
    limiter = WebSocketConnectionLimiter(clock=clock)

    for _ in range(5):
        decision = await limiter.acquire("jwt:window-user", "free")
        assert decision.allowed is True
        await limiter.release("jwt:window-user")

    assert (await limiter.acquire("jwt:window-user", "free")).reason == "rate_limit"

    clock.now = 60.1
    reopened = await limiter.acquire("jwt:window-user", "free")
    assert reopened.allowed is True
