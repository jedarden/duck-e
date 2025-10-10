"""
Middleware package for DUCK-E
Exports security, rate limiting, cost protection, and authentication components
"""
from .rate_limiting import (
    limiter,
    RateLimitMiddleware,
    RateLimitConfig,
    get_rate_limit_config,
    get_rate_limit_for_endpoint,
    get_rate_limit_for_user_tier,
    custom_rate_limit_exceeded_handler,
    check_redis_health as check_rate_limit_redis_health
)

from .cost_protection import (
    CostProtectionMiddleware,
    SessionCostTracker,
    CostProtectionConfig,
    get_cost_config,
    get_cost_tracker
)

from .security_headers import SecurityHeadersMiddleware, create_security_headers_middleware
from .cors_config import CORSConfig, configure_cors, get_cors_config
from .websocket_validator import (
    WebSocketOriginValidator,
    WebSocketSecurityMiddleware,
    create_websocket_validator,
    get_websocket_security_middleware
)

# Authentication components (optional)
try:
    from .auth import (
        JWTAuthMiddleware,
        create_access_token,
        create_refresh_token,
        validate_token,
        get_user_tier,
        get_tier_limits,
        get_current_user,
        get_current_user_optional,
        refresh_access_token,
        revoke_token
    )
    _auth_available = True
except ImportError:
    _auth_available = False

__all__ = [
    # Rate limiting
    "limiter",
    "RateLimitMiddleware",
    "RateLimitConfig",
    "get_rate_limit_config",
    "get_rate_limit_for_endpoint",
    "get_rate_limit_for_user_tier",
    "custom_rate_limit_exceeded_handler",
    "check_rate_limit_redis_health",

    # Cost protection
    "CostProtectionMiddleware",
    "SessionCostTracker",
    "CostProtectionConfig",
    "get_cost_config",
    "get_cost_tracker",

    # Security
    "SecurityHeadersMiddleware",
    "create_security_headers_middleware",
    "CORSConfig",
    "configure_cors",
    "get_cors_config",
    "WebSocketOriginValidator",
    "WebSocketSecurityMiddleware",
    "create_websocket_validator",
    "get_websocket_security_middleware"
]

# Add auth exports if available
if _auth_available:
    __all__.extend([
        "JWTAuthMiddleware",
        "create_access_token",
        "create_refresh_token",
        "validate_token",
        "get_user_tier",
        "get_tier_limits",
        "get_current_user",
        "get_current_user_optional",
        "refresh_access_token",
        "revoke_token"
    ])
