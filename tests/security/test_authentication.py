"""
TDD London School: Authentication Tests (RED Phase)
Tests MUST fail initially - implementation comes after

Test Coverage:
- Anonymous user access (free tier)
- JWT token validation (premium tier)
- Expired/malformed token rejection
- User tier detection
- Token refresh mechanism
- Security features (CSRF, session hijacking)
"""
import pytest
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from typing import Optional


# Test Configuration
TEST_SECRET_KEY = "test-secret-key-for-jwt-validation-only"
TEST_ALGORITHM = "HS256"
TEST_ACCESS_TOKEN_EXPIRE_MINUTES = 120
TEST_REFRESH_TOKEN_EXPIRE_DAYS = 7


class TestJWTTokenGeneration:
    """Test JWT token creation and validation - Mock collaborators"""

    def test_create_access_token_with_valid_payload(self):
        """RED: Should create access token with user data and expiration"""
        # This will fail until we implement create_access_token
        from app.middleware.auth import create_access_token

        payload = {"sub": "user123", "tier": "premium"}
        token = create_access_token(payload)

        assert token is not None
        assert isinstance(token, str)

        # Decode and verify
        decoded = jwt.decode(token, TEST_SECRET_KEY, algorithms=[TEST_ALGORITHM])
        assert decoded["sub"] == "user123"
        assert decoded["tier"] == "premium"
        assert "exp" in decoded

    def test_create_access_token_with_custom_expiration(self):
        """RED: Should support custom token expiration times"""
        from app.middleware.auth import create_access_token

        payload = {"sub": "user456"}
        custom_expire = timedelta(minutes=30)
        token = create_access_token(payload, expires_delta=custom_expire)

        decoded = jwt.decode(token, TEST_SECRET_KEY, algorithms=[TEST_ALGORITHM])
        exp_time = datetime.fromtimestamp(decoded["exp"], tz=timezone.utc)
        now = datetime.now(timezone.utc)

        # Should expire in approximately 30 minutes
        assert (exp_time - now).total_seconds() < 1900  # 31.6 min
        assert (exp_time - now).total_seconds() > 1700  # 28.3 min

    def test_create_refresh_token_with_longer_expiration(self):
        """RED: Refresh tokens should have longer expiration than access tokens"""
        from app.middleware.auth import create_refresh_token

        payload = {"sub": "user789"}
        refresh_token = create_refresh_token(payload)

        decoded = jwt.decode(refresh_token, TEST_SECRET_KEY, algorithms=[TEST_ALGORITHM])
        exp_time = datetime.fromtimestamp(decoded["exp"], tz=timezone.utc)
        now = datetime.now(timezone.utc)

        # Should expire in approximately 7 days
        days_until_expiry = (exp_time - now).days
        assert days_until_expiry >= 6 and days_until_expiry <= 8


class TestAnonymousUserTier:
    """Test anonymous user defaults to free tier - behavior verification"""

    def test_anonymous_user_defaults_to_free_tier(self):
        """RED: Users without token should be assigned free tier"""
        from app.middleware.auth import get_user_tier

        # Mock HTTP request without Authorization header
        mock_request = Mock()
        mock_request.headers = {}

        tier = get_user_tier(mock_request)
        assert tier == "free"

    def test_anonymous_user_receives_free_tier_limits(self):
        """RED: Free tier should have 5 conn/min and $5 budget"""
        from app.middleware.auth import get_tier_limits

        limits = get_tier_limits("free")

        assert limits["rate_limit"] == "5/minute"
        assert limits["session_budget"] == 5.0
        assert limits["session_timeout"] == 30 * 60  # 30 minutes in seconds

    def test_missing_authorization_header_returns_free_tier(self):
        """RED: No Authorization header = free tier (graceful degradation)"""
        from app.middleware.auth import JWTAuthMiddleware

        mock_request = Mock()
        mock_request.headers = {}

        auth_middleware = JWTAuthMiddleware()
        tier = auth_middleware.get_user_tier(mock_request)

        assert tier == "free"


