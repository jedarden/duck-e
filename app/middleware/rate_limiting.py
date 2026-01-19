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
from typing import Optional, Callable
import os
import logging
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram

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
