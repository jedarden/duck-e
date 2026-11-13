"""
Unit tests for RealtimeSession voice change functionality.

A mid-conversation voice change needs a NEW Realtime session: session.update
rejects the flat session.voice field outright and refuses
session.audio.output.voice once assistant audio exists (verified live
2026-11-12, docs/research/realtime-api-verified-2026-11-12.md). change_voice
therefore mints a fresh ephemeral key for the new voice and sends ducke.reinit;
the browser rebuilds the WebRTC peer and replays the transcript. A successful
change returns the NO_TOOL_OUTPUT sentinel so _handle_tool_call does not send a
function_call_output for a call_id that only exists in the old session.
"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.realtime_session import (
    RealtimeSession,
    AVAILABLE_VOICES,
    NO_TOOL_OUTPUT,
    transcription_language_for,
)

# Stand-in for the sanitized config _get_ephemeral_key returns.
FAKE_KEY_CONFIG = {
    "client_secret": {"value": "ek_test_ephemeral"},
    "model": "gpt-realtime-2",
}


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket."""
    ws = AsyncMock()
    ws.send_json = AsyncMock()
    return ws


@pytest.fixture
def session(mock_websocket):
    """Create a RealtimeSession for testing."""
    return RealtimeSession(
        websocket=mock_websocket,
        model="gpt-realtime-2",
        api_key="test-key",  # pragma: allowlist secret
        system_message="You are a test assistant.",
        voice="alloy",
    )


class TestVoiceChange:
    """change_voice: valid voices reinit, invalid ones return an error string."""

    def test_available_voices_list(self):
        """Test that AVAILABLE_VOICES contains expected voices."""
        expected_voices = [
            "alloy", "ash", "ballad", "coral", "echo",
            "sage", "shimmer", "verse", "marin", "cedar",
        ]
        for voice in expected_voices:
            assert voice in AVAILABLE_VOICES

    def test_change_voice_invalid_voice(self, session, mock_websocket):
        """An invalid voice returns the error string and sends nothing."""
        result = asyncio.run(session.change_voice("fable"))

        assert "Invalid voice 'fable'" in result
        assert "Available voices:" in result
        for voice in AVAILABLE_VOICES:
            assert voice in result
        mock_websocket.send_json.assert_not_awaited()

    def test_valid_voice_sends_exactly_one_reinit(self, session, mock_websocket):
        """A valid voice sends only the ducke.reinit message, key minted for it."""
        with patch.object(
            session, "_get_ephemeral_key", new=AsyncMock(return_value=dict(FAKE_KEY_CONFIG))
        ) as mock_get_key:
            result = asyncio.run(session.change_voice("cedar"))

        assert result is NO_TOOL_OUTPUT
        mock_get_key.assert_awaited_once_with("cedar")
        assert mock_websocket.send_json.await_count == 1
        sent = mock_websocket.send_json.await_args.args[0]
        assert sent == {
            "type": "ducke.reinit",
            "config": FAKE_KEY_CONFIG,
            "voice": "cedar",
            "init": [],
        }
        assert sent["config"]["client_secret"]["value"] == "ek_test_ephemeral"

    def test_valid_voice_updates_session_voice(self, session, mock_websocket):
        """The session remembers the new voice for later key mints."""
        with patch.object(
            session, "_get_ephemeral_key", new=AsyncMock(return_value=dict(FAKE_KEY_CONFIG))
        ):
            asyncio.run(session.change_voice("shimmer"))

        assert session.voice == "shimmer"

    def test_all_available_voices_send_reinit(self, session, mock_websocket):
        """Every accepted voice produces a reinit carrying that voice."""
        for voice in AVAILABLE_VOICES:
            mock_websocket.send_json.reset_mock()
            with patch.object(
                session, "_get_ephemeral_key",
                new=AsyncMock(return_value=dict(FAKE_KEY_CONFIG)),
            ):
                result = asyncio.run(session.change_voice(voice))

            assert result is NO_TOOL_OUTPUT
            sent = mock_websocket.send_json.call_args[0][0]
            assert sent["type"] == "ducke.reinit"
            assert sent["voice"] == voice

    def test_key_mint_failure_sends_no_reinit(self, session, mock_websocket):
        """If minting the ephemeral key fails, the session is left alone."""
        with patch.object(
            session, "_get_ephemeral_key",
            new=AsyncMock(side_effect=RuntimeError("upstream unavailable")),
        ):
            result = asyncio.run(session.change_voice("cedar"))

        assert result.startswith("Failed to change voice")
        assert "upstream unavailable" in result
        mock_websocket.send_json.assert_not_awaited()

    def test_websocket_send_failure_returns_error(self, session, mock_websocket):
        """A WebSocket failure while sending the reinit is reported, not raised."""
        mock_websocket.send_json.side_effect = TimeoutError("socket timed out")
        with patch.object(
            session, "_get_ephemeral_key", new=AsyncMock(return_value=dict(FAKE_KEY_CONFIG))
        ):
            result = asyncio.run(session.change_voice("sage"))

        assert result == "Failed to change voice: socket timed out"


