"""
WebSocket cost-protection lifecycle integration tests.

Drives the real /session handler (handle_media_stream) and the real
RealtimeSession loop with a scripted WebSocket, so cost enforcement is
exercised through the complete session lifecycle rather than against
SessionCostTracker in isolation:

1. session-start rejection (hourly limit / circuit breaker) — the connection
   is refused with `service_unavailable` + close 1013 before tracking starts
2. check_budget failures — a web_search tool call whose response usage pushes
   the session over its budget sends `budget_exceeded` and closes 1008
3. circuit-breaker activation — a mid-session usage spike trips the $100
   breaker, terminates the in-flight session and blocks new sessions
4. response token accounting — API response usage is accumulated in
   session_tokens/session_costs and relayed to the browser as
   ducke.backend_cost (the backend side of the client's response.done
   cost accounting)
5. duration limits — the in-handler watchdog closes the session with
   `session_timeout` + close 1008 once the configured duration is exceeded
6. exactly-once end_session cleanup — asserted for every termination path,
   including rejection paths that must NOT call it

The WebSocket stand-in follows the direct-invocation pattern used by
tests/test_tool_contracts.py: no network transport, fully deterministic.
"""
import asyncio
import contextlib
import os
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from prometheus_client import REGISTRY
from starlette.websockets import WebSocketDisconnect

os.environ.setdefault("OPENAI_API_KEY", "test-key")  # pragma: allowlist secret

import app.main as main  # noqa: E402  (import after env setup)
import app.middleware.auth as auth  # noqa: E402
from app.middleware.cost_protection import SessionCostTracker  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


class ScriptedWebSocket:
    """Minimal WebSocket stand-in driving the real /session handler.

    Records every send_json payload and close the server performs, and
    replays a scripted incoming message queue. Once the queue is drained the
    receive loop parks until end_input() simulates the browser disconnecting.
    """

    def __init__(self):
        self.sent = []
        self.closes = []
        self.headers = {
            "origin": "http://localhost:8000",
            "accept-language": "en-US",
        }
        self.client = None
        self.query_params = SimpleNamespace(getlist=lambda name: [], get=lambda name: None)
        self.scope = {}
        self._incoming = []
        self._idle = asyncio.Event()

    def script(self, *messages):
        """Queue client -> server messages for the session loop to consume."""
        self._incoming.extend(messages)

    def end_input(self):
        """Simulate the browser closing the connection."""
        self._idle.set()

    async def accept(self):
        pass

    async def send_json(self, payload):
        self.sent.append(payload)

    async def close(self, code=1000, reason=""):
        self.closes.append({"code": code, "reason": reason})

    async def receive_json(self):
        while self._incoming:
            message = self._incoming.pop(0)
            if isinstance(message, Exception):
                raise message
            return message
        await self._idle.wait()
        raise WebSocketDisconnect()

    def messages_of_type(self, msg_type):
        return [m for m in self.sent if m.get("type") == msg_type]

    def first_message(self, msg_type):
        matches = self.messages_of_type(msg_type)
        assert matches, f"expected a {msg_type!r} message, sent={self.sent}"
        return matches[0]


def tool_call(call_id="call-1", arguments='{"query": "latest AI news"}'):
    """A client -> server function-call message, as relayed by the WebRTC
    data channel for the web_search tool."""
    return {
        "type": "response.function_call_arguments.done",
        "name": "web_search",
        "call_id": call_id,
        "arguments": arguments,
    }


def search_client(input_tokens, output_tokens, text="Search results"):
    """An OpenAI client mock whose web_search response carries usage, the
    same shape the Responses API returns and the client's response.done
    accounting is based on."""
    client = MagicMock()
    client.responses.create.return_value = SimpleNamespace(
        output_text=text,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )
    return client


