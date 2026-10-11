"""
JWT Authentication Middleware - Optional authentication with tier-based access
Implements London School TDD: behavior-driven with mock collaborators

Design:
- Anonymous users = free tier (default)
- A supplied JWT must be valid or authentication is rejected
- Valid access JWTs select a validated tier
- Token revocation support via Redis
"""
from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
import os
import logging
import hashlib
import redis.asyncio as redis
from uuid import uuid4

from app.models.user import UserTier, TIER_CONFIGURATIONS, TokenData, TierLimits

logger = logging.getLogger(__name__)

# The default secret that should NEVER be used in production
_DEFAULT_SECRET = "your-secret-key-here-change-in-production"
_JWT_SECRET_WARNING_LOGGED = False

# JWT Configuration from environment
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", _DEFAULT_SECRET)
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRATION_MINUTES", "120"))
JWT_REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))


def _is_using_default_secret() -> bool:
    """
    Check if JWT is configured with the publicly-known default secret.
    Returns True if JWT_SECRET_KEY is unset or equals the default string.
    """
    return not JWT_SECRET_KEY or JWT_SECRET_KEY == _DEFAULT_SECRET


def _warn_if_default_secret() -> None:
    """
    Log a one-time warning if using the default JWT secret.
    This should be called once at application startup.
    """
    global _JWT_SECRET_WARNING_LOGGED
    if _is_using_default_secret() and not _JWT_SECRET_WARNING_LOGGED:
        logger.error(
            "SECURITY WARNING: JWT_SECRET_KEY is using the default value or is unset. "
            "JWT token validation and creation will be disabled. "
            "Set JWT_SECRET_KEY environment variable to a secure random value. "
            "This allows attackers to forge tokens and impersonate users."
        )
        _JWT_SECRET_WARNING_LOGGED = True


# Log warning once at module import
_warn_if_default_secret()

# Redis for token revocation (optional)
REDIS_URL = os.getenv("REDIS_URL")

# Initialize Redis client for token revocation
_redis_client: Optional[redis.Redis] = None

if REDIS_URL:
    try:
        _redis_client = redis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5
        )
        logger.info("Redis initialized for token revocation")
    except Exception as e:
        logger.warning(f"Redis initialization failed, revocation disabled: {e}")


# HTTPBearer for optional authentication
security = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"}
    )


def extract_bearer_token(authorization_header: Optional[str]) -> Optional[str]:
    """Parse an optional Bearer header; a present malformed header is an error."""
    if authorization_header is None:
        return None

    parts = authorization_header.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise _unauthorized("Malformed Authorization header")
    return parts[1]


def extract_websocket_token(
    authorization_header: Optional[str],
    query_token: Optional[str]
) -> Optional[str]:
    """Extract a WebSocket JWT from one supported transport only.

    Browser WebSocket clients pass ``?token=...`` because the browser API does
    not allow setting an Authorization header. Other clients should use the
    standard Bearer header. Supplying both is ambiguous and rejected.
    """
    bearer_token = extract_bearer_token(authorization_header)
    if bearer_token is not None and query_token is not None:
        raise _unauthorized("Use one JWT transport per WebSocket connection")

    if query_token is not None:
        token = query_token.strip()
        if not token:
            raise _unauthorized("JWT token must not be empty")
        return token

    return bearer_token


