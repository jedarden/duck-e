"""
User models and tier definitions for authentication system
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

from app.tier_policy import TIER_CONFIGURATIONS, TierLimits, UserTier


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