@pytest.fixture
def tracked_session(monkeypatch):
    """A real SessionCostTracker wired into app.main, instrumented with
    start/end spies so exactly-once cleanup can be asserted, plus a reset
    WebSocket admission limiter."""
    main.websocket_connection_limiter.reset()

    tracker = SessionCostTracker(redis_client=None)
    calls = {"start": [], "end": []}
    real_start = tracker.start_session
    real_end = tracker.end_session

    async def spy_start(session_id, *args):
        # Accepts (session_id) and (session_id, tier) so the suite runs
        # against both the committed handler and tier-aware variants.
        calls["start"].append(session_id)
        await real_start(session_id, *args)

    async def spy_end(session_id):
        calls["end"].append(session_id)
        await real_end(session_id)

    tracker.start_session = spy_start
    tracker.end_session = spy_end
    tracker.lifecycle_calls = calls

    monkeypatch.setattr(main, "cost_tracker", tracker)
    monkeypatch.setattr(
        main,
        "realtime_llm_config",
        {"config_list": [{"model": "gpt-realtime-2", "api_key": "test-key"}]},  # pragma: allowlist secret
    )

    yield tracker

    main.websocket_connection_limiter.reset()


@pytest.fixture
def ephemeral_key(monkeypatch):
    """Stub the OpenAI client_secrets call so RealtimeSession boots without
    network access and the session reaches its ducke.init message."""
    response = MagicMock()
    response.is_success = True
    response.json.return_value = {"value": "test-ephemeral-key"}
    response.raise_for_status = MagicMock()

    async def fake_post(*args, **kwargs):
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return response


async def start_session(ws):
    """Run the /session handler as a task against a scripted WebSocket."""
    return asyncio.create_task(main.handle_media_stream(ws))


def authenticated_socket(tier, monkeypatch):
    """Return a socket bearing a signed access JWT for the selected tier."""
    monkeypatch.setattr(auth, "JWT_SECRET_KEY", "test-jwt-secret-key")  # pragma: allowlist secret
    token = auth.create_access_token({"sub": f"test-{tier}", "tier": tier})
    ws = ScriptedWebSocket()
    ws.query_params = SimpleNamespace(
        getlist=lambda name: [token] if name == "token" else [],
        get=lambda name: token if name == "token" else None,
    )
    return ws