class TestVoiceChangeToolCallFlow:
    """_handle_tool_call sends a function_call_output unless the voice changed."""

    @pytest.fixture
    def voice_session(self, mock_websocket):
        """A session whose change_voice tool runs the real change_voice."""
        s = RealtimeSession(
            websocket=mock_websocket,
            model="gpt-realtime-2",
            api_key="test-key",  # pragma: allowlist secret
            system_message="You are a test assistant.",
            voice="alloy",
        )
        s.tool_handlers["change_voice"] = s.change_voice
        return s

    @staticmethod
    def _call_tool(session, voice):
        asyncio.run(session._handle_tool_call({
            "name": "change_voice",
            "call_id": "call_v",
            "arguments": json.dumps({"voice": voice}),
        }))

    @staticmethod
    def _sent_types(mock_websocket):
        return [c.args[0].get("type") for c in mock_websocket.send_json.await_args_list]

    def test_valid_voice_sends_no_conversation_item(self, voice_session, mock_websocket):
        """A successful reinit suppresses the function_call_output entirely."""
        with patch.object(
            voice_session, "_get_ephemeral_key",
            new=AsyncMock(return_value=dict(FAKE_KEY_CONFIG)),
        ):
            self._call_tool(voice_session, "cedar")

        # Only the reinit was sent — no conversation.item.create for a
        # call_id that belongs to the replaced session.
        assert self._sent_types(mock_websocket) == ["ducke.reinit"]

    def test_invalid_voice_sends_tool_output_naming_voices(
        self, voice_session, mock_websocket
    ):
        """An invalid voice is reported to the model with the voice list."""
        self._call_tool(voice_session, "fable")

        assert self._sent_types(mock_websocket) == ["conversation.item.create"]
        sent = mock_websocket.send_json.await_args.args[0]
        assert sent["item"]["type"] == "function_call_output"
        assert sent["item"]["call_id"] == "call_v"
        assert "Invalid voice 'fable'" in sent["item"]["output"]
        for voice in AVAILABLE_VOICES:
            assert voice in sent["item"]["output"]

    def test_key_mint_failure_sends_failure_output_and_no_reinit(
        self, voice_session, mock_websocket
    ):
        """A failed key mint keeps the old session and tells the model."""
        with patch.object(
            voice_session, "_get_ephemeral_key",
            new=AsyncMock(side_effect=RuntimeError("upstream unavailable")),
        ):
            self._call_tool(voice_session, "cedar")

        types = self._sent_types(mock_websocket)
        assert "ducke.reinit" not in types
        sent = mock_websocket.send_json.await_args.args[0]
        assert sent["type"] == "conversation.item.create"
        assert sent["item"]["output"].startswith("Failed to change voice")


class _FakeResponse:
    """Minimal successful stand-in for the httpx.Response of client_secrets."""

    def __init__(self):
        self.is_success = True
        self.status_code = 200
        self.text = ""

    def json(self):
        return {"value": "ek_test_ephemeral"}

    def raise_for_status(self):
        return None


class _FakeAsyncClient:
    """
    Async context manager replacing app.realtime_session.httpx.AsyncClient.

    Records the json body of every post() call on the class so tests can
    assert on the exact session payload DUCK-E builds.
    """

    last_json = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, headers=None, json=None):
        type(self).last_json = json
        return _FakeResponse()