class TestJWTTokenValidation:
    """Test JWT validation for premium tier - interaction testing"""

    def test_valid_jwt_token_returns_premium_tier(self):
        """RED: Valid JWT should authenticate as premium user"""
        from app.middleware.auth import validate_token, get_user_tier_from_token

        # Create valid token
        payload = {
            "sub": "premium_user_123",
            "tier": "premium",
            "exp": datetime.now(timezone.utc) + timedelta(hours=2)
        }
        token = jwt.encode(payload, TEST_SECRET_KEY, algorithm=TEST_ALGORITHM)

        # Validate token
        user_data = validate_token(token)
        assert user_data is not None
        assert user_data["sub"] == "premium_user_123"

        # Extract tier
        tier = get_user_tier_from_token(user_data)
        assert tier == "premium"

    def test_premium_tier_receives_higher_limits(self):
        """RED: Premium tier should have 20 conn/min and $20 budget"""
        from app.middleware.auth import get_tier_limits

        limits = get_tier_limits("premium")

        assert limits["rate_limit"] == "20/minute"
        assert limits["session_budget"] == 20.0
        assert limits["session_timeout"] == 2 * 60 * 60  # 2 hours

    def test_enterprise_tier_receives_maximum_limits(self):
        """RED: Enterprise tier should have 100 conn/min and $100 budget"""
        from app.middleware.auth import get_tier_limits

        limits = get_tier_limits("enterprise")

        assert limits["rate_limit"] == "100/minute"
        assert limits["session_budget"] == 100.0
        assert limits["session_timeout"] == 8 * 60 * 60  # 8 hours


class TestExpiredTokenRejection:
    """Test expired token handling - mock-driven development"""

    def test_expired_token_raises_401_unauthorized(self):
        """RED: Expired tokens must be rejected with 401 status"""
        from app.middleware.auth import validate_token

        # Create expired token
        payload = {
            "sub": "expired_user",
            "tier": "premium",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1)  # Expired 1 hour ago
        }
        token = jwt.encode(payload, TEST_SECRET_KEY, algorithm=TEST_ALGORITHM)

        # Should raise HTTPException with 401 status
        with pytest.raises(HTTPException) as exc_info:
            validate_token(token)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "expired" in str(exc_info.value.detail).lower()

    def test_expired_token_falls_back_to_free_tier(self):
        """RED: Expired token should gracefully degrade to free tier"""
        from app.middleware.auth import get_user_tier_with_fallback

        expired_payload = {
            "sub": "user_with_expired_token",
            "tier": "premium",
            "exp": datetime.now(timezone.utc) - timedelta(minutes=5)
        }
        expired_token = jwt.encode(expired_payload, TEST_SECRET_KEY, algorithm=TEST_ALGORITHM)

        mock_request = Mock()
        mock_request.headers = {"Authorization": f"Bearer {expired_token}"}

        # Should fall back to free tier instead of failing
        tier = get_user_tier_with_fallback(mock_request)
        assert tier == "free"


class TestMalformedTokenRejection:
    """Test invalid token handling - behavior verification"""

    def test_malformed_token_raises_401(self):
        """RED: Invalid token format should raise 401"""
        from app.middleware.auth import validate_token

        malformed_token = "this.is.not.a.valid.jwt.token"

        with pytest.raises(HTTPException) as exc_info:
            validate_token(malformed_token)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_token_with_invalid_signature_rejected(self):
        """RED: Token with wrong signature must be rejected"""
        from app.middleware.auth import validate_token

        # Create token with different secret
        wrong_secret = "wrong-secret-key"
        payload = {
            "sub": "attacker",
            "tier": "enterprise",  # Trying to fake enterprise tier
            "exp": datetime.now(timezone.utc) + timedelta(hours=1)
        }
        tampered_token = jwt.encode(payload, wrong_secret, algorithm=TEST_ALGORITHM)

        with pytest.raises(HTTPException) as exc_info:
            validate_token(tampered_token)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "signature" in str(exc_info.value.detail).lower() or "invalid" in str(exc_info.value.detail).lower()

    def test_token_without_required_claims_rejected(self):
        """RED: Token missing 'sub' claim should be rejected"""
        from app.middleware.auth import validate_token

        # Token without 'sub' (subject) claim
        incomplete_payload = {
            "tier": "premium",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1)
        }
        incomplete_token = jwt.encode(incomplete_payload, TEST_SECRET_KEY, algorithm=TEST_ALGORITHM)

        with pytest.raises(HTTPException) as exc_info:
            validate_token(incomplete_token)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


class TestUserTierDetection:
    """Test tier extraction from JWT - contract testing"""

    def test_tier_claim_in_token_is_respected(self):
        """RED: Token tier claim should determine user tier"""
        from app.middleware.auth import get_user_tier_from_token

        token_data = {
            "sub": "user123",
            "tier": "enterprise"
        }

        tier = get_user_tier_from_token(token_data)
        assert tier == "enterprise"

    def test_missing_tier_claim_defaults_to_premium(self):
        """RED: Valid token without tier claim defaults to premium"""
        from app.middleware.auth import get_user_tier_from_token

        token_data = {
            "sub": "user456"
            # No tier claim
        }

        tier = get_user_tier_from_token(token_data)
        assert tier == "premium"  # Authenticated but no tier = premium

    def test_invalid_tier_claim_defaults_to_premium(self):
        """RED: Invalid tier values should default to premium"""
        from app.middleware.auth import get_user_tier_from_token

        token_data = {
            "sub": "user789",
            "tier": "ultra-mega-premium"  # Invalid tier
        }

        tier = get_user_tier_from_token(token_data)
        assert tier == "premium"


