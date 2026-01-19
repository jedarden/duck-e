"""
Mock utilities for OpenAI API in DUCK-E tests.

Usage:
    from tests.mocks.openai_mock import create_mock_response, MockRealtimeStream
"""

from unittest.mock import MagicMock, AsyncMock
from typing import Optional, List, Dict, Any
import json


def create_mock_response(
    content: Optional[str] = None,
    has_tool_calls: bool = False,
    tool_call_name: str = "web_search",
    finish_reason: str = "stop"
) -> MagicMock:
    """
    Create a mock OpenAI chat completion response.

    Args:
        content: The response content text
        has_tool_calls: Whether to include tool_calls in response
        tool_call_name: Name of the tool call (if has_tool_calls=True)
        finish_reason: The finish reason (stop, tool_calls, length, etc.)

    Returns:
        MagicMock configured like openai.chat.completions.create() response
    """
    message = MagicMock()
    message.content = content

    if has_tool_calls:
        tool_call = MagicMock()
        tool_call.id = "call_abc123"
        tool_call.type = "function"
        tool_call.function = MagicMock()
        tool_call.function.name = tool_call_name
        tool_call.function.arguments = json.dumps({"query": "test query"})
        message.tool_calls = [tool_call]
        finish_reason = "tool_calls"
    else:
        message.tool_calls = None

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason
    choice.index = 0

    response = MagicMock()
    response.choices = [choice]
    response.id = "chatcmpl-abc123"
    response.model = "gpt-5-mini"
    response.usage = MagicMock(prompt_tokens=10, completion_tokens=20, total_tokens=30)

    return response


def create_mock_empty_response() -> MagicMock:
    """Create a mock response with no choices."""
    response = MagicMock()
    response.choices = []
    return response


def create_mock_streaming_response(chunks: List[str]) -> List[MagicMock]:
    """
    Create a list of mock streaming response chunks.

    Args:
        chunks: List of text chunks to simulate streaming

    Returns:
        List of MagicMock objects simulating streaming chunks
    """
    mock_chunks = []

    for i, chunk_text in enumerate(chunks):
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta = MagicMock()
        chunk.choices[0].delta.content = chunk_text
        chunk.choices[0].finish_reason = None if i < len(chunks) - 1 else "stop"
        mock_chunks.append(chunk)

    return mock_chunks


class MockRealtimeStream:
    """
    Mock for OpenAI Realtime API streaming responses.

    Usage:
        stream = MockRealtimeStream([
            {"type": "server.response.text_delta", "delta": "Hello "},
            {"type": "server.response.text_delta", "delta": "world!"},
            {"type": "server.response.done"}
        ])

        async for message in stream:
            print(message)
    """

    def __init__(self, messages: List[Dict[str, Any]]):
        self.messages = messages
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self.messages):
            raise StopAsyncIteration

        message = self.messages[self._index]
        self._index += 1
        return message


def create_mock_openai_client(
    chat_response: Optional[MagicMock] = None,
    raise_exception: Optional[Exception] = None
) -> MagicMock:
    """
    Create a fully mocked OpenAI client.

    Args:
        chat_response: Response to return from chat.completions.create()
        raise_exception: Exception to raise instead of returning response

    Returns:
        MagicMock configured like openai.OpenAI()
    """
    client = MagicMock()

    if raise_exception:
        client.chat.completions.create.side_effect = raise_exception
    else:
        client.chat.completions.create.return_value = chat_response or create_mock_response(
            content="Default mock response"
        )

    return client


# Common error scenarios
class MockOpenAIErrors:
    """Factory for common OpenAI error scenarios."""

    @staticmethod
    def rate_limit():
        """Simulate rate limit error."""
        from openai import RateLimitError
        return RateLimitError("Rate limit exceeded", response=MagicMock(), body=None)

    @staticmethod
    def timeout():
        """Simulate timeout error."""
        from openai import APITimeoutError
        return APITimeoutError(request=MagicMock())

    @staticmethod
    def connection_error():
        """Simulate connection error."""
        from openai import APIConnectionError
        return APIConnectionError(request=MagicMock())

    @staticmethod
    def auth_error():
        """Simulate authentication error."""
        from openai import AuthenticationError
        return AuthenticationError(
            "Invalid API key",
            response=MagicMock(status_code=401),
            body=None
        )