def create_access_token(
    payload: Dict[str, Any],
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create JWT access token with expiration and unique ID

    Args:
        payload: Token payload (must include 'sub')
        expires_delta: Custom expiration time (default: 2 hours)

    Returns:
        Encoded JWT token string

    Raises:
        HTTPException: 500 if JWT_SECRET_KEY is using the default value
    """
    # Fail closed: refuse to mint tokens with default secret
    if _is_using_default_secret():
        logger.error("SECURITY: Refusing to create access token with default JWT secret")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT authentication is not properly configured. Set JWT_SECRET_KEY environment variable to a secure random value."
        )

    to_encode = payload.copy()

    # Set expiration
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)

    # Add standard claims
    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": str(uuid4()),  # Unique token ID for revocation
        "token_type": "access"
    })

    # Encode token
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def create_refresh_token(payload: Dict[str, Any]) -> str:
    """
    Create JWT refresh token with longer expiration

    Args:
        payload: Token payload (must include 'sub')

    Returns:
        Encoded JWT refresh token string

    Raises:
        HTTPException: 500 if JWT_SECRET_KEY is using the default value
    """
    # Fail closed: refuse to mint tokens with default secret
    if _is_using_default_secret():
        logger.error("SECURITY: Refusing to create refresh token with default JWT secret")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT authentication is not properly configured. Set JWT_SECRET_KEY environment variable to a secure random value."
        )

    to_encode = payload.copy()

    # Refresh tokens last 7 days
    expire = datetime.now(timezone.utc) + timedelta(days=JWT_REFRESH_TOKEN_EXPIRE_DAYS)

    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": str(uuid4()),
        "token_type": "refresh"
    })

    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def validate_token(token: str) -> Dict[str, Any]:
    """
    Validate JWT token and return decoded payload

    Args:
        token: JWT token string

    Returns:
        Decoded token payload

    Raises:
        HTTPException: 401 if token is invalid, expired, or revoked
                       401 if JWT_SECRET_KEY is using default value (security)
    """
    # Fail closed: refuse to validate tokens if using default secret
    if _is_using_default_secret():
        logger.warning("SECURITY: Rejecting token validation - default JWT secret in use")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT authentication is not properly configured",
            headers={"WWW-Authenticate": "Bearer"}
        )

    try:
        # Decode and verify signature
        payload = jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM]
        )

        # Require a subject and an explicit expiry. python-jose checks an
        # existing exp claim, but does not require one by default.
        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            logger.warning("Token missing 'sub' claim")
            raise _unauthorized("Invalid token: missing subject claim")

        expiration = payload.get("exp")
        if not isinstance(expiration, (int, float)) or isinstance(expiration, bool):
            logger.warning("Token missing a valid 'exp' claim")
            raise _unauthorized("Invalid token: missing or invalid expiration claim")

        token_type = payload.get("token_type", "access")
        if token_type not in ("access", "refresh"):
            logger.warning("Token has an unsupported token type")
            raise _unauthorized("Invalid token type")

        # Validate tier claims before any caller can grant tier-specific access.
        get_user_tier_from_token(payload)

        # Check if token is revoked (if Redis available)
        if _redis_client and "jti" in payload:
            try:
                is_revoked = _redis_client.get(f"revoked_token:{payload['jti']}")
                if is_revoked:
                    logger.warning(f"Revoked token attempted: {payload['jti']}")
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Token has been revoked",
                        headers={"WWW-Authenticate": "Bearer"}
                    )
            except redis.RedisError as e:
                logger.error(f"Redis error checking revocation: {e}")
                # Continue without revocation check if Redis fails

        return payload

    except jwt.ExpiredSignatureError:
        logger.warning("Expired token attempted")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"}
        )
    except (jwt.JWTError, TypeError, ValueError) as e:
        logger.warning(f"Invalid token: {e}")
        raise _unauthorized("Invalid token signature or format")


def validate_access_token(token: str) -> Dict[str, Any]:
    """Validate a JWT specifically for use as an API access credential."""
    payload = validate_token(token)
    if payload.get("token_type", "access") != "access":
        logger.warning("Non-access token used for authentication")
        raise _unauthorized("Invalid token type for authentication")
    return payload


def refresh_access_token(refresh_token: str) -> str:
    """
    Generate new access token from valid refresh token

    Args:
        refresh_token: Valid refresh token

    Returns:
        New access token

    Raises:
        HTTPException: 401 if refresh token is invalid or not refresh type
    """
    # Validate refresh token
    payload = validate_token(refresh_token)

    # Verify it's a refresh token
    if payload.get("token_type") != "refresh":
        logger.warning("Attempted to refresh with non-refresh token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type for refresh",
            headers={"WWW-Authenticate": "Bearer"}
        )

    # Create new access token with same user data
    new_payload = {
        "sub": payload["sub"],
        "tier": payload.get("tier", "premium")
    }

    return create_access_token(new_payload)


def revoke_token(jti: str, ttl: Optional[int] = None) -> None:
    """
    Revoke a token by its JTI (JWT ID)

    Args:
        jti: JWT ID to revoke
        ttl: Time to live in seconds (default: 7 days)
    """
    if not _redis_client:
        logger.warning("Token revocation attempted but Redis not available")
        return

    if ttl is None:
        ttl = JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60  # 7 days in seconds

    try:
        _redis_client.setex(f"revoked_token:{jti}", ttl, "1")
        logger.info(f"Token revoked: {jti}")
    except redis.RedisError as e:
        logger.error(f"Failed to revoke token: {e}")


def get_user_tier_from_token(token_data: Dict[str, Any]) -> UserTier:
    """
    Extract user tier from token payload

    Args:
        token_data: Decoded JWT payload

    Returns:
        UserTier enum value
    """
    # Keep compatibility with previously-issued signed tokens that omitted
    # tier; explicit unknown values are invalid and must not gain premium access.
    tier_str = token_data.get("tier", UserTier.PREMIUM.value)

    # Validate tier value
    try:
        return UserTier(tier_str)
    except (TypeError, ValueError):
        logger.warning("Invalid tier claim in token")
        raise _unauthorized("Invalid token: unsupported tier")


def get_tier_limits(tier: str) -> Dict[str, Any]:
    """
    Get limits for a specific tier

    Args:
        tier: Tier name (free/premium/enterprise)

    Returns:
        Dict with rate_limit, session_budget, session_timeout
    """
    try:
        tier_enum = UserTier(tier)
        limits = TIER_CONFIGURATIONS[tier_enum]

        return {
            "rate_limit": limits.rate_limit,
            "session_budget": limits.session_budget,
            "session_timeout": limits.session_timeout,
            "websocket_connections": limits.websocket_connections
        }
    except (ValueError, KeyError):
        # Invalid tier, return free tier limits
        logger.warning(f"Invalid tier requested: {tier}, returning free tier")
        limits = TIER_CONFIGURATIONS[UserTier.FREE]
        return {
            "rate_limit": limits.rate_limit,
            "session_budget": limits.session_budget,
            "session_timeout": limits.session_timeout,
            "websocket_connections": limits.websocket_connections
        }


def get_rate_limit_for_tier(tier: str) -> str:
    """
    Get rate limit string for a tier

    Args:
        tier: Tier name

    Returns:
        Rate limit string (e.g., "5/minute")
    """
    limits = get_tier_limits(tier)
    return limits["rate_limit"]


def get_user_tier(request: Request) -> str:
    """
    Get user tier from request (anonymous requests use the free tier)

    Args:
        request: FastAPI request object

    Returns:
        Tier string (free/premium/enterprise)
    """
    # No credential means anonymous free-tier access. A supplied but malformed
    # or invalid credential is rejected instead of silently downgraded.
    auth_header = request.headers.get("Authorization")
    if auth_header is None:
        return UserTier.FREE.value

    token = extract_bearer_token(auth_header)
    assert token is not None  # a present, well-formed Bearer header has a token
    payload = validate_access_token(token)
    return get_user_tier_from_token(payload).value


def get_user_tier_with_fallback(request: Request) -> str:
    """
    Get the tier, defaulting to free only when no credential was supplied.

    Args:
        request: FastAPI request object

    Returns:
        Tier string; invalid supplied credentials raise HTTP 401
    """
    return get_user_tier(request)


# Advanced security features

def create_access_token_with_binding(
    payload: Dict[str, Any],
    user_agent: str,
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create token bound to user agent (session hijacking prevention)

    Args:
        payload: Token payload
        user_agent: User agent string from request
        expires_delta: Custom expiration

    Returns:
        Token with user agent hash binding
    """
    # Hash user agent for privacy
    ua_hash = hashlib.sha256(user_agent.encode()).hexdigest()[:16]

    payload_with_binding = payload.copy()
    payload_with_binding["ua_hash"] = ua_hash

    return create_access_token(payload_with_binding, expires_delta)


def validate_token_with_binding(token: str, user_agent: str) -> Dict[str, Any]:
    """
    Validate token with user agent binding check

    Args:
        token: JWT token
        user_agent: Current user agent

    Returns:
        Decoded payload if valid

    Raises:
        HTTPException: 401 if user agent doesn't match
    """
    payload = validate_access_token(token)

    # Check user agent hash
    if "ua_hash" in payload:
        current_ua_hash = hashlib.sha256(user_agent.encode()).hexdigest()[:16]
        if payload["ua_hash"] != current_ua_hash:
            logger.warning("User agent mismatch detected")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session binding validation failed",
                headers={"WWW-Authenticate": "Bearer"}
            )

    return payload