class TestTokenRefreshMechanism:
    """Test token refresh workflow - collaboration patterns"""

    def test_refresh_token_generates_new_access_token(self):
        """RED: Valid refresh token should generate new access token"""
        from app.middleware.auth import refresh_access_token, validate_token

        # Create refresh token
        refresh_payload = {
            "sub": "user123",
            "tier": "premium",
            "token_type": "refresh",
            "exp": datetime.now(timezone.utc) + timedelta(days=7)
        }
        refresh_token = jwt.encode(refresh_payload, TEST_SECRET_KEY, algorithm=TEST_ALGORITHM)

        # Generate new access token
        new_access_token = refresh_access_token(refresh_token)

        assert new_access_token is not None
        assert new_access_token != refresh_token

        # Validate new access token
        decoded = validate_token(new_access_token)
        assert decoded["sub"] == "user123"
        assert decoded["tier"] == "premium"

    def test_access_token_cannot_be_used_for_refresh(self):
        """RED: Regular access tokens should not work for refresh"""
        from app.middleware.auth import refresh_access_token

        # Create regular access token (not refresh)
        access_payload = {
            "sub": "user456",
            "tier": "premium",
            "token_type": "access",
            "exp": datetime.now(timezone.utc) + timedelta(hours=2)
        }
        access_token = jwt.encode(access_payload, TEST_SECRET_KEY, algorithm=TEST_ALGORITHM)

        # Should reject access token for refresh
        with pytest.raises(HTTPException) as exc_info:
            refresh_access_token(access_token)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_expired_refresh_token_cannot_generate_new_token(self):
        """RED: Expired refresh tokens should be rejected"""
        from app.middleware.auth import refresh_access_token

        expired_refresh_payload = {
            "sub": "user789",
            "tier": "premium",
            "token_type": "refresh",
            "exp": datetime.now(timezone.utc) - timedelta(days=1)  # Expired
        }
        expired_refresh_token = jwt.encode(expired_refresh_payload, TEST_SECRET_KEY, algorithm=TEST_ALGORITHM)

        with pytest.raises(HTTPException) as exc_info:
            refresh_access_token(expired_refresh_token)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


class TestRateLimitTierIntegration:
    """Test rate limiting integration with auth tiers"""

    def test_free_tier_rate_limit_applied(self):
        """RED: Free tier should enforce 5/minute rate limit"""
        from app.middleware.auth import get_rate_limit_for_tier

        rate_limit = get_rate_limit_for_tier("free")
        assert rate_limit == "5/minute"

    def test_premium_tier_rate_limit_applied(self):
        """RED: Premium tier should enforce 20/minute rate limit"""
        from app.middleware.auth import get_rate_limit_for_tier

        rate_limit = get_rate_limit_for_tier("premium")
        assert rate_limit == "20/minute"

    def test_enterprise_tier_rate_limit_applied(self):
        """RED: Enterprise tier should enforce 100/minute rate limit"""
        from app.middleware.auth import get_rate_limit_for_tier

        rate_limit = get_rate_limit_for_tier("enterprise")
        assert rate_limit == "100/minute"


