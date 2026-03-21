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
            api_key="test-key",  # pragma: allowlist secret
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
        assert sent_message["update"]["session"]["voice"] == "nova"

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
            assert sent_message["update"]["session"]["voice"] == voice


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
            api_key="test-key",  # pragma: allowlist secret
            system_message="You are a test assistant.",
            voice="alloy",
        )

    def test_session_update_format(self, session, mock_websocket):
        """
        Test that session.update format is correct.

        The session.update event should have:
        - type: "session.update"
        - session.voice: <voice name>
        """
        asyncio.run(session.change_voice("nova"))

        sent_message = mock_websocket.send_json.call_args[0][0]
        update = sent_message["update"]

        # Verify format
        assert update["type"] == "session.update"
        assert "voice" in update["session"]
        assert update["session"]["voice"] == "nova"

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

        The session.update should only include the voice field,
        not modify other session settings like tools or instructions.
        """
        asyncio.run(session.change_voice("onyx"))

        sent_message = mock_websocket.send_json.call_args[0][0]
        session_obj = sent_message["update"]["session"]

        # Should only have voice
        assert "voice" in session_obj
        assert session_obj["voice"] == "onyx"

        # Should NOT have tools or instructions in the update
        # (those are set during initial session creation)
        assert "tools" not in session_obj
        assert "instructions" not in session_obj