def _capture_ephemeral_payload(**session_kwargs) -> dict:
    """Build a RealtimeSession and return the body posted to client_secrets."""
    _FakeAsyncClient.last_json = None
    session = RealtimeSession(
        websocket=AsyncMock(),
        model="gpt-realtime-2",
        api_key="test-key",  # pragma: allowlist secret
        system_message="You are a test assistant.",
        **session_kwargs,
    )
    with patch("app.realtime_session.httpx.AsyncClient", _FakeAsyncClient):
        asyncio.run(session._get_ephemeral_key())
    assert _FakeAsyncClient.last_json is not None, "client_secrets was not called"
    return _FakeAsyncClient.last_json


@pytest.fixture(autouse=True)
def _clean_realtime_payload_env(monkeypatch):
    """Keep the three payload env vars out of every test in this module."""
    for var in (
        "REALTIME_VAD_EAGERNESS",
        "REALTIME_NOISE_REDUCTION",
        "REALTIME_TRANSCRIPTION_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


class TestEphemeralKeySessionPayload:
    """Session body posted to /v1/realtime/client_secrets (ducke-baf3ab64)."""

    def test_defaults_produce_semantic_vad_noise_reduction_and_transcription(self):
        payload = _capture_ephemeral_payload()

        audio_input = payload["session"]["audio"]["input"]
        assert audio_input["turn_detection"] == {
            "type": "semantic_vad",
            "eagerness": "auto",
        }
        assert audio_input["noise_reduction"] == {"type": "near_field"}
        assert audio_input["transcription"] == {"model": "gpt-4o-transcribe"}

    def test_env_vars_override_their_fields(self, monkeypatch):
        monkeypatch.setenv("REALTIME_VAD_EAGERNESS", "low")
        monkeypatch.setenv("REALTIME_NOISE_REDUCTION", "far_field")
        monkeypatch.setenv("REALTIME_TRANSCRIPTION_MODEL", "whisper-1")

        audio_input = _capture_ephemeral_payload()["session"]["audio"]["input"]

        assert audio_input["turn_detection"] == {
            "type": "semantic_vad",
            "eagerness": "low",
        }
        assert audio_input["noise_reduction"] == {"type": "far_field"}
        assert audio_input["transcription"] == {"model": "whisper-1"}

    def test_invalid_env_values_fall_back_to_defaults(self, monkeypatch):
        monkeypatch.setenv("REALTIME_VAD_EAGERNESS", "immediately")
        monkeypatch.setenv("REALTIME_NOISE_REDUCTION", "studio")

        audio_input = _capture_ephemeral_payload()["session"]["audio"]["input"]

        assert audio_input["turn_detection"]["eagerness"] == "auto"
        assert audio_input["noise_reduction"] == {"type": "near_field"}

    def test_noise_reduction_off_omits_the_key(self, monkeypatch):
        monkeypatch.setenv("REALTIME_NOISE_REDUCTION", "off")

        audio_input = _capture_ephemeral_payload()["session"]["audio"]["input"]

        assert "noise_reduction" not in audio_input

    def test_voice_model_and_tools_still_sent(self):
        payload = _capture_ephemeral_payload(voice="shimmer")

        session_body = payload["session"]
        assert session_body["type"] == "realtime"
        assert session_body["model"] == "gpt-realtime-2"
        assert session_body["audio"]["output"]["voice"] == "shimmer"
        assert session_body["instructions"] == "You are a test assistant."

    def test_language_present_only_when_transcription_language_set(self):
        audio_input = _capture_ephemeral_payload()["session"]["audio"]["input"]
        assert "language" not in audio_input["transcription"]

        audio_input = _capture_ephemeral_payload(
            transcription_language="en"
        )["session"]["audio"]["input"]
        assert audio_input["transcription"]["language"] == "en"


class TestTranscriptionLanguageFor:
    """transcription_language_for maps Accept-Language to supported codes."""

    @pytest.mark.parametrize(
        "header,expected",
        [
            ("en-US,en;q=0.9", "en"),
            ("pt_BR", "pt"),
            ("FR", "fr"),
            ("xx-YY", None),
            ("", None),
            ("tlh", None),
            ("fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7", "fr"),
            ("*;q=0.5", None),
            ("   de-DE  ", "de"),
        ],
    )
    def test_mapping(self, header, expected):
        assert transcription_language_for(header) == expected
