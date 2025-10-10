"""
JWT Authentication Middleware - Optional authentication with tier-based access
Implements London School TDD: behavior-driven with mock collaborators

Design:
- Anonymous users = free tier (default)
- JWT authenticated = premium/enterprise tier
- Graceful degradation on token failures
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

# JWT Configuration from environment
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-here-change-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRATION_MINUTES", "120"))
JWT_REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))

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
    """
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
    """
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
    """
    try:
        # Decode and verify signature
        payload = jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM]
        )

        # Verify required claims
        if "sub" not in payload:
            logger.warning("Token missing 'sub' claim")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing subject claim",
                headers={"WWW-Authenticate": "Bearer"}
            )

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
    except jwt.JWTError as e:
        logger.warning(f"Invalid token: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token signature or format",
            headers={"WWW-Authenticate": "Bearer"}
        )


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
    tier_str = token_data.get("tier", "premium")

    # Validate tier value
    try:
        return UserTier(tier_str)
    except ValueError:
        logger.warning(f"Invalid tier in token: {tier_str}, defaulting to premium")
        return UserTier.PREMIUM


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
    Get user tier from request (with graceful fallback to free tier)

    Args:
        request: FastAPI request object

    Returns:
        Tier string (free/premium/enterprise)
    """
    # Extract Authorization header
    auth_header = request.headers.get("Authorization", "")

    # No header or empty = free tier
    if not auth_header or not auth_header.strip():
        return UserTier.FREE.value

    # Parse Bearer token
    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        logger.debug("Invalid authorization header format")
        return UserTier.FREE.value

    token = parts[1]
    if not token or not token.strip():
        return UserTier.FREE.value

    # Try to validate token
    try:
        payload = validate_token(token)
        tier = get_user_tier_from_token(payload)
        return tier.value
    except HTTPException:
        # Invalid/expired token = gracefully fall back to free tier
        logger.debug("Token validation failed, falling back to free tier")
        return UserTier.FREE.value


def get_user_tier_with_fallback(request: Request) -> str:
    """
    Get user tier with explicit fallback handling (alias for get_user_tier)

    Args:
        request: FastAPI request object

    Returns:
        Tier string, never raises exception
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
    payload = validate_token(token)

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
    payload = validate_token(token)

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
    return validate_token(token)


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

    try:
        token = credentials.credentials
        return validate_token(token)
    except HTTPException:
        # Invalid token = anonymous user
        return None


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
