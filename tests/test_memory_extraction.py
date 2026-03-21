"""
Tests for memory auto-extraction from conversation turns (duck-e-xqo).

Covers:
- UserMemoryStore.extract_and_save: happy path, empty user text, bad JSON, HTTP error
- RealtimeSession.on_turn_done callback fires on ducke.turn_done message
"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from starlette.websockets import WebSocketDisconnect

from app.memory import UserMemoryStore
from app.realtime_session import RealtimeSession


# ---------------------------------------------------------------------------
# UserMemoryStore.extract_and_save
# ---------------------------------------------------------------------------

class TestExtractAndSave:
    """Test the async extract_and_save method."""

    def _make_store(self, tmp_path):
        return UserMemoryStore(user_id="test@example.com", memory_dir=str(tmp_path))

    @pytest.mark.asyncio
    async def test_saves_extracted_facts(self, tmp_path):
        """Facts returned by the model are persisted."""
        store = self._make_store(tmp_path)
        store.load()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '["User lives in London", "User prefers Celsius"]'}}]
        }

        with patch("app.memory.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await store.extract_and_save(
                user_text="I live in London and prefer Celsius.",
                assistant_text="Got it!",
                api_key="test-key",  # pragma: allowlist secret
            )

        facts = store.get_facts()
        assert "User lives in London" in facts
        assert "User prefers Celsius" in facts

    @pytest.mark.asyncio
    async def test_empty_user_text_is_noop(self, tmp_path):
        """Empty user_text skips the extraction call entirely."""
        store = self._make_store(tmp_path)
        store.load()

        with patch("app.memory.httpx.AsyncClient") as mock_client_cls:
            await store.extract_and_save(
                user_text="   ",
                assistant_text="Something",
                api_key="test-key",  # pragma: allowlist secret
            )
            # httpx client should never be instantiated
            mock_client_cls.assert_not_called()

        assert store.get_facts() == []

    @pytest.mark.asyncio
    async def test_empty_array_saves_nothing(self, tmp_path):
        """Model returning [] does not add any facts."""
        store = self._make_store(tmp_path)
        store.load()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "[]"}}]
        }

        with patch("app.memory.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await store.extract_and_save("Hello", "Hi there", "test-key")  # pragma: allowlist secret

        assert store.get_facts() == []

    @pytest.mark.asyncio
    async def test_invalid_json_is_silently_suppressed(self, tmp_path):
        """Bad JSON from the model does not raise and saves nothing."""
        store = self._make_store(tmp_path)
        store.load()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "not valid json"}}]
        }

        with patch("app.memory.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            # Should not raise
            await store.extract_and_save("Tell me something", "Sure!", "test-key")  # pragma: allowlist secret

        assert store.get_facts() == []

    @pytest.mark.asyncio
    async def test_http_error_is_silently_suppressed(self, tmp_path):
        """Network errors do not propagate out of extract_and_save."""
        store = self._make_store(tmp_path)
        store.load()

        with patch("app.memory.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=Exception("Network failure"))
            mock_client_cls.return_value = mock_client

            # Should not raise
            await store.extract_and_save("Hello", "Hi", "test-key")  # pragma: allowlist secret

        assert store.get_facts() == []

    @pytest.mark.asyncio
    async def test_uses_gpt_5_4_nano_model(self, tmp_path):
        """Extraction uses gpt-5.4-nano as specified in the bead."""
        store = self._make_store(tmp_path)
        store.load()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "[]"}}]
        }

        with patch("app.memory.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await store.extract_and_save("I like Python", "Great choice!", "test-key")  # pragma: allowlist secret

        call_kwargs = mock_client.post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs.args[1] if len(call_kwargs.args) > 1 else call_kwargs.kwargs["json"]
        assert body["model"] == "gpt-5.4-nano"


# ---------------------------------------------------------------------------
# RealtimeSession: on_turn_done callback
# ---------------------------------------------------------------------------

class TestOnTurnDoneCallback:
    """Test that on_turn_done is fired when ducke.turn_done is received."""

    @pytest.fixture
    def mock_websocket(self):
        ws = AsyncMock()
        ws.send_json = AsyncMock()
        return ws

    @pytest.mark.asyncio
    async def test_on_turn_done_fires_on_ducke_turn_done(self, mock_websocket):
        """ducke.turn_done message triggers the on_turn_done callback."""
        captured = {}

        async def fake_callback(user_text: str, assistant_text: str) -> None:
            captured["user_text"] = user_text
            captured["assistant_text"] = assistant_text

        session = RealtimeSession(
            websocket=mock_websocket,
            model="gpt-realtime-1.5",
            api_key="test-key",  # pragma: allowlist secret
            system_message="Test",
            on_turn_done=fake_callback,
        )

        # Simulate the session receiving a ducke.turn_done message then disconnecting
        mock_websocket.receive_json = AsyncMock(side_effect=[
            {
                "type": "ducke.turn_done",
                "user_text": "What's the weather?",
                "assistant_text": "It's sunny.",
            },
            WebSocketDisconnect(code=1000),
        ])

        # Patch _get_ephemeral_key so run() doesn't hit OpenAI
        with patch.object(session, "_get_ephemeral_key", new=AsyncMock(return_value={
            "client_secret": {"value": "fake-secret"},
            "model": "gpt-realtime-1.5",
        })):
            await session.run()

        # Give the fire-and-forget task a chance to complete
        await asyncio.sleep(0)

        assert captured.get("user_text") == "What's the weather?"
        assert captured.get("assistant_text") == "It's sunny."

    @pytest.mark.asyncio
    async def test_on_turn_done_not_called_when_not_set(self, mock_websocket):
        """No on_turn_done set → ducke.turn_done is silently ignored."""
        session = RealtimeSession(
            websocket=mock_websocket,
            model="gpt-realtime-1.5",
            api_key="test-key",  # pragma: allowlist secret
            system_message="Test",
            on_turn_done=None,
        )

        mock_websocket.receive_json = AsyncMock(side_effect=[
            {
                "type": "ducke.turn_done",
                "user_text": "Hello",
                "assistant_text": "Hi",
            },
            WebSocketDisconnect(code=1000),
        ])

        with patch.object(session, "_get_ephemeral_key", new=AsyncMock(return_value={
            "client_secret": {"value": "fake-secret"},
            "model": "gpt-realtime-1.5",
        })):
            await session.run()  # Must not raise

    @pytest.mark.asyncio
    async def test_on_turn_done_fires_as_background_task(self, mock_websocket):
        """on_turn_done is scheduled as a fire-and-forget task (non-blocking)."""
        call_order = []

        async def slow_callback(user_text: str, assistant_text: str) -> None:
            await asyncio.sleep(0.05)
            call_order.append("callback_done")

        session = RealtimeSession(
            websocket=mock_websocket,
            model="gpt-realtime-1.5",
            api_key="test-key",  # pragma: allowlist secret
            system_message="Test",
            on_turn_done=slow_callback,
        )

        mock_websocket.receive_json = AsyncMock(side_effect=[
            {
                "type": "ducke.turn_done",
                "user_text": "Hi",
                "assistant_text": "Hello",
            },
            WebSocketDisconnect(code=1000),
        ])

        with patch.object(session, "_get_ephemeral_key", new=AsyncMock(return_value={
            "client_secret": {"value": "fake-secret"},
            "model": "gpt-realtime-1.5",
        })):
            await session.run()

        # run() should have returned before the slow callback completes
        call_order.append("run_done")

        # Allow the background task to finish
        await asyncio.sleep(0.1)

        assert call_order == ["run_done", "callback_done"]
