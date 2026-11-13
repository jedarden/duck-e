"""Integration coverage for JWT-tier WebSocket admission.

These tests invoke the production ``handle_media_stream`` function directly,
including JWT validation, identity resolution, admission, and the handler's
cleanup decorator.  The realtime provider and cost tracker are replaced only
at their external boundaries so the admission path stays real and
deterministic.
"""

import asyncio
from datetime import datetime
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.websockets import WebSocketDisconnect

# ``app.main`` constructs the OpenAI client at import time, before pytest's
# session fixture runs.
os.environ.setdefault("OPENAI_API_KEY", "integration-test-key")

import app.main as main
import app.middleware.auth as auth


class AdmissionWebSocket:
    """Small ASGI WebSocket stand-in for direct handler integration tests."""

    def __init__(self, token: str):
        self.headers = {
            "origin": "http://localhost:8000",
            "accept-language": "en-US",
        }
        self.client = SimpleNamespace(host="127.0.0.1")
        self.query_params = SimpleNamespace(
            getlist=lambda name: [token] if name == "token" else [],
            get=lambda name: token if name == "token" else None,
        )
        self.scope = {}
        self.sent = []
        self.closes = []

    async def accept(self):
        return None

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self, code=1000, reason=""):
        self.closes.append({"code": code, "reason": reason})


class AdmissionCostTracker:
    """Minimal cost boundary that preserves handler lifecycle semantics."""

    def __init__(self):
        self.circuit_breaker_active = False
        self.circuit_breaker_reset_time = None
        self.session_start_times = {}
        self.started = []
        self.ended = []

    async def can_start_new_session(self):
        return {"allowed": True}

    async def start_session(self, session_id, tier="free"):
        self.started.append((session_id, tier))
        self.session_start_times[session_id] = datetime.utcnow()

    async def check_circuit_breaker(self):
        return None

    def get_session_limits(self, session_id):
        tier = next(
            tier for current_id, tier in self.started if current_id == session_id
        )
        return {
            "tier": tier,
            "max_session_cost_usd": 5.0,
            "max_session_duration_seconds": 30 * 60,
        }

    async def end_session(self, session_id):
        self.ended.append(session_id)
        self.session_start_times.pop(session_id, None)


class BlockingRealtimeSession:
    """Fake provider session whose run method can hold an admitted slot."""

    instances = []
    mode = "block"

    def __init__(self, **kwargs):
        self.handlers = {}
        self.started = asyncio.Event()
        self.release_event = asyncio.Event()
        type(self).instances.append(self)

    def register_tool(self, name, description, handler, parameters):
        self.handlers[name] = handler

    async def send_backend_cost(self, *args, **kwargs):
        return None

    async def run(self):
        self.started.set()
        if self.mode == "return":
            return None
        if self.mode == "disconnect":
            raise WebSocketDisconnect()
        if self.mode == "exception":
            raise RuntimeError("provider failed")
        await self.release_event.wait()

    @classmethod
    def release_all(cls):
        """Release every fake provider session currently holding a slot."""
        for instance in cls.instances:
            instance.release_event.set()


@pytest.fixture
def admission_harness(monkeypatch):
    tracker = AdmissionCostTracker()
    main.websocket_connection_limiter.reset()
    monkeypatch.setattr(main, "cost_tracker", tracker)
    monkeypatch.setattr(
        main,
        "realtime_llm_config",
        {"config_list": [{"model": "test-realtime", "api_key": "test-key"}]},
    )
    monkeypatch.setattr(
        main.ws_security,
        "validate_connection",
        AsyncMock(return_value=True),
    )

    class FakeMemoryStore:
        def __init__(self, user_id):
            self.user_id = user_id

        def load(self):
            return None

        async def get_or_generate_summary(self, api_key):
            return None

        def get_structured_facts(self):
            return []

    monkeypatch.setattr(main, "UserMemoryStore", FakeMemoryStore)
    BlockingRealtimeSession.instances = []
    BlockingRealtimeSession.mode = "block"
    monkeypatch.setattr(main, "RealtimeSession", BlockingRealtimeSession)
    monkeypatch.setattr(auth, "JWT_SECRET_KEY", "integration-jwt-secret")

    yield tracker

    main.websocket_connection_limiter.reset()


def token_for(subject: str, tier: str, email: str | None = None) -> str:
    claims = {"sub": subject, "tier": tier}
    if email is not None:
        claims["email"] = email
    return auth.create_access_token(claims)


