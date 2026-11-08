"""Contract and failure-path tests for server-side Realtime tools."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import httpx
import pytest
import pytest_asyncio


class FakeSession:
    """Capture the tools registered by handle_media_stream without a provider."""

    def __init__(self):
        self.tools = []
        self.tool_handlers = {}
        self.change_voice = AsyncMock(side_effect=lambda voice: f"Voice changed to {voice}.")

    def register_tool(self, *, name, description, handler, parameters):
        self.tools.append({
            "type": "function",
            "name": name,
            "description": description,
            "parameters": parameters,
        })
        self.tool_handlers[name] = handler

    async def run(self):
        return None

    async def send_backend_cost(self, *args, **kwargs):
        return None


class FakeCostTracker:
    def __init__(self):
        self.config = SimpleNamespace(
            max_session_duration_minutes=30,
            max_session_cost_usd=5.0,
        )
        self.circuit_breaker_active = False
        self.session_start_times = {}

    async def can_start_new_session(self):
        return {"allowed": True}

    async def start_session(self, session_id, tier="free"):
        self.session_start_times[session_id] = None
        self.tier = tier

    def get_session_limits(self, session_id):
        return {
            "tier": self.tier,
            "max_session_cost_usd": self.config.max_session_cost_usd,
            "max_session_duration_seconds": self.config.max_session_duration_minutes * 60,
        }

    async def check_circuit_breaker(self):
        return None

    async def end_session(self, session_id):
        self.session_start_times.pop(session_id, None)

    async def track_usage(self, **kwargs):
        return {"budget_ok": True}


@pytest_asyncio.fixture
async def tool_context(monkeypatch):
    import app.main as main

    fake_session = FakeSession()
    fake_tracker = FakeCostTracker()
    websocket = MagicMock()
    websocket.headers = {"origin": "http://localhost:8000", "host": "localhost:8000"}
    websocket.client = None
    websocket.query_params = SimpleNamespace(getlist=lambda name: [])
    websocket.scope = {}
    websocket.url = "ws://localhost/session"
    websocket.send_json = AsyncMock()
    websocket.close = AsyncMock()

    monkeypatch.setattr(main, "RealtimeSession", lambda **kwargs: fake_session)
    monkeypatch.setattr(
        main,
        "realtime_llm_config",
        {"config_list": [{"model": "test-model", "api_key": "test-key"}]},
    )
    monkeypatch.setattr(main, "cost_tracker", fake_tracker)
    monkeypatch.setattr(main.ws_security, "validate_connection", AsyncMock(return_value=True))
    main.websocket_connection_limiter.reset()

    await main.handle_media_stream(websocket)
    try:
        yield SimpleNamespace(
            main=main,
            session=fake_session,
            websocket=websocket,
            schemas={tool["name"]: tool for tool in fake_session.tools},
            handlers=fake_session.tool_handlers,
        )
    finally:
        main.websocket_connection_limiter.reset()


def _response(*, status_code=200, payload=None, text=""):
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.json.return_value = payload or {}
    response.raise_for_status.return_value = None
    return response


def _geo_response():
    return _response(payload={"results": [{
        "latitude": 51.5,
        "longitude": -0.1,
        "timezone": "Europe/London",
    }]})


def _weather_response(kind):
    if kind == "current":
        payload = {"current": {"temperature_2m": 9.5, "weather_code": 3}}
    else:
        payload = {"daily": {"time": ["2026-11-08"], "weather_code": [3]}}
    return _response(payload=payload)


@pytest.mark.asyncio
async def test_registered_tool_schemas_are_stable(tool_context):
    expected = {
        "get_current_weather": ("location",),
        "get_weather_forecast": ("location",),
        "web_search": ("query",),
        "web_fetch": ("url",),
        "change_voice": ("voice",),
    }

    assert set(tool_context.schemas) == set(expected)
    for name, required in expected.items():
        schema = tool_context.schemas[name]
        assert schema["type"] == "function"
        assert schema["description"]
        assert schema["parameters"]["type"] == "object"
        assert schema["parameters"]["required"] == list(required)
        assert set(schema["parameters"]["properties"]) == set(required)
        assert all(
            value["type"] == "string"
            for value in schema["parameters"]["properties"].values()
        )

    assert tool_context.schemas["change_voice"]["parameters"]["properties"]["voice"]["enum"] == [
        "alloy", "ash", "ballad", "coral", "echo", "fable", "nova",
        "onyx", "sage", "shimmer", "verse",
    ]


@pytest.mark.asyncio
async def test_weather_tools_return_successful_provider_results(tool_context, monkeypatch):
    for name, kind in (
        ("get_current_weather", "current"),
        ("get_weather_forecast", "forecast"),
    ):
        requests_get = Mock(side_effect=[_geo_response(), _weather_response(kind)])
        monkeypatch.setattr(tool_context.main.requests, "get", requests_get)

        result = json.loads(await tool_context.handlers[name]("London"))

        assert kind == ("current" if "current" in result else "forecast")
        assert requests_get.call_count == 2
        assert requests_get.call_args_list[0].kwargs["timeout"] == 10
        assert requests_get.call_args_list[1].kwargs["timeout"] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, expected",
    [
        ("get_current_weather", "Invalid location format"),
        ("get_weather_forecast", "Invalid location format"),
    ],
)
async def test_weather_tools_reject_invalid_locations(tool_context, monkeypatch, name, expected):
    requests_get = Mock()
    monkeypatch.setattr(tool_context.main.requests, "get", requests_get)

    result = json.loads(await tool_context.handlers[name]("'; DROP TABLE weather--"))

    assert expected in result["error"]
    requests_get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, expected",
    [
        ("get_current_weather", "Unable to fetch weather data"),
        ("get_weather_forecast", "Unable to fetch weather forecast"),
    ],
)
async def test_weather_tools_surface_provider_errors_and_timeouts(
    tool_context, monkeypatch, name, expected
):
    provider_error = Mock(side_effect=[_geo_response(), _response(status_code=503, text="unavailable")])
    monkeypatch.setattr(tool_context.main.requests, "get", provider_error)
    assert json.loads(await tool_context.handlers[name]("London"))["error"] == expected

    timeout = Mock(side_effect=[_geo_response(), tool_context.main.requests.Timeout("timed out")])
    monkeypatch.setattr(tool_context.main.requests, "get", timeout)
    assert json.loads(await tool_context.handlers[name]("London"))["error"] == expected


@pytest.mark.asyncio
async def test_web_search_returns_provider_text_and_uses_contract(tool_context, monkeypatch):
    response = SimpleNamespace(output_text="A sourced result")
    create = Mock(return_value=response)
    monkeypatch.setattr(tool_context.main.openai_client.responses, "create", create)

    result = await tool_context.handlers["web_search"]("latest Python release")

    assert result == "A sourced result"
    create.assert_called_once_with(
        model="gpt-5.4-nano",
        tools=[{"type": "web_search_preview"}],
        input="latest Python release",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query, expected",
    [
        ("x", "Invalid search query"),
        ("normal query", "trouble searching"),
        ("timeout query", "trouble searching"),
    ],
)
async def test_web_search_validation_provider_error_and_timeout(tool_context, monkeypatch, query, expected):
    create = Mock(side_effect=TimeoutError("provider timed out") if query == "timeout query" else RuntimeError("API down"))
    monkeypatch.setattr(tool_context.main.openai_client.responses, "create", create)

    result = await tool_context.handlers["web_search"](query)

    assert expected.lower() in result.lower()
    if query == "x":
        create.assert_not_called()


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, response_factory):
        self.requests = []
        self.response_factory = response_factory

    async def handle_async_request(self, request):
        self.requests.append(request)
        return await self.response_factory(request) if asyncio.iscoroutinefunction(self.response_factory) else self.response_factory(request)

    async def aclose(self):
        return None


def _patch_fetch_transport(tool_context, monkeypatch, transport):
    monkeypatch.setattr(tool_context.main.httpx, "AsyncHTTPTransport", lambda: transport)
    monkeypatch.setattr(tool_context.main, "_resolve_vetted_ip", Mock(return_value="93.184.216.34"))


@pytest.mark.asyncio
async def test_web_fetch_returns_content_and_honors_initial_ssrf_boundary(tool_context, monkeypatch):
    transport = RecordingTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="public content",
            request=request,
        )
    )
    _patch_fetch_transport(tool_context, monkeypatch, transport)

    result = json.loads(await tool_context.handlers["web_fetch"]("https://example.com/read"))

    assert result["text"] == "public content"
    assert transport.requests[0].url.host == "93.184.216.34"
    assert transport.requests[0].headers["host"] == "example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url, expected",
    [
        ("file:///etc/passwd", "Invalid URL format"),
        ("https://127.0.0.1/admin", "Invalid URL format"),
    ],
)
async def test_web_fetch_rejects_invalid_urls_before_network(tool_context, monkeypatch, url, expected):
    resolver = Mock()
    monkeypatch.setattr(tool_context.main, "_resolve_vetted_ip", resolver)

    result = json.loads(await tool_context.handlers["web_fetch"](url))

    assert result["error"] == expected
    resolver.assert_not_called()


@pytest.mark.asyncio
async def test_web_fetch_returns_provider_http_error_and_timeout(tool_context, monkeypatch):
    error_transport = RecordingTransport(
        lambda request: httpx.Response(503, request=request)
    )
    _patch_fetch_transport(tool_context, monkeypatch, error_transport)
    error = json.loads(await tool_context.handlers["web_fetch"]("https://example.com/unavailable"))
    assert error == {"error": "HTTP 503", "url": "https://example.com/unavailable"}

    def timeout_response(request):
        raise httpx.ReadTimeout("provider timed out", request=request)

    timeout_transport = RecordingTransport(timeout_response)
    _patch_fetch_transport(tool_context, monkeypatch, timeout_transport)
    timeout = json.loads(await tool_context.handlers["web_fetch"]("https://example.com/slow"))
    assert timeout["error"] == "Request timed out after 15 seconds"


@pytest.mark.asyncio
async def test_web_fetch_blocks_private_initial_and_redirect_addresses(tool_context, monkeypatch):
    private = tool_context.main._PrivateAddressResolutionError("internal.example", "169.254.169.254")
    resolver = Mock(side_effect=[private])
    monkeypatch.setattr(tool_context.main, "_resolve_vetted_ip", resolver)
    transport = RecordingTransport(
        lambda request: httpx.Response(
            302,
            headers={"location": "http://internal.example/metadata"},
            request=request,
        )
    )
    monkeypatch.setattr(tool_context.main.httpx, "AsyncHTTPTransport", lambda: transport)

    initial = json.loads(await tool_context.handlers["web_fetch"]("https://private.example/start"))

    assert initial["error"] == "Cannot fetch from internal/private addresses"
    assert transport.requests == []

    resolver.reset_mock()
    resolver.side_effect = ["93.184.216.34", private]
    result = json.loads(await tool_context.handlers["web_fetch"]("https://example.com/start"))

    assert "Redirect to private IP blocked" in result["error"]
    assert len(transport.requests) == 1
    assert resolver.call_count == 2


@pytest.mark.asyncio
async def test_web_fetch_follows_and_revalidates_public_redirect(tool_context, monkeypatch):
    resolver = Mock(side_effect=["93.184.216.34", "93.184.216.35"])
    monkeypatch.setattr(tool_context.main, "_resolve_vetted_ip", resolver)

    def response_factory(request):
        if len(transport.requests) == 1:
            return httpx.Response(302, headers={"location": "http://redirect.example/path"}, request=request)
        return httpx.Response(200, headers={"content-type": "text/plain"}, text="redirected", request=request)

    transport = RecordingTransport(response_factory)
    monkeypatch.setattr(tool_context.main.httpx, "AsyncHTTPTransport", lambda: transport)

    result = json.loads(await tool_context.handlers["web_fetch"]("http://example.com/start"))

    assert result["text"] == "redirected"
    assert [request.url.host for request in transport.requests] == [
        "93.184.216.34", "93.184.216.35",
    ]
    assert resolver.call_count == 2


@pytest.mark.asyncio
async def test_realtime_dispatch_sends_function_output_and_contains_handler_errors():
    from app.realtime_session import RealtimeSession

    websocket = AsyncMock()
    session = RealtimeSession(
        websocket=websocket,
        model="test-model",
        api_key="test-key",
        system_message="test",
    )
    handler = AsyncMock(return_value={"status": "ok"})
    session.register_tool(
        name="example_tool",
        description="example",
        handler=handler,
        parameters={"type": "object", "properties": {}, "required": []},
    )

    await session._handle_tool_call({
        "name": "example_tool",
        "call_id": "call-123",
        "arguments": json.dumps({"value": "input"}),
    })

    handler.assert_awaited_once_with(value="input")
    sent = websocket.send_json.call_args.args[0]
    assert sent["type"] == "conversation.item.create"
    assert sent["item"] == {
        "type": "function_call_output",
        "call_id": "call-123",
        "output": "{'status': 'ok'}",
    }

    websocket.send_json.reset_mock()
    handler.side_effect = RuntimeError("provider unavailable")
    await session._handle_tool_call({
        "name": "example_tool",
        "call_id": "call-124",
        "arguments": "not-json",
    })

    assert websocket.send_json.call_args.args[0]["item"]["output"] == (
        "Error executing tool: provider unavailable"
    )


@pytest.mark.asyncio
async def test_voice_tool_dispatches_and_reports_send_timeout(tool_context):
    result = await tool_context.handlers["change_voice"]("nova")
    assert result == "Voice changed to nova."
    tool_context.session.change_voice.assert_awaited_once_with("nova")

    from app.realtime_session import RealtimeSession

    websocket = AsyncMock()
    websocket.send_json.side_effect = TimeoutError("socket timed out")
    session = RealtimeSession(
        websocket=websocket,
        model="test-model",
        api_key="test-key",
        system_message="test",
    )

    failure = await session.change_voice("nova")

    assert failure == "Failed to change voice: socket timed out"
