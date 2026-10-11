"""
Rate limiting middleware for DUCK-E public API
Implements per-IP and per-endpoint rate limits using in-memory storage

NOTE: This implementation uses in-memory storage for single instance deployment.
Rate limit counters reset on server restart - this is acceptable for the use case.
"""
from fastapi import Request, HTTPException
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from collections import defaultdict, deque
from dataclasses import dataclass
from math import ceil
from threading import Lock
from time import monotonic
from typing import Optional, Callable, Any
import os
import logging
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram

from app.models.user import TIER_CONFIGURATIONS, UserTier

logger = logging.getLogger(__name__)

# Prometheus metrics
rate_limit_exceeded = Counter(
    'rate_limit_exceeded_total',
    'Total number of rate limit violations',
    ['endpoint', 'client_ip']
)

request_duration = Histogram(
    'rate_limit_check_duration_seconds',
    'Time spent checking rate limits',
    ['endpoint']
)


class RateLimitConfig(BaseModel):
    """
    Rate limiting configuration with validation

    NOTE: In-memory storage only - no Redis support.
    All rate limit counters are stored in application memory and
    reset when the server restarts. This is acceptable for single
    instance deployments where session continuity is not required.
    """
    enabled: bool = Field(
        default=True,
        description="Enable/disable rate limiting globally"
    )
    default_limit: str = Field(
        default="100/minute",
        description="Default rate limit for all endpoints"
    )
    status_limit: str = Field(
        default="60/minute",
        description="Rate limit for /status endpoint"
    )
    main_page_limit: str = Field(
        default="30/minute",
        description="Rate limit for main page /"
    )
    websocket_limit: str = Field(
        default="5/minute",
        description="Rate limit for WebSocket connections"
    )
    weather_api_limit: str = Field(
        default="10/hour",
        description="Rate limit for weather API calls"
    )
    web_search_limit: str = Field(
        default="5/hour",
        description="Rate limit for web search calls"
    )

    class Config:
        env_prefix = "RATE_LIMIT_"


def get_rate_limit_config() -> RateLimitConfig:
    """
    Load rate limit configuration from environment

    NOTE: In-memory storage only. All limits reset on server restart.
    """
    return RateLimitConfig(
        enabled=os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true",
        default_limit=os.getenv("RATE_LIMIT_DEFAULT", "100/minute"),
        status_limit=os.getenv("RATE_LIMIT_STATUS", "60/minute"),
        main_page_limit=os.getenv("RATE_LIMIT_MAIN_PAGE", "30/minute"),
        websocket_limit=os.getenv("RATE_LIMIT_WEBSOCKET", "5/minute"),
        weather_api_limit=os.getenv("RATE_LIMIT_WEATHER_API", "10/hour"),
        web_search_limit=os.getenv("RATE_LIMIT_WEB_SEARCH", "5/hour")
    )


# Redis support removed - using in-memory storage only
# This simplifies deployment for single instance use case
# Rate limit counters reset on server restart


def get_client_identifier(request: Request) -> str:
    """
    Get client identifier for rate limiting
    Uses X-Forwarded-For header if behind proxy, otherwise remote IP
    """
    # Check for X-Forwarded-For header (for proxied requests)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Get first IP in chain (original client)
        client_ip = forwarded_for.split(",")[0].strip()
    else:
        # Direct connection
        client_ip = get_remote_address(request)

    # Log client identifier for debugging
    logger.debug(f"Rate limit check for client: {client_ip}")
    return client_ip


def get_user_tier_from_request(request: Request) -> str:
    """
    Get user tier from request for tier-based rate limiting
    Integrates with JWT authentication middleware

    Returns:
        str: User tier ('free', 'premium', or 'enterprise')
    """
    try:
        # Try to import auth module (graceful degradation if not available)
        from app.middleware.auth import get_user_tier
        return get_user_tier(request)
    except ImportError:
        # Auth module not available, default to free tier
        logger.debug("Auth module not available, defaulting to free tier")
        return "free"
    except HTTPException:
        # A supplied invalid JWT is an authentication failure, not anonymous
        # free-tier access. Let FastAPI render the 401 response.
        raise
    except Exception as e:
        # Any other error, default to free tier
        logger.warning(f"Error getting user tier: {e}, defaulting to free tier")
        return "free"