def create_access_token_with_ip_binding(
    payload: Dict[str, Any],
    client_ip: str,
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Create token bound to IP address

    Args:
        payload: Token payload
        client_ip: Client IP address
        expires_delta: Custom expiration

    Returns:
        Token with IP hash binding
    """
    # Hash IP for privacy
    ip_hash = hashlib.sha256(client_ip.encode()).hexdigest()[:16]

    payload_with_ip = payload.copy()
    payload_with_ip["ip_hash"] = ip_hash

    return create_access_token(payload_with_ip, expires_delta)


def validate_token_with_ip(token: str, client_ip: str) -> Dict[str, Any]:
    """
    Validate token with IP binding check

    Args:
        token: JWT token
        client_ip: Current client IP

    Returns:
        Decoded payload if valid

    Raises:
        HTTPException: 401 if IP doesn't match
    """
    payload = validate_access_token(token)

    # Check IP hash
    if "ip_hash" in payload:
        current_ip_hash = hashlib.sha256(client_ip.encode()).hexdigest()[:16]
        if payload["ip_hash"] != current_ip_hash:
            logger.warning(f"IP address mismatch detected")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="IP binding validation failed",
                headers={"WWW-Authenticate": "Bearer"}
            )

    return payload


# FastAPI Dependencies

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Dict[str, Any]:
    """
    FastAPI dependency: Extract authenticated user from JWT token

    Args:
        credentials: HTTP Bearer credentials

    Returns:
        User data from token

    Raises:
        HTTPException: 401 if authentication fails
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"}
        )

    token = credentials.credentials
    return validate_access_token(token)


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[Dict[str, Any]]:
    """
    FastAPI dependency: Optional authentication (returns None for anonymous)

    Args:
        credentials: HTTP Bearer credentials (optional)

    Returns:
        User data from token or None for anonymous
    """
    if not credentials:
        return None

    return validate_access_token(credentials.credentials)


class JWTAuthMiddleware:
    """
    JWT Authentication Middleware for FastAPI
    Provides optional authentication with tier-based access
    """

    def __init__(self):
        self.security = HTTPBearer(auto_error=False)

    def get_user_tier(self, request: Request) -> str:
        """Get user tier from request"""
        return get_user_tier(request)

    async def __call__(self, request: Request, call_next):
        """Middleware handler"""
        # Add user tier to request state
        request.state.user_tier = self.get_user_tier(request)

        response = await call_next(request)
        return response