class TestSecurityFeatures:
    """Test security hardening features - mock collaborators"""

    def test_token_includes_jti_for_revocation(self):
        """RED: Tokens should include unique JTI (JWT ID) for revocation tracking"""
        from app.middleware.auth import create_access_token

        payload = {"sub": "user123", "tier": "premium"}
        token = create_access_token(payload)

        decoded = jwt.decode(token, TEST_SECRET_KEY, algorithms=[TEST_ALGORITHM])
        assert "jti" in decoded  # JWT ID for revocation
        assert isinstance(decoded["jti"], str)
        assert len(decoded["jti"]) > 0

    def test_revoked_token_is_rejected(self):
        """RED: Revoked tokens should be rejected even if valid"""
        from app.middleware.auth import validate_token, revoke_token

        # Create valid token
        payload = {
            "sub": "user456",
            "tier": "premium",
            "jti": "unique-token-id-123",
            "exp": datetime.now(timezone.utc) + timedelta(hours=2)
        }
        token = jwt.encode(payload, TEST_SECRET_KEY, algorithm=TEST_ALGORITHM)

        # Revoke the token
        revoke_token("unique-token-id-123")

        # Should reject revoked token
        with pytest.raises(HTTPException) as exc_info:
            validate_token(token)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "revoked" in str(exc_info.value.detail).lower()

    def test_token_includes_issued_at_timestamp(self):
        """RED: Tokens should include 'iat' (issued at) timestamp"""
        from app.middleware.auth import create_access_token

        before_creation = datetime.now(timezone.utc)
        payload = {"sub": "user789", "tier": "enterprise"}
        token = create_access_token(payload)
        after_creation = datetime.now(timezone.utc)

        decoded = jwt.decode(token, TEST_SECRET_KEY, algorithms=[TEST_ALGORITHM])
        assert "iat" in decoded

        issued_at = datetime.fromtimestamp(decoded["iat"], tz=timezone.utc)
        assert before_creation <= issued_at <= after_creation


class TestCORSWithCredentials:
    """Test CORS configuration for authenticated requests"""

    def test_cors_allows_credentials_for_authenticated_routes(self):
        """RED: CORS should allow credentials for auth endpoints"""
        # This tests the CORS configuration update
        # Will verify in integration tests
        pass  # Placeholder for integration test

    def test_preflight_request_accepts_authorization_header(self):
        """RED: CORS preflight should accept Authorization header"""
        # Integration test placeholder
        pass


class TestSessionHijackingPrevention:
    """Test session security features"""

    def test_token_bound_to_user_agent(self):
        """RED: Tokens should validate user agent to prevent session hijacking"""
        from app.middleware.auth import create_access_token_with_binding, validate_token_with_binding

        payload = {"sub": "user123", "tier": "premium"}
        user_agent = "Mozilla/5.0 (Test Browser)"

        token = create_access_token_with_binding(payload, user_agent)

        # Valid with correct user agent
        decoded = validate_token_with_binding(token, user_agent)
        assert decoded["sub"] == "user123"

        # Invalid with different user agent
        different_user_agent = "Different Browser"
        with pytest.raises(HTTPException) as exc_info:
            validate_token_with_binding(token, different_user_agent)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED

    def test_token_bound_to_ip_address(self):
        """RED: Tokens should optionally bind to IP address"""
        from app.middleware.auth import create_access_token_with_ip_binding, validate_token_with_ip

        payload = {"sub": "user456", "tier": "enterprise"}
        client_ip = "192.168.1.100"

        token = create_access_token_with_ip_binding(payload, client_ip)

        # Valid with correct IP
        decoded = validate_token_with_ip(token, client_ip)
        assert decoded["sub"] == "user456"

        # Invalid with different IP
        different_ip = "10.0.0.1"
        with pytest.raises(HTTPException) as exc_info:
            validate_token_with_ip(token, different_ip)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


class TestAuthenticationBypassPrevention:
    """Test security against authentication bypass attempts"""

    def test_empty_authorization_header_defaults_to_free_tier(self):
        """RED: Empty Authorization header should not bypass auth"""
        from app.middleware.auth import get_user_tier

        mock_request = Mock()
        mock_request.headers = {"Authorization": ""}

        tier = get_user_tier(mock_request)
        assert tier == "free"

    def test_bearer_without_token_defaults_to_free_tier(self):
        """RED: 'Bearer' without token should default to free tier"""
        from app.middleware.auth import get_user_tier

        mock_request = Mock()
        mock_request.headers = {"Authorization": "Bearer "}

        tier = get_user_tier(mock_request)
        assert tier == "free"

    def test_non_bearer_auth_scheme_rejected(self):
        """RED: Only Bearer tokens should be accepted"""
        from app.middleware.auth import get_user_tier

        mock_request = Mock()
        mock_request.headers = {"Authorization": "Basic dXNlcjpwYXNz"}

        tier = get_user_tier(mock_request)
        assert tier == "free"  # Reject and fall back to free