async def wait_for_message(ws, msg_type, timeout=5.0):
    """Poll the recorded sends until the server emits msg_type."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        matches = ws.messages_of_type(msg_type)
        if matches:
            return matches[0]
        await asyncio.sleep(0.01)
    raise AssertionError(f"server never sent {msg_type!r} within {timeout}s")


async def finish(ws, task, timeout=5.0):
    """Disconnect the client and wait for the handler to unwind."""
    ws.end_input()
    await asyncio.wait_for(task, timeout=timeout)


# ---------------------------------------------------------------------------
# 1. Session-start rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hourly_limit_rejects_session_before_tracking_starts(tracked_session):
    """A session attempted while hourly spend is over the $50 ceiling is
    refused before cost tracking begins — nothing is registered and there is
    nothing to clean up."""
    ws = ScriptedWebSocket()
    tracked_session.total_cost_last_hour = 55.0

    task = await start_session(ws)
    await asyncio.wait_for(task, timeout=5.0)

    rejection = ws.first_message("service_unavailable")
    assert rejection["reason"] == "hourly_limit"
    assert rejection["current_hourly_cost"] == 55.0
    assert "$50" in rejection["error"]
    assert ws.closes == [{"code": 1013, "reason": "Service temporarily unavailable"}]

    assert tracked_session.lifecycle_calls["start"] == []
    assert tracked_session.lifecycle_calls["end"] == []
    assert tracked_session.session_costs == {}


@pytest.mark.asyncio
async def test_circuit_breaker_at_connect_cleans_up_started_session(
    tracked_session, monkeypatch
):
    """The redundant post-start circuit-breaker check must release the
    tracking state it just created: the session is started, rejected with
    close 1013, and end_session runs exactly once."""
    ws = ScriptedWebSocket()
    await tracked_session.activate_circuit_breaker()

    # Simulate the breaker tripping between the admission check and the
    # redundant check (e.g. a concurrent session crossed the threshold).
    async def admission_passed(*args, **kwargs):
        return {"allowed": True, "reason": None, "message": "Session can be started"}

    tracked_session.can_start_new_session = admission_passed

    task = await start_session(ws)
    await asyncio.wait_for(task, timeout=5.0)

    rejection = ws.first_message("service_unavailable")
    assert rejection["circuit_breaker_active"] is True
    assert rejection["reset_time"] == (
        tracked_session.circuit_breaker_reset_time.isoformat()
    )
    assert ws.closes == [{"code": 1013, "reason": "Service temporarily unavailable"}]

    assert len(tracked_session.lifecycle_calls["start"]) == 1
    assert tracked_session.lifecycle_calls["end"] == tracked_session.lifecycle_calls["start"]
    assert tracked_session.session_costs == {}


# ---------------------------------------------------------------------------
# 2. check_budget failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tier", "tokens_each", "budget"),
    [
        ("free", 8_000_000, 5.0),
        ("premium", 28_000_000, 20.0),
        ("enterprise", 135_000_000, 100.0),
    ],
)
async def test_budget_exceeded_terminates_websocket_session(
    tracked_session, ephemeral_key, monkeypatch, tier, tokens_each, budget
):
    """Tracked API usage closes each tier at its JWT-selected budget."""
    tracked_session.config.max_total_cost_per_hour_usd = 1000.0
    tracked_session.config.circuit_breaker_threshold_usd = 1000.0
    monkeypatch.setattr(
        main,
        "openai_client",
        search_client(input_tokens=tokens_each, output_tokens=tokens_each),
    )  # gpt-5.4-nano: $0.75 per 1M paired input/output tokens

    ws = (
        ScriptedWebSocket()
        if tier == "free"
        else authenticated_socket(tier, monkeypatch)
    )
    ws.script(tool_call())
    task = await start_session(ws)

    exceeded = await wait_for_message(ws, "budget_exceeded")
    assert exceeded["session_cost"] == pytest.approx(tokens_each * 0.75 / 1_000_000)
    assert "Session budget limit exceeded" in exceeded["warnings"]
    assert exceeded["error"].startswith(f"Session budget limit of ${budget:.2f}")
    session_id = tracked_session.lifecycle_calls["start"][0]
    assert tracked_session.get_session_limits(session_id)["tier"] == tier

    await finish(ws, task)

    assert ws.closes == [{"code": 1008, "reason": "Budget limit exceeded"}]
    assert tracked_session.lifecycle_calls["start"] == tracked_session.lifecycle_calls["end"]
    assert len(tracked_session.lifecycle_calls["end"]) == 1
    assert tracked_session.session_costs == {}


# ---------------------------------------------------------------------------
# 3. response token accounting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_usage_tokens_are_accounted_and_relayed(
    tracked_session, ephemeral_key, monkeypatch
):
    """The usage carried by the API response is accumulated once in
    session_tokens/session_costs and relayed to the browser as
    ducke.backend_cost — the backend half of response.done cost accounting."""
    monkeypatch.setattr(
        main, "openai_client", search_client(input_tokens=100_000, output_tokens=200_000)
    )  # $0.135 at gpt-5.4-nano pricing, well inside the $5 budget

    gauge_before = REGISTRY.get_sample_value("active_sessions_total") or 0.0

    ws = ScriptedWebSocket()
    ws.script(tool_call())
    task = await start_session(ws)

    relayed = await wait_for_message(ws, "ducke.backend_cost")
    assert relayed == {
        "type": "ducke.backend_cost",
        "model": "gpt-5.4-nano",
        "input_tokens": 100_000,
        "output_tokens": 200_000,
    }

    # Accounting happens while the session is still live; end_session below
    # removes the per-session entries.
    session_id = tracked_session.lifecycle_calls["start"][0]
    assert tracked_session.session_tokens[session_id] == {
        "input": 100_000,
        "output": 200_000,
    }
    assert tracked_session.session_costs[session_id] == pytest.approx(0.135, abs=1e-6)
    assert tracked_session.total_cost_last_hour == pytest.approx(0.135, abs=1e-6)

    # The tool result still reaches the conversation so the realtime turn can
    # continue, and a clean session is never closed by enforcement.
    output_item = ws.first_message("conversation.item.create")
    assert output_item["item"]["call_id"] == "call-1"
    assert output_item["item"]["output"] == "Search results"
    assert ws.closes == []

    await finish(ws, task)

    assert tracked_session.lifecycle_calls["end"] == [session_id]
    assert tracked_session.session_costs == {}
    assert REGISTRY.get_sample_value("active_sessions_total") == pytest.approx(gauge_before)


# ---------------------------------------------------------------------------
# 4. Circuit-breaker activation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_trips_mid_session_and_blocks_new_sessions(
    tracked_session, ephemeral_key, monkeypatch
):
    """A usage spike crosses the breaker threshold mid-session: the in-flight
    session is terminated by the budget check, and the next connection is
    refused at session start with the breaker reason."""
    tracked_session.config.circuit_breaker_threshold_usd = 2.0
    monkeypatch.setattr(
        main, "openai_client", search_client(input_tokens=1_000_000, output_tokens=4_000_000)
    )  # $2.55 at gpt-5.4-nano pricing — inside the $5 session budget

    ws = ScriptedWebSocket()
    ws.script(tool_call(call_id="call-1"))
    task = await start_session(ws)

    exceeded = await wait_for_message(ws, "budget_exceeded")
    assert "System-wide circuit breaker is active" in exceeded["warnings"]
    assert tracked_session.circuit_breaker_active is True
    assert tracked_session.circuit_breaker_reset_time is not None
    assert ws.closes == [{"code": 1008, "reason": "Budget limit exceeded"}]

    await finish(ws, task)
    first_session = tracked_session.lifecycle_calls["start"][0]
    assert tracked_session.lifecycle_calls["end"] == [first_session]

    # A brand-new connection is denied while the breaker holds.
    ws2 = ScriptedWebSocket()
    task2 = await start_session(ws2)
    await asyncio.wait_for(task2, timeout=5.0)

    rejection = ws2.first_message("service_unavailable")
    assert rejection["reason"] == "circuit_breaker"
    assert rejection["reset_time"] == (
        tracked_session.circuit_breaker_reset_time.isoformat()
    )
    assert ws2.closes == [{"code": 1013, "reason": "Service temporarily unavailable"}]

    # Only the first session was ever tracked or cleaned up — exactly once.
    assert tracked_session.lifecycle_calls["start"] == [first_session]
    assert tracked_session.lifecycle_calls["end"] == [first_session]
    assert tracked_session.session_costs == {}


# ---------------------------------------------------------------------------
# 5. Duration limits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tier", "age_minutes", "limit_minutes"),
    [("free", 31, 30), ("premium", 121, 120), ("enterprise", 481, 480)],
)
async def test_duration_watchdog_closes_session_at_limit(
    tracked_session, ephemeral_key, monkeypatch, tier, age_minutes, limit_minutes
):
    """The watchdog closes each tier after its JWT-selected maximum duration."""
    real_sleep = asyncio.sleep
    gate = {"armed": False}

    async def gated_sleep(delay=None):
        # The watchdog polls every 10s; gate its first poll until the test has
        # backdated the session start. Everything else (test polling) just
        # yields.
        await real_sleep(0)
        if delay is not None and delay >= 1.0 and not gate["armed"]:
            while not gate["armed"]:
                await real_sleep(0.01)

    monkeypatch.setattr(asyncio, "sleep", gated_sleep)

    ws = (
        ScriptedWebSocket()
        if tier == "free"
        else authenticated_socket(tier, monkeypatch)
    )
    task = await start_session(ws)
    try:
        await wait_for_message(ws, "ducke.init")
        session_id = tracked_session.lifecycle_calls["start"][0]
        assert tracked_session.get_session_limits(session_id)["tier"] == tier

        tracked_session.session_start_times[session_id] = (
            datetime.utcnow() - timedelta(minutes=age_minutes)
        )
        gate["armed"] = True

        timeout_message = await wait_for_message(ws, "session_timeout")
        assert f"maximum duration of {limit_minutes} minutes" in timeout_message["error"]
        assert timeout_message["duration_seconds"] >= limit_minutes * 60
        assert ws.closes == [{"code": 1008, "reason": "Session duration exceeded"}]

        await finish(ws, task)

        assert tracked_session.lifecycle_calls["end"] == [session_id]
        assert tracked_session.session_costs == {}
    finally:
        # Unwind the input loop if an assertion fails before normal cleanup.
        ws.end_input()
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