def get_rate_limit_for_user_tier(request: Request, endpoint: str) -> str:
    """
    Get dynamic rate limit based on user tier and endpoint

    Args:
        request: FastAPI request object
        endpoint: Endpoint path

    Returns:
        str: Rate limit string (e.g., "20/minute")
    """
    tier = get_user_tier_from_request(request)

    # Import tier configuration
    try:
        from app.models.user import TIER_CONFIGURATIONS, UserTier

        tier_enum = UserTier(tier)
        tier_limits = TIER_CONFIGURATIONS[tier_enum]

        # Return tier-specific rate limit
        return tier_limits.rate_limit
    except (ImportError, ValueError, KeyError) as e:
        logger.warning(f"Error getting tier limits: {e}, using default")
        # Fallback to configured endpoint limit
        return get_rate_limit_for_endpoint(endpoint)


@dataclass(frozen=True)
class WebSocketAdmission:
    """Decision returned when a WebSocket session asks for admission."""

    allowed: bool
    reason: str
    tier: str
    rate_limit: str
    max_concurrent_sessions: int
    current_connections: int
    retry_after_seconds: Optional[int] = None


class WebSocketConnectionLimiter:
    """Enforce per-identity, tier-specific WebSocket admission limits.

    The rate limit counts accepted connection attempts in a sliding one-minute
    window. The concurrent limit counts currently running sessions. Both
    counters are keyed by a stable identity supplied by the WebSocket handler,
    rather than by tier, so changing a token's tier cannot bypass existing
    usage for that identity.

    This is intentionally in-memory, matching the application's existing
    single-instance rate limiter. A future multi-replica deployment should
    move this state to a shared store as one atomic operation.
    """

    WINDOW_SECONDS = 60.0

    def __init__(self, clock: Any = monotonic):
        self._clock = clock
        self._lock = Lock()
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._active_connections: dict[str, int] = defaultdict(int)

    @staticmethod
    def _tier_limits(tier: str) -> tuple[UserTier, Any]:
        """Return a validated tier and its limits, failing closed to free."""
        try:
            tier_enum = UserTier(tier)
        except (TypeError, ValueError):
            tier_enum = UserTier.FREE
        return tier_enum, TIER_CONFIGURATIONS[tier_enum]

    @staticmethod
    def _identity(identity: str) -> str:
        """Normalize an admission key without allowing blank identities."""
        value = str(identity or "").strip()
        return value or "anonymous:unknown"

    def _prune(self, attempts: deque[float], now: float) -> None:
        cutoff = now - self.WINDOW_SECONDS
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()

    async def acquire(self, identity: str, tier: str) -> WebSocketAdmission:
        """Atomically reserve a session slot if both tier limits allow it."""
        key = self._identity(identity)
        tier_enum, limits = self._tier_limits(tier)
        now = self._clock()

        with self._lock:
            attempts = self._attempts[key]
            self._prune(attempts, now)
            current_connections = self._active_connections[key]

            if current_connections >= limits.websocket_connections:
                return WebSocketAdmission(
                    allowed=False,
                    reason="concurrent_limit",
                    tier=tier_enum.value,
                    rate_limit=limits.rate_limit,
                    max_concurrent_sessions=limits.websocket_connections,
                    current_connections=current_connections,
                )

            rate_limit_count = int(limits.rate_limit.split("/", 1)[0])
            if len(attempts) >= rate_limit_count:
                retry_after = max(1, ceil(attempts[0] + self.WINDOW_SECONDS - now))
                return WebSocketAdmission(
                    allowed=False,
                    reason="rate_limit",
                    tier=tier_enum.value,
                    rate_limit=limits.rate_limit,
                    max_concurrent_sessions=limits.websocket_connections,
                    current_connections=current_connections,
                    retry_after_seconds=retry_after,
                )

            attempts.append(now)
            self._active_connections[key] = current_connections + 1
            return WebSocketAdmission(
                allowed=True,
                reason="allowed",
                tier=tier_enum.value,
                rate_limit=limits.rate_limit,
                max_concurrent_sessions=limits.websocket_connections,
                current_connections=current_connections + 1,
            )

    async def release(self, identity: str) -> None:
        """Release one active session slot; rate-limit history is retained."""
        key = self._identity(identity)
        with self._lock:
            current_connections = self._active_connections.get(key, 0)
            if current_connections <= 1:
                self._active_connections.pop(key, None)
            else:
                self._active_connections[key] = current_connections - 1

    def active_connections(self, identity: str) -> int:
        """Return the current active count, primarily for observability/tests."""
        key = self._identity(identity)
        with self._lock:
            return self._active_connections.get(key, 0)

    def reset(self) -> None:
        """Clear limiter state for process lifecycle management and tests."""
        with self._lock:
            self._attempts.clear()
            self._active_connections.clear()