class TestFastAPIIntegration:
    """Test FastAPI dependency injection - integration patterns"""

    @pytest.mark.asyncio
    async def test_get_current_user_dependency(self):
        """RED: Dependency should extract user from valid token"""
        from app.middleware.auth import get_current_user
        from fastapi import Depends

        # Create valid token
        payload = {
            "sub": "integration_user",
            "tier": "premium",
            "exp": datetime.now(timezone.utc) + timedelta(hours=2)
        }
        token = jwt.encode(payload, TEST_SECRET_KEY, algorithm=TEST_ALGORITHM)

        # Mock request with token
        mock_request = Mock()
        mock_request.headers = {"Authorization": f"Bearer {token}"}

        # Should extract user data
        user_data = await get_current_user(mock_request)
        assert user_data["sub"] == "integration_user"
        assert user_data["tier"] == "premium"

    @pytest.mark.asyncio
    async def test_optional_authentication_dependency(self):
        """RED: Optional dependency should allow anonymous access"""
        from app.middleware.auth import get_current_user_optional

        # Mock request without token
        mock_request = Mock()
        mock_request.headers = {}

        # Should return None for anonymous users
        user_data = await get_current_user_optional(mock_request)
        assert user_data is None


class TestBruteForceProtection:
    """Test protection against brute force attacks"""

    def test_failed_validation_attempts_are_logged(self):
        """RED: Failed token validations should be logged for monitoring"""
        from app.middleware.auth import validate_token

        invalid_token = "invalid.token.here"

        with patch('app.middleware.auth.logger') as mock_logger:
            with pytest.raises(HTTPException):
                validate_token(invalid_token)

            # Should log the failed attempt
            assert mock_logger.warning.called or mock_logger.error.called

    def test_rate_limiting_on_token_validation(self):
        """RED: Token validation endpoint should have rate limiting"""
        # This will be tested in integration tests with actual rate limiter
        pass  # Placeholder


# Acceptance Criteria Validation Tests
class TestAcceptanceCriteria:
    """Verify all acceptance criteria from mission brief"""

    def test_acceptance_anonymous_users_5_conn_min(self):
        """✅ Anonymous users: 5 conn/min"""
        from app.middleware.auth import get_tier_limits

        limits = get_tier_limits("free")
        assert limits["rate_limit"] == "5/minute"

    def test_acceptance_anonymous_users_5_dollar_budget(self):
        """✅ Anonymous users: $5 session budget"""
        from app.middleware.auth import get_tier_limits

        limits = get_tier_limits("free")
        assert limits["session_budget"] == 5.0

    def test_acceptance_premium_users_20_conn_min(self):
        """✅ Premium users (JWT): 20 conn/min"""
        from app.middleware.auth import get_tier_limits

        limits = get_tier_limits("premium")
        assert limits["rate_limit"] == "20/minute"

    def test_acceptance_premium_users_20_dollar_budget(self):
        """✅ Premium users (JWT): $20 session budget"""
        from app.middleware.auth import get_tier_limits

        limits = get_tier_limits("premium")
        assert limits["session_budget"] == 20.0

    def test_acceptance_enterprise_users_100_conn_min(self):
        """✅ Enterprise users: 100 conn/min"""
        from app.middleware.auth import get_tier_limits

        limits = get_tier_limits("enterprise")
        assert limits["rate_limit"] == "100/minute"

    def test_acceptance_enterprise_users_100_dollar_budget(self):
        """✅ Enterprise users: $100 session budget"""
        from app.middleware.auth import get_tier_limits

        limits = get_tier_limits("enterprise")
        assert limits["session_budget"] == 100.0

    def test_acceptance_expired_tokens_rejected_401(self):
        """✅ Expired tokens rejected with 401"""
        from app.middleware.auth import validate_token

        expired_payload = {
            "sub": "test",
            "exp": datetime.now(timezone.utc) - timedelta(hours=1)
        }
        expired_token = jwt.encode(expired_payload, TEST_SECRET_KEY, algorithm=TEST_ALGORITHM)

        with pytest.raises(HTTPException) as exc_info:
            validate_token(expired_token)

        assert exc_info.value.status_code == 401

    def test_acceptance_invalid_signatures_rejected(self):
        """✅ Invalid signatures rejected"""
        from app.middleware.auth import validate_token

        tampered_payload = {
            "sub": "attacker",
            "tier": "enterprise",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1)
        }
        tampered_token = jwt.encode(tampered_payload, "wrong-key", algorithm=TEST_ALGORITHM)

        with pytest.raises(HTTPException):
            validate_token(tampered_token)

    def test_acceptance_token_refresh_works(self):
        """✅ Token refresh works correctly"""
        from app.middleware.auth import refresh_access_token

        refresh_payload = {
            "sub": "user123",
            "tier": "premium",
            "token_type": "refresh",
            "exp": datetime.now(timezone.utc) + timedelta(days=7)
        }
        refresh_token = jwt.encode(refresh_payload, TEST_SECRET_KEY, algorithm=TEST_ALGORITHM)

        new_token = refresh_access_token(refresh_token)
        assert new_token is not None
        assert new_token != refresh_token
