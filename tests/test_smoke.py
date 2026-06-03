"""
Live smoke tests for DUCK-E — verifies all tools and features against the
deployed service.  Skipped when DUCK_E_URL is not set.

Run:
    DUCK_E_URL=https://duck-e-ducke-ardenone-cluster-ts.ardenone.com:8444 \
        pytest tests/test_smoke.py -v --timeout=60

Each test injects a real tool-call message over the backend WebSocket and
verifies the tool handler returns a non-error result, WITHOUT needing a
browser or microphone.
"""
import asyncio
import json
import os
import ssl
import uuid

import httpx
import pytest
import websockets

DUCK_E_URL = os.environ.get("DUCK_E_URL", "")
WS_URL = DUCK_E_URL.replace("https://", "wss://").replace("http://", "ws://") + "/session" if DUCK_E_URL else ""

pytestmark = pytest.mark.skipif(
    not DUCK_E_URL,
    reason="DUCK_E_URL not set — skipping live smoke tests",
)

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _open_session(extra_headers=None):
    """
    Connect to the backend WebSocket and return (ws, ag2_init_message).
    Does NOT establish a WebRTC peer — just confirms session init works.
    The Origin header must match ALLOWED_ORIGINS set on the deployed pod.
    """
    base = DUCK_E_URL.replace(":8444", "").replace("-ducke-ardenone-cluster-ts", "").replace(
        "duck-e-", "ducke.")
    # Derive the public origin from the deployment URL, fall back to known value
    origin = os.environ.get("DUCK_E_ORIGIN", "https://ducke.ardenone.com")
    headers = {
        "Origin": origin,
        "x-forwarded-user": "smoke-test",
        "x-forwarded-email": "smoke@test.internal",
        **(extra_headers or {}),
    }
    ws = await websockets.connect(
        WS_URL,
        ssl=SSL_CTX,
        additional_headers=headers,
        open_timeout=20,
    )
    raw = await asyncio.wait_for(ws.recv(), timeout=20)
    msg = json.loads(raw)
    return ws, msg


async def _call_tool(ws, name: str, args: dict) -> dict:
    """
    Inject a function_call_arguments.done message and return the
    conversation.item.create message that carries the tool output.
    """
    call_id = f"smoke-{uuid.uuid4().hex[:8]}"
    await ws.send(json.dumps({
        "type": "response.function_call_arguments.done",
        "name": name,
        "call_id": call_id,
        "arguments": json.dumps(args),
    }))
    # Read until we get the function_call_output for our call_id
    for _ in range(10):
        raw = await asyncio.wait_for(ws.recv(), timeout=30)
        msg = json.loads(raw)
        if (
            msg.get("type") == "conversation.item.create"
            and msg.get("item", {}).get("call_id") == call_id
        ):
            return msg
    raise TimeoutError(f"No tool result received for call_id={call_id}")


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------

