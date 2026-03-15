"""
Unit tests for RealtimeSession voice change functionality.

Tests the fix for duck-e-ovk: Voice change reports failure but actually succeeds.
The root cause was that the code was using the deprecated /v1/realtime/sessions
endpoint with an old format. The fix uses session.update event instead.
"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.realtime_session import RealtimeSession, AVAILABLE_VOICES


class TestVoiceChange:
    """Test voice change functionality."""

    @pytest.fixture
    def mock_websocket(self):
        """Create a mock WebSocket."""
        ws = AsyncMock()
        ws.send_json = AsyncMock()
        return ws

    @pytest.fixture
    def session(self, mock_websocket):
        """Create a RealtimeSession for testing."""
        return RealtimeSession(
            websocket=mock_websocket,
            model="gpt-realtime-1.5",
            api_key="test-key",
            system_message="You are a test assistant.",
            voice="alloy",
        )

    def test_available_voices_list(self):
        """Test that AVAILABLE_VOICES contains expected voices."""
        expected_voices = [
            "alloy", "ash", "ballad", "coral", "echo",
            "fable", "nova", "onyx", "sage", "shimmer", "verse"
        ]
        for voice in expected_voices:
            assert voice in AVAILABLE_VOICES

    def test_change_voice_invalid_voice(self, session):
        """Test that change_voice rejects invalid voices."""
        result = asyncio.run(session.change_voice("invalid_voice"))

        assert "Invalid voice" in result
        assert "invalid_voice" in result
        assert "Available voices:" in result

    def test_change_voice_valid_voice_sends_session_update(self, session, mock_websocket):
        """Test that change_voice sends ducke.session_update message."""
        result = asyncio.run(session.change_voice("nova"))

        # Verify the message was sent
        assert mock_websocket.send_json.called
        sent_message = mock_websocket.send_json.call_args[0][0]

        # Verify message structure
        assert sent_message["type"] == "ducke.session_update"
        assert "update" in sent_message
        assert sent_message["update"]["type"] == "session.update"
        assert sent_message["update"]["session"]["type"] == "realtime"
        assert sent_message["update"]["session"]["audio"]["output"]["voice"] == "nova"

    def test_change_voice_includes_init_messages(self, session, mock_websocket):
        """Test that change_voice includes init messages for confirmation."""
        result = asyncio.run(session.change_voice("fable"))

        sent_message = mock_websocket.send_json.call_args[0][0]

        # Verify init messages are included
        assert "init" in sent_message
        assert len(sent_message["init"]) == 2

        # First init message should create a conversation item
        assert sent_message["init"][0]["type"] == "conversation.item.create"
        assert "voice was just changed to fable" in sent_message["init"][0]["item"]["content"][0]["text"]

        # Second init message should trigger a response
        assert sent_message["init"][1]["type"] == "response.create"

    def test_change_voice_updates_session_voice(self, session, mock_websocket):
        """Test that change_voice updates the session's voice attribute."""
        assert session.voice == "alloy"  # Initial voice

        asyncio.run(session.change_voice("shimmer"))

        assert session.voice == "shimmer"

    def test_change_voice_returns_success_message(self, session, mock_websocket):
        """Test that change_voice returns a success message."""
        result = asyncio.run(session.change_voice("echo"))

        assert "Voice changed to echo" in result
        assert "Reinitialising" not in result  # Old behavior text should be gone

    def test_change_voice_handles_websocket_error(self, session, mock_websocket):
        """Test that change_voice handles WebSocket errors gracefully."""
        # Simulate WebSocket error
        mock_websocket.send_json.side_effect = Exception("WebSocket error")

        result = asyncio.run(session.change_voice("sage"))

        # Should return an error message
        assert "Failed to change voice" in result

    def test_all_available_voices_are_accepted(self, session, mock_websocket):
        """Test that all voices in AVAILABLE_VOICES are accepted."""
        for voice in AVAILABLE_VOICES:
            mock_websocket.send_json.reset_mock()
            result = asyncio.run(session.change_voice(voice))

            # Should succeed
            assert f"Voice changed to {voice}" in result

            # Should send the correct voice in the session update
            sent_message = mock_websocket.send_json.call_args[0][0]
            assert sent_message["update"]["session"]["audio"]["output"]["voice"] == voice


class TestVoiceChangeMessageFormat:
    """Test the exact message format for session.update."""

    @pytest.fixture
    def mock_websocket(self):
        """Create a mock WebSocket."""
        ws = AsyncMock()
        ws.send_json = AsyncMock()
        return ws

    @pytest.fixture
    def session(self, mock_websocket):
        """Create a RealtimeSession for testing."""
        return RealtimeSession(
            websocket=mock_websocket,
            model="gpt-realtime-1.5",
            api_key="test-key",
            system_message="You are a test assistant.",
            voice="alloy",
        )

    def test_session_update_format_matches_ga_api_spec(self, session, mock_websocket):
        """
        Test that session.update format matches OpenAI Realtime GA API spec.

        According to the GA migration guide, the session.update event should have:
        - type: "session.update"
        - session.type: "realtime"
        - session.audio.output.voice: <voice name>
        """
        asyncio.run(session.change_voice("nova"))

        sent_message = mock_websocket.send_json.call_args[0][0]
        update = sent_message["update"]

        # Verify GA API format
        assert update["type"] == "session.update"
        assert update["session"]["type"] == "realtime"
        assert "audio" in update["session"]
        assert "output" in update["session"]["audio"]
        assert "voice" in update["session"]["audio"]["output"]

    def test_session_update_does_not_call_ephemeral_key_endpoint(self, session, mock_websocket):
        """
        Test that change_voice does NOT call _get_ephemeral_key.

        The fix for duck-e-ovk avoids calling the deprecated /v1/realtime/sessions
        endpoint by using session.update instead.
        """
        with patch.object(session, '_get_ephemeral_key', new=AsyncMock()) as mock_get_key:
            asyncio.run(session.change_voice("coral"))

            # _get_ephemeral_key should NOT be called
            assert not mock_get_key.called

    def test_session_update_preserves_other_session_config(self, session, mock_websocket):
        """
        Test that session.update only changes voice, not other session config.

        The session.update should only include the audio.output.voice field,
        not modify other session settings like tools or instructions.
        """
        asyncio.run(session.change_voice("onyx"))

        sent_message = mock_websocket.send_json.call_args[0][0]
        session_obj = sent_message["update"]["session"]

        # Should only have type and audio.output.voice
        assert session_obj["type"] == "realtime"
        assert "audio" in session_obj
        assert "output" in session_obj["audio"]
        assert "voice" in session_obj["audio"]["output"]

        # Should NOT have tools or instructions in the update
        # (those are set during initial session creation)
        assert "tools" not in session_obj
        assert "instructions" not in session_obj
