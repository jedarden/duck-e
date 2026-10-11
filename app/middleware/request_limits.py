"""
Request and Response Size Limiting Middleware
OWASP API4: Unrestricted Resource Consumption

Prevents:
- Large request payloads (memory exhaustion)
- JSON bomb attacks (deeply nested objects)
- Large response payloads (data exfiltration)
- Streaming response overflow
"""
import json
import logging
from typing import Optional
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.datastructures import Headers

logger = logging.getLogger(__name__)


class RequestSizeLimitMiddleware:
    """
    Limit request payload size to prevent memory exhaustion attacks
    """

    def __init__(
        self,
        app,
        max_size_mb: float = 1.0,
        max_json_depth: int = 50
    ):
        """
        Initialize middleware

        Args:
            app: FastAPI application
            max_size_mb: Maximum request size in MB
            max_json_depth: Maximum JSON nesting depth
        """
        self.app = app
        self.max_size_bytes = int(max_size_mb * 1024 * 1024)
        self.max_json_depth = max_json_depth

    async def __call__(self, scope, receive, send):
        """Bound and replay HTTP request bodies before they reach route parsing."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
                if size < 0:
                    raise ValueError("negative content length")
                if size > self.max_size_bytes:
                    logger.warning(
                        f"Request rejected: size {size} bytes exceeds limit "
                        f"{self.max_size_bytes} bytes from {self._client_host(scope)}"
                    )
                    response = JSONResponse(
                        status_code=413,
                        content={
                            "error": "Payload Too Large",
                            "max_size_mb": self.max_size_bytes / (1024 * 1024)
                        }
                    )
                    await response(scope, receive, send)
                    return

            except ValueError:
                logger.warning(f"Invalid Content-Length header: {content_length}")
                response = JSONResponse(
                    status_code=400,
                    content={"error": "Invalid Content-Length header"}
                )
                await response(scope, receive, send)
                return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue

            body.extend(message.get("body", b""))
            if len(body) > self.max_size_bytes:
                logger.warning(
                    "Request rejected: streamed body exceeds limit %s bytes from %s",
                    self.max_size_bytes,
                    self._client_host(scope),
                )
                response = JSONResponse(
                    status_code=413,
                    content={
                        "error": "Payload Too Large",
                        "max_size_mb": self.max_size_bytes / (1024 * 1024)
                    }
                )
                await response(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if body and (content_type == "application/json" or content_type.endswith("+json")):
            depth = None
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                pass  # Let FastAPI return its normal malformed-JSON error.
            except RecursionError:
                depth = self.max_json_depth + 1
            else:
                depth = self._get_json_depth(parsed)

            if depth is not None and depth > self.max_json_depth:
                logger.warning(
                    "JSON bomb attempt detected: depth %s exceeds limit %s from %s",
                    depth,
                    self.max_json_depth,
                    self._client_host(scope),
                )
                response = JSONResponse(
                    status_code=400,
                    content={
                        "error": "JSON payload too deeply nested",
                        "max_depth": self.max_json_depth,
                        "detected_depth": depth,
                    },
                )
                await response(scope, receive, send)
                return

        replayed = False

        async def replay_body():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay_body, send)

    @staticmethod
    def _client_host(scope) -> str:
        client = scope.get("client")
        return client[0] if client else "unknown"

    async def check_request_size(self, request: Request) -> Optional[Response]:
        """
        Check request size (used in tests)

        Returns:
            Error response if size exceeded, None otherwise
        """
        content_length = request.headers.get("content-length")

        if not content_length:
            # No Content-Length header - could be streaming
            # In production, you might want to track streaming size
            return None

        try:
            size = int(content_length)

            if size > self.max_size_bytes:
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": "Payload Too Large",
                        "max_size_mb": self.max_size_bytes / (1024 * 1024)
                    }
                )

        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid Content-Length header"}
            )

        return None

    async def validate_json_depth(self, request: Request) -> Optional[Response]:
        """
        Validate JSON nesting depth to prevent JSON bomb attacks

        Returns:
            Error response if depth exceeded, None otherwise
        """
        try:
            # Read request body
            body = await request.body()

            if not body:
                return None

            # Parse JSON
            data = json.loads(body)

            # Check nesting depth
            depth = self._get_json_depth(data)

            if depth > self.max_json_depth:
                logger.warning(
                    f"JSON bomb attempt detected: depth {depth} exceeds limit "
                    f"{self.max_json_depth} from {request.client.host if request.client else 'unknown'}"
                )
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "JSON payload too deeply nested",
                        "max_depth": self.max_json_depth,
                        "detected_depth": depth
                    }
                )

        except RecursionError:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "JSON payload too deeply nested",
                    "max_depth": self.max_json_depth,
                    "detected_depth": self.max_json_depth + 1,
                },
            )
        except json.JSONDecodeError:
            # Invalid JSON - let the application handle it
            pass
        except Exception as e:
            logger.error(f"Error validating JSON depth: {e}")

        return None

    def _get_json_depth(self, obj, current_depth: int = 0) -> int:
        """
        Recursively calculate JSON nesting depth

        Args:
            obj: JSON object
            current_depth: Current recursion depth

        Returns:
            Maximum depth
        """
        maximum_depth = current_depth
        pending = [(obj, current_depth)]
        while pending:
            value, depth = pending.pop()
            maximum_depth = max(maximum_depth, depth)
            if isinstance(value, dict):
                pending.extend((child, depth + 1) for child in value.values())
            elif isinstance(value, list):
                pending.extend((child, depth + 1) for child in value)
        return maximum_depth


class ResponseSizeLimitMiddleware(BaseHTTPMiddleware):
    """
    Limit response payload size to prevent data exfiltration
    """

    def __init__(self, app, max_size_mb: float = 10.0):
        """
        Initialize middleware

        Args:
            app: FastAPI application
            max_size_mb: Maximum response size in MB
        """
        super().__init__(app)
        self.max_size_bytes = int(max_size_mb * 1024 * 1024)

    async def dispatch(self, request: Request, call_next):
        """
        Middleware dispatch handler
        """
        response = await call_next(request)

        chunks = []
        body_size = 0
        async for chunk in response.body_iterator:
            if not isinstance(chunk, bytes):
                chunk = str(chunk).encode()
            body_size += len(chunk)
            if body_size > self.max_size_bytes:
                logger.warning(
                    f"Response rejected: size exceeds limit {self.max_size_bytes} bytes "
                    f"for {request.url.path}"
                )
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": "Response Too Large",
                        "message": "The requested resource is too large to return",
                        "max_size_mb": self.max_size_bytes / (1024 * 1024)
                    }
                )
            chunks.append(chunk)

        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers.pop("transfer-encoding", None)
        return Response(
            content=b"".join(chunks),
            status_code=response.status_code,
            headers=headers,
            background=response.background,
        )

    async def validate_response_size(self, response: Response) -> Response:
        """
        Validate response size (used in tests)

        Returns:
            Original or error response
        """
        if hasattr(response, 'body') and response.body:
            body_size = len(response.body)

            if body_size > self.max_size_bytes:
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": "Response Too Large",
                        "message": "The requested resource is too large to return",
                        "max_size_mb": self.max_size_bytes / (1024 * 1024)
                    }
                )

        return response

    async def track_streaming_response(self, stream_generator):
        """
        Track streaming response size

        Raises exception if size limit exceeded
        """
        total_size = 0

        async for chunk in stream_generator:
            chunk_size = len(chunk) if isinstance(chunk, bytes) else len(str(chunk).encode())
            total_size += chunk_size

            if total_size > self.max_size_bytes:
                raise Exception(
                    f"Streaming response size limit exceeded: "
                    f"{total_size} bytes > {self.max_size_bytes} bytes"
                )

            yield chunk
