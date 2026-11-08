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
            getlist=lambda name: [token] if name == "token" else []
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
    release = None
    mode = "block"

    def __init__(self, **kwargs):
        self.handlers = {}
        self.started = asyncio.Event()
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
        await self.release.wait()


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
    BlockingRealtimeSession.release = asyncio.Event()
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tier", "rate_budget", "concurrent_cap"),
    [("free", 5, 1), ("premium", 20, 5), ("enterprise", 100, 20)],
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
    assert concurrent_rejection.sent[0]["reason"] == "concurrent_limit"
    assert concurrent_rejection.closes == [
        {"code": 1008, "reason": "WebSocket connection limit exceeded"}
    ]

    # The live sessions plus the rejected concurrent attempts share one
    # subject-scoped rolling budget.  Once the budget is exhausted, the real
    # handler reports rate_limit even though the concurrent cap is still full.
    for _ in range(rate_budget - concurrent_cap - 1):
        rejected = AdmissionWebSocket(token)
        await (await run_handler(rejected))
        assert rejected.sent[0]["reason"] == "concurrent_limit"

    rate_rejection = AdmissionWebSocket(token)
    await (await run_handler(rate_rejection))
    assert rate_rejection.sent[0]["reason"] == "rate_limit"
    assert rate_rejection.sent[0]["tier"] == tier
    assert rate_rejection.sent[0]["rate_limit"] == f"{rate_budget}/minute"

    BlockingRealtimeSession.release.set()
    await asyncio.gather(*live_tasks)
    assert main.websocket_connection_limiter.active_connections(f"jwt:{subject}") == 0


@pytest.mark.asyncio
async def test_real_handler_isolates_sessions_by_jwt_subject(
    admission_harness,
):
    first = await run_handler(
        AdmissionWebSocket(token_for("stable-subject", "free", "first@example.com"))
    )
    await wait_until(lambda: len(admission_harness.started) == 1)

    same_subject = AdmissionWebSocket(
        token_for("stable-subject", "free", "renamed@example.com")
    )
    await (await run_handler(same_subject))
    assert same_subject.sent[0]["reason"] == "concurrent_limit"

    different_subject = AdmissionWebSocket(
        token_for("different-subject", "free", "renamed@example.com")
    )
    other = await run_handler(different_subject)
    await wait_until(lambda: len(admission_harness.started) == 2)
    assert different_subject.sent == []

    BlockingRealtimeSession.release.set()
    await asyncio.gather(first, other)
    assert main.websocket_connection_limiter.active_connections("jwt:stable-subject") == 0
    assert main.websocket_connection_limiter.active_connections("jwt:different-subject") == 0


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
            BlockingRealtimeSession.release.set()
        await task

    assert main.websocket_connection_limiter.active_connections(f"jwt:{subject}") == 0
    assert admission_harness.ended == [admission_harness.started[0][0]]
