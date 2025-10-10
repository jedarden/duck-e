"""
User models and tier definitions for authentication system
"""
from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class UserTier(str, Enum):
    """User tier levels with associated permissions and limits"""
    FREE = "free"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"


class TierLimits(BaseModel):
    """Rate limits and budgets for each tier"""
    rate_limit: str = Field(description="Rate limit string (e.g., '5/minute')")
    session_budget: float = Field(description="Session budget in dollars")
    session_timeout: int = Field(description="Session timeout in seconds")
    websocket_connections: int = Field(description="Max concurrent WebSocket connections")

    class Config:
        frozen = True  # Immutable


# Tier configuration (immutable)
TIER_CONFIGURATIONS = {
    UserTier.FREE: TierLimits(
        rate_limit="5/minute",
        session_budget=5.0,
        session_timeout=30 * 60,  # 30 minutes
        websocket_connections=1
    ),
    UserTier.PREMIUM: TierLimits(
        rate_limit="20/minute",
        session_budget=20.0,
        session_timeout=2 * 60 * 60,  # 2 hours
        websocket_connections=5
    ),
    UserTier.ENTERPRISE: TierLimits(
        rate_limit="100/minute",
        session_budget=100.0,
        session_timeout=8 * 60 * 60,  # 8 hours
        websocket_connections=20
    )
}


class TokenData(BaseModel):
    """JWT token payload data"""
    sub: str = Field(description="Subject (user identifier)")
    tier: UserTier = Field(default=UserTier.PREMIUM, description="User tier")
    exp: datetime = Field(description="Expiration timestamp")
    iat: Optional[datetime] = Field(default=None, description="Issued at timestamp")
    jti: Optional[str] = Field(default=None, description="JWT ID for revocation tracking")
    token_type: Optional[str] = Field(default="access", description="Token type (access/refresh)")


class User(BaseModel):
    """User model with authentication data"""
    id: str = Field(description="Unique user identifier")
    tier: UserTier = Field(default=UserTier.FREE, description="User tier level")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_login: Optional[datetime] = None

    def get_limits(self) -> TierLimits:
        """Get tier limits for this user"""
        return TIER_CONFIGURATIONS[self.tier]

    class Config:
        use_enum_values = True