# Initialize limiter with in-memory storage
# All rate limit counters stored in application memory
# Counters reset on server restart - acceptable for single instance deployment
logger.info("Initializing in-memory rate limiting (single instance deployment)")
limiter = Limiter(
    key_func=get_client_identifier,
    default_limits=[get_rate_limit_config().default_limit],
    headers_enabled=True,
    retry_after="http-date"
)


class RateLimitMiddleware:
    """
    Custom rate limit middleware with enhanced features
    - Prometheus metrics
    - Detailed logging
    - Graceful degradation
    """

    def __init__(self, app):
        self.app = app
        self.config = get_rate_limit_config()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Skip rate limiting if disabled
        if not self.config.enabled:
            await self.app(scope, receive, send)
            return

        # Extract path for metrics
        path = scope.get("path", "unknown")

        # Track request duration
        with request_duration.labels(endpoint=path).time():
            try:
                await self.app(scope, receive, send)
            except RateLimitExceeded as e:
                # Track rate limit violations
                client_ip = scope.get("client", ["unknown"])[0]
                rate_limit_exceeded.labels(
                    endpoint=path,
                    client_ip=client_ip
                ).inc()

                logger.warning(
                    f"Rate limit exceeded for {client_ip} on {path}"
                )
                raise


def create_rate_limiter():
    """Factory function to create configured rate limiter"""
    return limiter


def get_rate_limit_for_endpoint(endpoint: str) -> str:
    """
    Get appropriate rate limit string for specific endpoint
    """
    config = get_rate_limit_config()

    # Endpoint-specific rate limits
    limits = {
        "/status": config.status_limit,
        "/": config.main_page_limit,
        "/session": config.websocket_limit,
    }

    return limits.get(endpoint, config.default_limit)


# Redis health check stub - always returns healthy since we use in-memory storage
def check_redis_health() -> bool:
    """
    Health check stub for rate limiting.
    Always returns True since we use in-memory storage (no Redis).
    """
    return True


# Custom rate limit exceeded handler with enhanced error response
async def custom_rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """
    Custom handler for rate limit exceeded errors
    Provides detailed error messages and retry information
    """
    # Extract retry-after header
    retry_after = getattr(exc, "retry_after", None)

    # Build detailed error response
    error_detail = {
        "error": "Rate limit exceeded",
        "message": "Too many requests. Please try again later.",
        "retry_after_seconds": retry_after,
        "endpoint": str(request.url.path),
        "limit": str(exc)
    }

    logger.warning(
        f"Rate limit exceeded for {get_client_identifier(request)} "
        f"on {request.url.path}"
    )

    raise HTTPException(
        status_code=429,
        detail=error_detail,
        headers={
            "Retry-After": str(retry_after) if retry_after else "60"
        }
    )