class TestHTTPEndpoints:

    def test_health_endpoint(self):
        """Basic health check returns 200."""
        r = httpx.get(f"{DUCK_E_URL}/status", verify=False, timeout=10)
        assert r.status_code == 200

    def test_openai_health_endpoint(self):
        """Session creation against OpenAI succeeds and returns the running version."""
        r = httpx.get(f"{DUCK_E_URL}/health/openai", verify=False, timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok", f"Expected ok, got: {data}"
        assert "version" in data
        assert "model" in data


# ---------------------------------------------------------------------------
# Session initialisation
# ---------------------------------------------------------------------------

class TestSession:

    @pytest.mark.asyncio
    async def test_websocket_returns_ag2_init(self):
        """
        Backend WebSocket must respond with ag2.init containing an ephemeral key.
        This is the gating test — if it fails, all tool tests will also fail.
        """
        ws, msg = await _open_session()
        await ws.close()

        assert msg["type"] == "ag2.init", f"Expected ag2.init, got: {msg}"
        cfg = msg.get("config", {})
        assert "client_secret" in cfg, "ag2.init missing client_secret"
        assert cfg["client_secret"].get("value"), "Ephemeral key is empty"
        assert cfg.get("model"), "ag2.init missing model"


# ---------------------------------------------------------------------------
# Tool handler smoke tests
# (inject the tool-call message directly — no browser or microphone needed)
# ---------------------------------------------------------------------------

class TestTools:

    @pytest.mark.asyncio
    async def test_get_current_weather(self):
        """Weather tool returns current temperature for a known city."""
        ws, msg = await _open_session()
        assert msg["type"] == "ag2.init"
        try:
            result_msg = await _call_tool(ws, "get_current_weather", {"location": "New York"})
        finally:
            await ws.close()

        output = result_msg["item"]["output"]
        data = json.loads(output)
        assert "error" not in data, f"Weather tool error: {data}"
        assert "current" in data or "temperature" in str(data).lower(), \
            f"Expected weather data, got: {output[:200]}"

    @pytest.mark.asyncio
    async def test_get_weather_forecast(self):
        """Forecast tool returns daily forecast data."""
        ws, msg = await _open_session()
        assert msg["type"] == "ag2.init"
        try:
            result_msg = await _call_tool(ws, "get_weather_forecast", {"location": "London"})
        finally:
            await ws.close()

        output = result_msg["item"]["output"]
        data = json.loads(output)
        assert "error" not in data, f"Forecast tool error: {data}"
        assert "daily" in data, f"Expected daily forecast, got: {output[:200]}"

    @pytest.mark.asyncio
    async def test_web_search(self):
        """Web search tool returns non-empty results for a simple query."""
        ws, msg = await _open_session()
        assert msg["type"] == "ag2.init"
        try:
            result_msg = await _call_tool(ws, "web_search", {"query": "OpenAI Realtime API"})
        finally:
            await ws.close()

        output = result_msg["item"]["output"]
        assert len(output) > 50, f"Web search returned too little: {output[:200]}"
        assert "error" not in output.lower()[:100], f"Web search error: {output[:200]}"

    @pytest.mark.asyncio
    async def test_web_fetch(self):
        """Web fetch tool retrieves content from a public URL."""
        ws, msg = await _open_session()
        assert msg["type"] == "ag2.init"
        try:
            result_msg = await _call_tool(ws, "web_fetch", {"url": "https://httpbin.org/get"})
        finally:
            await ws.close()

        output = result_msg["item"]["output"]
        assert len(output) > 10, f"Web fetch returned too little: {output[:200]}"
        # Should not be an SSRF error or empty
        assert "blocked" not in output.lower() and "ssrf" not in output.lower(), \
            f"Unexpected block: {output[:200]}"

    @pytest.mark.asyncio
    async def test_save_and_recall_memory(self):
        """Memory tools can save a fact and retrieve it."""
        ws, msg = await _open_session()
        assert msg["type"] == "ag2.init"
        try:
            fact = f"smoke test fact {uuid.uuid4().hex[:6]}"
            save_msg = await _call_tool(ws, "save_memory", {"fact": fact})
            save_output = save_msg["item"]["output"]
            assert "saved" in save_output.lower() or "ok" in save_output.lower() or fact[:10] in save_output, \
                f"Save memory unexpected response: {save_output}"

            recall_msg = await _call_tool(ws, "recall_memories", {})
            recall_output = recall_msg["item"]["output"]
            assert fact in recall_output, \
                f"Fact not found in recall output: {recall_output[:400]}"
        finally:
            await ws.close()

    @pytest.mark.asyncio
    async def test_change_voice(self):
        """Voice change tool accepts a valid voice name without error."""
        ws, msg = await _open_session()
        assert msg["type"] == "ag2.init"
        try:
            result_msg = await _call_tool(ws, "change_voice", {"voice": "nova"})
        finally:
            await ws.close()

        output = result_msg["item"]["output"]
        assert "nova" in output.lower() or "voice" in output.lower(), \
            f"Unexpected voice change response: {output}"
        assert "error" not in output.lower() or "invalid" not in output.lower(), \
            f"Voice change error: {output}"

    @pytest.mark.asyncio
    async def test_change_voice_invalid_rejects(self):
        """Voice change tool rejects an invalid voice name."""
        ws, msg = await _open_session()
        assert msg["type"] == "ag2.init"
        try:
            result_msg = await _call_tool(ws, "change_voice", {"voice": "not_a_real_voice"})
        finally:
            await ws.close()

        output = result_msg["item"]["output"]
        assert "invalid" in output.lower() or "available" in output.lower(), \
            f"Expected rejection, got: {output}"
