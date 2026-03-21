"""
Custom RealtimeSession for DUCK-E
Replaces AG2 RealtimeAgent with a direct OpenAI Realtime API integration.
Supports voice change via WebRTC session teardown and reinit.
"""
import asyncio
import httpx
import json
import time
from logging import Logger, getLogger
from typing import Any, Callable, Optional

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect


AVAILABLE_VOICES = [
    "alloy", "ash", "ballad", "coral", "echo",
    "fable", "nova", "onyx", "sage", "shimmer", "verse"
]


class RealtimeSession:
    """
    Manages a single OpenAI Realtime API WebRTC session.

    Replaces AG2's RealtimeAgent. Responsibilities:
    - Obtain an ephemeral key from OpenAI /v1/realtime/sessions (server-side)
    - Send ag2.init to the browser client to bootstrap WebRTC
    - Relay tool calls from client -> execute -> return results
    - Support voice change via ducke.reinit (new ephemeral key, new WebRTC peer)
    """

    def __init__(
        self,
        websocket: WebSocket,
        model: str,
        api_key: str,
        system_message: str,
        voice: str = "alloy",
        logger: Optional[Logger] = None,
    ):
        self.websocket = websocket
        self.model = model
        self.api_key = api_key
        self.system_message = system_message
        self.voice = voice
        self.logger = logger or getLogger(__name__)
        self.tools: list[dict[str, Any]] = []
        self.tool_handlers: dict[str, Callable] = {}

    def register_tool(
        self,
        name: str,
        description: str,
        handler: Callable,
        parameters: dict[str, Any],
    ) -> None:
        """Register a callable tool handler for a given tool name."""
        self.tool_handlers[name] = handler
        self.tools.append({
            "type": "function",
            "name": name,
            "description": description,
            "parameters": parameters,
        })

    async def _get_ephemeral_key(self, voice: Optional[str] = None) -> dict[str, Any]:
        """
        Call OpenAI /v1/realtime/sessions server-side to obtain a short-lived
        ephemeral key.  Returns a SANITIZED config safe to send to the browser —
        only client_secret and model are included; the real API key is never
        forwarded.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/realtime/sessions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "voice": voice or self.voice,
                    "instructions": self.system_message,
                    "tools": self.tools,
                    "input_audio_transcription": {"model": "whisper-1"},
                },
            )
            resp.raise_for_status()
            data = resp.json()

        # SECURITY: Only return fields the client needs.
        # Never forward the raw response — it may contain server-side secrets.
        return {
            "client_secret": data["client_secret"],  # ephemeral key (short-lived)
            "model": data.get("model", self.model),
        }

    async def change_voice(self, voice: str) -> str:
        """
        Change the assistant's voice by sending a session.update event.

        Uses the OpenAI Realtime API's session.update event to change the voice
        without needing to regenerate ephemeral keys or tear down the WebRTC
        connection. This is faster and more reliable than the old reinit approach.

        The function_call_output reply (sent by _handle_tool_call after this
        returns) will be spoken by DUCK-E in the new voice.
        """
        if voice not in AVAILABLE_VOICES:
            return (
                f"Invalid voice '{voice}'. "
                f"Available voices: {', '.join(AVAILABLE_VOICES)}"
            )

        try:
            self.logger.info(f"Changing voice to: {voice}")
            self.voice = voice

            # Send a session.update event to change the voice.
            # The function_call_output from _handle_tool_call (containing
            # "Voice changed to X") will be spoken in the new voice, and
            # ag2client.js will send response.create to trigger continuation.
            await self.websocket.send_json({
                "type": "ducke.session_update",
                "update": {
                    "type": "session.update",
                    "session": {
                        "voice": voice,
                    },
                },
            })
            return f"Voice changed to {voice}."
        except Exception as e:
            self.logger.error(f"Failed to change voice: {e}", exc_info=True)
            return f"Failed to change voice: {str(e)}"

    async def run(self) -> None:
        """
        Main session loop.

        The WebSocket has already been accepted by the security middleware
        before this is called, so we do NOT call websocket.accept() here.
        """
        try:
            session_data = await self._get_ephemeral_key()
        except Exception as e:
            self.logger.error(f"Failed to get ephemeral key: {e}", exc_info=True)
            await self.websocket.send_json({
                "type": "error",
                "error": f"Failed to initialize session: {str(e)}",
            })
            await self.websocket.close(code=1011, reason="Session initialization failed")
            return

        # Send ag2.init — compatible with existing ag2client.js
        # SECURITY: session_data contains only ephemeral key + model.
        await self.websocket.send_json({
            "type": "ag2.init",
            "config": session_data,
            "init": [],
        })

        try:
            while True:
                data = await self.websocket.receive_json()
                msg_type = data.get("type", "")
                if msg_type == "response.function_call_arguments.done":
                    await self._handle_tool_call(data)
                elif msg_type == "ducke.annotation":
                    annotation = data.get("annotation", {})
                    self.logger.info(f"Annotation received from client: {annotation}")
        except WebSocketDisconnect:
            pass
        except Exception as e:
            self.logger.error(f"Session error: {e}", exc_info=True)

    async def _handle_tool_call(self, data: dict[str, Any]) -> None:
        """Execute a registered tool and send the result back to the client."""
        name = data.get("name")
        call_id = data.get("call_id")
        t_received = time.monotonic()

        self.logger.info(json.dumps({
            "event": "tool_call.received",
            "tool": name,
            "call_id": call_id,
            "ts": time.time(),
        }))

        try:
            args = json.loads(data.get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {}

        handler = self.tool_handlers.get(name)
        if not handler:
            self.logger.warning(f"No handler registered for tool: {name}")
            return

        self.logger.info(json.dumps({
            "event": "tool_call.handler_start",
            "tool": name,
            "call_id": call_id,
            "args": args,
            "ts": time.time(),
        }))
        t_handler_start = time.monotonic()

        try:
            if asyncio.iscoroutinefunction(handler):
                result = await handler(**args)
            else:
                result = handler(**args)
        except Exception as e:
            self.logger.error(f"Tool handler error for '{name}': {e}", exc_info=True)
            result = f"Error executing tool: {str(e)}"

        t_handler_end = time.monotonic()
        result_str = str(result)
        self.logger.info(json.dumps({
            "event": "tool_call.handler_done",
            "tool": name,
            "call_id": call_id,
            "result_size": len(result_str),
            "handler_duration_ms": round((t_handler_end - t_handler_start) * 1000, 1),
            "ts": time.time(),
        }))

        await self.websocket.send_json({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": result_str,
            },
        })
        # NOTE: response.create is sent by the browser client (ag2client.js)
        # immediately after it forwards the function_call_output to the
        # OpenAI data channel.  Sending it from the backend was unreliable
        # because the relay adds latency and the message could be dropped.

        t_sent = time.monotonic()
        self.logger.info(json.dumps({
            "event": "tool_call.result_sent",
            "tool": name,
            "call_id": call_id,
            "result_size": len(result_str),
            "total_duration_ms": round((t_sent - t_received) * 1000, 1),
            "ts": time.time(),
        }))