async def wait_until(predicate, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not reached before timeout")
        await asyncio.sleep(0)


async def run_handler(websocket):
    return asyncio.create_task(main.handle_media_stream(websocket))


def assert_limit_rejection(
    websocket,
    *,
    tier,
    subject,
    reason,
    rate_limit,
    concurrent_limit,
    current_connections,
):
    """Assert a rejection with diagnostics for the tier and subject under test."""
    context = (
        f"tier={tier} subject={subject} rate_limit={rate_limit}/minute "
        f"concurrent_limit={concurrent_limit}"
    )
    assert websocket.sent, f"{context}: admission response was not sent"
    payload = websocket.sent[0]
    assert payload["reason"] == reason, f"{context}: payload={payload}"
    assert payload["tier"] == tier, f"{context}: payload={payload}"
    assert payload["rate_limit"] == f"{rate_limit}/minute", (
        f"{context}: payload={payload}"
    )
    assert payload["max_concurrent_sessions"] == concurrent_limit, (
        f"{context}: payload={payload}"
    )
    assert payload["current_connections"] == current_connections, (
        f"{context}: payload={payload}"
    )


TIER_LIMIT_CASES = [
    pytest.param("free", 5, 1, id="free-5-per-minute-1-concurrent"),
    pytest.param("premium", 20, 5, id="premium-20-per-minute-5-concurrent"),
    pytest.param(
        "enterprise", 100, 20, id="enterprise-100-per-minute-20-concurrent"
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tier", "rate_budget", "concurrent_cap"),
    TIER_LIMIT_CASES,
)
async def test_real_handler_enforces_tier_admission_and_rolling_budget(
    admission_harness, tier, rate_budget, concurrent_cap
):
    subject = f"handler-admission-{tier}"
    token = token_for(subject, tier)
    live_tasks = []

    for _ in range(concurrent_cap):
        live_tasks.append(await run_handler(AdmissionWebSocket(token)))
    await wait_until(
        lambda: len(admission_harness.started) == concurrent_cap
    )

    concurrent_rejection = AdmissionWebSocket(token)
    rejection_task = await run_handler(concurrent_rejection)
    await rejection_task
    assert_limit_rejection(
        concurrent_rejection,
        tier=tier,
        subject=subject,
        reason="concurrent_limit",
        rate_limit=rate_budget,
        concurrent_limit=concurrent_cap,
        current_connections=concurrent_cap,
    )
    assert concurrent_rejection.closes == [
        {"code": 1008, "reason": "WebSocket connection limit exceeded"}
    ]

    # The live sessions plus the rejected concurrent attempts share one
    # subject-scoped rolling budget.  Once the budget is exhausted, the real
    # handler reports rate_limit even though the concurrent cap is still full.
    for _ in range(rate_budget - concurrent_cap - 1):
        rejected = AdmissionWebSocket(token)
        await (await run_handler(rejected))
        assert_limit_rejection(
            rejected,
            tier=tier,
            subject=subject,
            reason="concurrent_limit",
            rate_limit=rate_budget,
            concurrent_limit=concurrent_cap,
            current_connections=concurrent_cap,
        )

    rate_rejection = AdmissionWebSocket(token)
    await (await run_handler(rate_rejection))
    assert_limit_rejection(
        rate_rejection,
        tier=tier,
        subject=subject,
        reason="rate_limit",
        rate_limit=rate_budget,
        concurrent_limit=concurrent_cap,
        current_connections=concurrent_cap,
    )

    BlockingRealtimeSession.release_all()
    await asyncio.gather(*live_tasks)
    assert main.websocket_connection_limiter.active_connections(f"jwt:{subject}") == 0


@pytest.mark.asyncio
async def test_real_handler_subjects_cannot_consume_or_release_one_anothers_quota(
    admission_harness,
):
    first_subject = "stable-subject"
    second_subject = "different-subject"
    first = await run_handler(
        AdmissionWebSocket(token_for(first_subject, "free", "first@example.com"))
    )
    await wait_until(lambda: len(admission_harness.started) == 1)
    first_session = BlockingRealtimeSession.instances[0]

    same_subject = AdmissionWebSocket(
        token_for(first_subject, "free", "renamed@example.com")
    )
    await (await run_handler(same_subject))
    assert_limit_rejection(
        same_subject,
        tier="free",
        subject=first_subject,
        reason="concurrent_limit",
        rate_limit=5,
        concurrent_limit=1,
        current_connections=1,
    )

    different_subject = AdmissionWebSocket(
        token_for(second_subject, "free", "renamed@example.com")
    )
    other = await run_handler(different_subject)
    await wait_until(lambda: len(admission_harness.started) == 2)
    assert different_subject.sent == []
    second_session = BlockingRealtimeSession.instances[1]

    # Releasing the first subject must not release the second subject's slot.
    first_session.release_event.set()
    await first
    assert main.websocket_connection_limiter.active_connections(
        f"jwt:{first_subject}"
    ) == 0
    assert main.websocket_connection_limiter.active_connections(
        f"jwt:{second_subject}"
    ) == 1

    second_still_full = AdmissionWebSocket(
        token_for(second_subject, "free", "another@example.com")
    )
    await (await run_handler(second_still_full))
    assert_limit_rejection(
        second_still_full,
        tier="free",
        subject=second_subject,
        reason="concurrent_limit",
        rate_limit=5,
        concurrent_limit=1,
        current_connections=1,
    )

    # The first subject can use its released slot without borrowing the
    # second subject's quota, while the second session remains live.
    first_again = AdmissionWebSocket(token_for(first_subject, "free"))
    first_again_task = await run_handler(first_again)
    await wait_until(lambda: len(admission_harness.started) == 3)
    first_again_session = BlockingRealtimeSession.instances[2]
    first_again_session.release_event.set()
    await first_again_task
    assert main.websocket_connection_limiter.active_connections(
        f"jwt:{second_subject}"
    ) == 1

    second_session.release_event.set()
    await other
    assert main.websocket_connection_limiter.active_connections(
        f"jwt:{first_subject}"
    ) == 0
    assert main.websocket_connection_limiter.active_connections(
        f"jwt:{second_subject}"
    ) == 0


@pytest.mark.asyncio
async def test_real_handler_downgrade_applies_lower_rolling_limit_immediately(
    admission_harness,
):
    subject = "rolling-downgrade-subject"
    BlockingRealtimeSession.mode = "return"

    # Six attempts fit enterprise's 100/minute budget but exceed free's
    # 5/minute budget. Each completed session must release its concurrent slot
    # before the next attempt so this isolates rolling-limit downgrade logic.
    for attempt in range(6):
        websocket = AdmissionWebSocket(token_for(subject, "enterprise"))
        await (await run_handler(websocket))
        assert websocket.sent == [], (
            f"tier=enterprise subject={subject} attempt={attempt + 1} "
            "unexpected rejection"
        )

    downgraded = AdmissionWebSocket(token_for(subject, "free"))
    await (await run_handler(downgraded))
    assert_limit_rejection(
        downgraded,
        tier="free",
        subject=subject,
        reason="rate_limit",
        rate_limit=5,
        concurrent_limit=1,
        current_connections=0,
    )
    assert main.websocket_connection_limiter.active_connections(f"jwt:{subject}") == 0


@pytest.mark.asyncio
async def test_real_handler_downgrade_applies_lower_concurrent_limit_immediately(
    admission_harness,
):
    subject = "concurrent-downgrade-subject"
    premium = await run_handler(AdmissionWebSocket(token_for(subject, "premium")))
    await wait_until(lambda: len(admission_harness.started) == 1)
    premium_session = BlockingRealtimeSession.instances[0]

    downgraded = AdmissionWebSocket(token_for(subject, "free"))
    await (await run_handler(downgraded))
    assert_limit_rejection(
        downgraded,
        tier="free",
        subject=subject,
        reason="concurrent_limit",
        rate_limit=5,
        concurrent_limit=1,
        current_connections=1,
    )
    assert main.websocket_connection_limiter.active_connections(f"jwt:{subject}") == 1

    # Releasing the pre-downgrade session removes the old active slot. The
    # lower-tier subject can then connect immediately, without stale allowance
    # or leaked capacity from the higher-tier session.
    premium_session.release_event.set()
    await premium
    assert main.websocket_connection_limiter.active_connections(f"jwt:{subject}") == 0

    after_cleanup = await run_handler(AdmissionWebSocket(token_for(subject, "free")))
    await wait_until(lambda: len(admission_harness.started) == 2)
    after_cleanup_session = BlockingRealtimeSession.instances[1]
    after_cleanup_session.release_event.set()
    await after_cleanup
    assert main.websocket_connection_limiter.active_connections(f"jwt:{subject}") == 0


@pytest.mark.asyncio
async def test_real_handler_cleanup_before_downgrade_does_not_leak_capacity(
    admission_harness,
):
    subject = "cleanup-before-downgrade-subject"
    premium = await run_handler(AdmissionWebSocket(token_for(subject, "premium")))
    await wait_until(lambda: len(admission_harness.started) == 1)
    premium_session = BlockingRealtimeSession.instances[0]
    premium_session.release_event.set()
    await premium
    assert main.websocket_connection_limiter.active_connections(f"jwt:{subject}") == 0

    free = await run_handler(AdmissionWebSocket(token_for(subject, "free")))
    await wait_until(lambda: len(admission_harness.started) == 2)
    free_session = BlockingRealtimeSession.instances[1]
    free_session.release_event.set()
    await free
    assert main.websocket_connection_limiter.active_connections(f"jwt:{subject}") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["return", "disconnect", "exception", "cancel"])
async def test_real_handler_releases_admission_on_every_exit_path(
    admission_harness, mode
):
    subject = f"cleanup-{mode}"
    websocket = AdmissionWebSocket(token_for(subject, "free"))
    BlockingRealtimeSession.mode = "block" if mode == "cancel" else mode
    task = await run_handler(websocket)
    await wait_until(lambda: len(admission_harness.started) == 1)

    if mode == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        if mode == "block":
            BlockingRealtimeSession.release_all()
        await task

    assert main.websocket_connection_limiter.active_connections(f"jwt:{subject}") == 0
    assert admission_harness.ended == [admission_harness.started[0][0]]
