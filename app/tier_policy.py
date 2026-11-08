"""Canonical tier policy and claim resolution for JWT-backed sessions.

Only a missing tier claim on a signed legacy JWT selects premium. Anonymous
sessions select free; an explicit unsupported tier is an invalid claim.
"""

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class UserTier(str, Enum):
    FREE = "free"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"


@dataclass(frozen=True, slots=True)
class TierLimits:
    websocket_attempts_per_minute: int
    websocket_connections: int
    session_budget: float
    session_timeout: int

    @property
    def rate_limit(self) -> str:
        return f"{self.websocket_attempts_per_minute}/minute"


WEBSOCKET_WINDOW_SECONDS = 60.0
TIER_CONFIGURATIONS: Mapping[UserTier, TierLimits] = MappingProxyType({
    UserTier.FREE: TierLimits(5, 1, 5.0, 30 * 60),
    UserTier.PREMIUM: TierLimits(20, 5, 20.0, 2 * 60 * 60),
    UserTier.ENTERPRISE: TierLimits(100, 20, 100.0, 8 * 60 * 60),
})


class InvalidTierClaim(ValueError):
    """A present tier claim is not one of the supported exact values."""


class InvalidSubjectClaim(ValueError):
    """A JWT has no usable stable subject."""


def parse_tier(value: object) -> UserTier:
    """Parse one exact tier value; never silently grant another tier's limits."""
    if not isinstance(value, (str, UserTier)):
        raise InvalidTierClaim("unsupported tier")
    try:
        return UserTier(value)
    except ValueError as exc:
        raise InvalidTierClaim("unsupported tier") from exc


def resolve_jwt_tier(claims: Mapping[str, object]) -> UserTier:
    """Use premium only for signed legacy JWTs with no tier claim."""
    return parse_tier(claims.get("tier", UserTier.PREMIUM.value))


def resolve_jwt_subject(claims: Mapping[str, object]) -> str:
    """Return the case-sensitive stable subject, trimming surrounding space."""
    value = claims.get("sub")
    if not isinstance(value, str) or not value.strip():
        raise InvalidSubjectClaim("missing subject claim")
    return value.strip()
