"""Pure unit tests for the shared JWT tier and subject policy."""

from dataclasses import FrozenInstanceError

import pytest

from app.tier_policy import (
    InvalidSubjectClaim,
    InvalidTierClaim,
    TIER_CONFIGURATIONS,
    UserTier,
    parse_tier,
    resolve_jwt_subject,
    resolve_jwt_tier,
)


@pytest.mark.parametrize(
    ("tier", "attempts", "sessions"),
    [
        (UserTier.FREE, 5, 1),
        (UserTier.PREMIUM, 20, 5),
        (UserTier.ENTERPRISE, 100, 20),
    ],
)
def test_each_supported_tier_has_its_own_websocket_policy(tier, attempts, sessions):
    assert parse_tier(tier.value) is tier
    assert resolve_jwt_tier({"tier": tier.value}) is tier
    limits = TIER_CONFIGURATIONS[tier]
    assert limits.websocket_attempts_per_minute == attempts
    assert limits.rate_limit == f"{attempts}/minute"
    assert limits.websocket_connections == sessions


def test_policy_covers_every_supported_tier():
    assert set(TIER_CONFIGURATIONS) == set(UserTier)


def test_only_absent_tier_claim_selects_legacy_premium():
    assert resolve_jwt_tier({"sub": "user-1"}) is UserTier.PREMIUM


@pytest.mark.parametrize("value", [None, "", "Premium", " premium ", "unknown", 1, True, []])
def test_present_unsupported_tier_is_rejected(value):
    with pytest.raises(InvalidTierClaim):
        resolve_jwt_tier({"tier": value})


def test_subject_is_stable_across_claims_and_surrounding_whitespace():
    assert resolve_jwt_subject({"sub": "  User-42  ", "email": "first@example.com"}) == "User-42"
    assert resolve_jwt_subject({"sub": "User-42", "tier": "free"}) == "User-42"


@pytest.mark.parametrize("claims", [{}, {"sub": None}, {"sub": ""}, {"sub": " \t "}, {"sub": 42}, {"sub": ["user"]}])
def test_missing_or_invalid_subject_is_rejected(claims):
    with pytest.raises(InvalidSubjectClaim):
        resolve_jwt_subject(claims)


def test_policy_cannot_be_mutated_or_shared_across_tiers():
    free = TIER_CONFIGURATIONS[UserTier.FREE]
    premium = TIER_CONFIGURATIONS[UserTier.PREMIUM]
    with pytest.raises(TypeError):
        TIER_CONFIGURATIONS[UserTier.FREE] = premium
    with pytest.raises(FrozenInstanceError):
        free.websocket_attempts_per_minute = 100
    assert free.websocket_attempts_per_minute == 5
    assert premium.websocket_attempts_per_minute == 20
