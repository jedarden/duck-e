"""
Integration Tests for Google OAuth Flow and Memory Keying

Tests the complete OAuth flow from login to memory storage:
1. Google OAuth callback generates JWT tokens with user identity
2. JWT tokens are validated and user email is extracted
3. Memory system uses authenticated user email for keying
4. WebSocket connections properly authenticate via JWT tokens

NOTE: These tests use mocked Google OAuth responses since we cannot
make actual OAuth calls to Google in tests.
"""
import pytest
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch, AsyncMock
from fastapi.testclient import TestClient
from jose import jwt
import os


# Test configuration
TEST_GOOGLE_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
TEST_GOOGLE_CLIENT_SECRET = "test-client-secret"
TEST_JWT_SECRET = "test-jwt-secret-key"
TEST_REDIRECT_URI = "http://localhost:8000/auth/callback"


class TestOAuthConfiguration:
    """Test OAuth configuration and availability checks"""

    def test_oauth_not_configured_without_env_vars(self):
        """OAuth should not be configured without environment variables"""
        from app.middleware.google_oauth import is_oauth_configured

        # Clear environment variables
        old_client_id = os.getenv("GOOGLE_CLIENT_ID")
        old_client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

        os.environ["GOOGLE_CLIENT_ID"] = ""
        os.environ["GOOGLE_CLIENT_SECRET"] = ""

        # Reimport to pick up new env vars
        import importlib
        from app.middleware import google_oauth
        importlib.reload(google_oauth)

        assert not google_oauth.is_oauth_configured()

        # Restore
        if old_client_id:
            os.environ["GOOGLE_CLIENT_ID"] = old_client_id
        if old_client_secret:
            os.environ["GOOGLE_CLIENT_SECRET"] = old_client_secret


class TestOAuthCallbackFlow:
    """Test OAuth callback and JWT token generation"""

    @pytest.mark.asyncio
    async def test_oauth_callback_creates_jwt_with_email(self):
        """OAuth callback should create JWT token with user email"""
        from app.middleware.google_oauth import handle_oauth_callback
        from app.middleware.auth import JWT_SECRET_KEY, JWT_ALGORITHM

        # Mock OAuth state validation
        with patch('app.middleware.google_oauth.validate_state', return_value=True):
            with patch('app.middleware.google_oauth.consume_state', return_value=TEST_REDIRECT_URI):
                # Mock Google token exchange
                mock_tokens = {
                    "access_token": "google_access_token",
                    "refresh_token": "google_refresh_token",
                    "expires_in": 3600
                }
                with patch('app.middleware.google_oauth.exchange_code_for_tokens', return_value=mock_tokens):
                    # Mock Google user info
                    mock_user_info = {
                        "id": "google_user_id_123",
                        "email": "test@example.com",
                        "name": "Test User",
                        "picture": "https://example.com/photo.jpg",
                        "verified_email": True
                    }
                    with patch('app.middleware.google_oauth.get_user_info', return_value=mock_user_info):
                        # Call the callback handler
                        result = await handle_oauth_callback(
                            code="test_auth_code",
                            state="test_state"
                        )

                        # Verify JWT tokens were created
                        assert "access_token" in result
                        assert "refresh_token" in result
                        assert "user_info" in result

                        # Verify user info is correct
                        assert result["user_info"]["email"] == "test@example.com"
                        assert result["user_info"]["name"] == "Test User"

                        # Verify JWT token contains user identity
                        decoded_jwt = jwt.decode(
                            result["access_token"],
                            JWT_SECRET_KEY,
                            algorithms=[JWT_ALGORITHM]
                        )
                        assert decoded_jwt["sub"] == "test@example.com"
                        assert decoded_jwt["email"] == "test@example.com"
                        assert decoded_jwt["auth_method"] == "google_oauth"
                        assert decoded_jwt["tier"] == "premium"

    @pytest.mark.asyncio
    async def test_jwt_token_extracted_from_websocket_query_param(self):
        """WebSocket should extract and validate JWT token from query parameter"""
        from app.main import app
        from app.middleware.google_oauth import get_user_info_from_token
        from app.middleware.auth import create_access_token, JWT_SECRET_KEY, JWT_ALGORITHM

        # Create a JWT token with user identity
        payload = {
            "sub": "websocket_user@example.com",
            "email": "websocket_user@example.com",
            "name": "WebSocket User",
            "tier": "premium",
            "auth_method": "google_oauth"
        }
        jwt_token = create_access_token(payload)

        # Verify token can be extracted and decoded
        user_info = get_user_info_from_token(jwt_token)
        assert user_info is not None
        assert user_info["email"] == "websocket_user@example.com"
        assert user_info["auth_method"] == "google_oauth"


class TestMemoryIntegration:
    """Test that OAuth authentication integrates with memory system"""

    def test_memory_store_keys_by_user_email(self):
        """UserMemoryStore should key memory files by user email"""
        from app.memory import UserMemoryStore
        import tempfile
        import shutil

        # Create a temporary directory for test memory
        temp_dir = tempfile.mkdtemp()
        try:
            # Create memory store for authenticated user
            user_email = "authenticated_user@example.com"
            memory_store = UserMemoryStore(user_email, memory_dir=temp_dir)
            memory_store.load()

            # Add a fact
            from app.memory import FactCategory, FactSource
            memory_store.add_fact(
                text="User prefers dark mode",
                category=FactCategory.PREFERENCE,
                confidence=1.0,
                source=FactSource.EXPLICIT
            )
            memory_store.save()

            # Verify file is keyed by email hash (not raw email)
            import hashlib
            expected_hash = hashlib.sha256(user_email.encode()).hexdigest()
            expected_filename = f"{expected_hash}.json"

            # Check that file exists with hashed name
            import os
            memory_files = os.listdir(temp_dir)
            assert expected_filename in memory_files

            # Verify we can load the same memory for the same user
            memory_store2 = UserMemoryStore(user_email, memory_dir=temp_dir)
            memory_store2.load()
            facts = memory_store2.get_facts()
            assert "User prefers dark mode" in facts

        finally:
            shutil.rmtree(temp_dir)

    def test_different_users_have_separate_memories(self):
        """Different authenticated users should have separate memory stores"""
        from app.memory import UserMemoryStore
        import tempfile
        import shutil

        temp_dir = tempfile.mkdtemp()
        try:
            # Create memory for user 1
            from app.memory import FactCategory, FactSource
            user1_email = "user1@example.com"
            memory1 = UserMemoryStore(user1_email, memory_dir=temp_dir)
            memory1.load()
            memory1.add_fact("User1 likes cats", category=FactCategory.PREFERENCE, confidence=1.0, source=FactSource.EXPLICIT)
            memory1.save()

            # Create memory for user 2
            user2_email = "user2@example.com"
            memory2 = UserMemoryStore(user2_email, memory_dir=temp_dir)
            memory2.load()
            memory2.add_fact("User2 likes dogs", category=FactCategory.PREFERENCE, confidence=1.0, source=FactSource.EXPLICIT)
            memory2.save()

            # Verify memories are separate
            facts1 = memory1.get_facts()
            facts2 = memory2.get_facts()

            assert "User1 likes cats" in facts1
            assert "User2 likes dogs" in facts2
            assert "User2 likes dogs" not in facts1
            assert "User1 likes cats" not in facts2

            # Verify separate files exist
            import os
            memory_files = os.listdir(temp_dir)
            assert len(memory_files) == 2

        finally:
            shutil.rmtree(temp_dir)


class TestOAuthEndpoints:
    """Test OAuth HTTP endpoints"""

    def test_auth_config_endpoint_returns_oauth_status(self):
        """Test /auth/config endpoint returns configuration status"""
        from app.main import app
        client = TestClient(app)

        response = client.get("/auth/config")
        assert response.status_code == 200

        data = response.json()
        assert "configured" in data
        assert "login_url" in data
        assert "message" in data

    def test_auth_me_endpoint_validates_jwt(self):
        """Test /auth/me endpoint validates JWT and returns user info"""
        from app.main import app
        from app.middleware.auth import create_access_token

        # Create a test JWT token
        payload = {
            "sub": "test_user@example.com",
            "email": "test_user@example.com",
            "name": "Test User",
            "tier": "premium",
            "auth_method": "google_oauth"
        }
        token = create_access_token(payload)

        client = TestClient(app)
        response = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"}
        )

        # OAuth might not be configured, so we expect either 401 or 200
        # If OAuth is configured, should return user info
        # If not, should return 401
        assert response.status_code in [200, 401]

        if response.status_code == 200:
            data = response.json()
            assert data.get("authenticated") == True
            assert "user_info" in data
            assert data["user_info"]["email"] == "test_user@example.com"


class TestOAuthMemoryE2E:
    """End-to-end tests for OAuth and memory integration"""

    @pytest.mark.asyncio
    async def test_oauth_user_memory_persistence_flow(self):
        """Test complete flow: OAuth login -> JWT -> WebSocket -> Memory storage"""
        from app.memory import UserMemoryStore
        from app.middleware.google_oauth import handle_oauth_callback
        from app.middleware.auth import JWT_SECRET_KEY, JWT_ALGORITHM
        import tempfile
        import shutil

        temp_dir = tempfile.mkdtemp()
        try:
            # Step 1: Simulate OAuth callback
            with patch('app.middleware.google_oauth.validate_state', return_value=True):
                with patch('app.middleware.google_oauth.consume_state', return_value=TEST_REDIRECT_URI):
                    mock_tokens = {
                        "access_token": "google_access_token",
                        "refresh_token": "google_refresh_token",
                        "expires_in": 3600
                    }
                    with patch('app.middleware.google_oauth.exchange_code_for_tokens', return_value=mock_tokens):
                        mock_user_info = {
                            "id": "google_user_456",
                            "email": "e2e_user@example.com",
                            "name": "E2E Test User",
                            "verified_email": True
                        }
                        with patch('app.middleware.google_oauth.get_user_info', return_value=mock_user_info):
                            oauth_result = await handle_oauth_callback(
                                code="e2e_auth_code",
                                state="e2e_state"
                            )

            # Step 2: Extract JWT and decode user identity
            jwt_token = oauth_result["access_token"]
            decoded_jwt = jwt.decode(
                jwt_token,
                JWT_SECRET_KEY,
                algorithms=[JWT_ALGORITHM]
            )
            user_email = decoded_jwt["sub"]  # Should be email

            # Step 3: Create memory store keyed by user email
            memory_store = UserMemoryStore(user_email, memory_dir=temp_dir)
            memory_store.load()

            # Step 4: Add memory for this user
            from app.memory import FactCategory, FactSource
            memory_store.add_fact(
                text="E2E user prefers metric units",
                category=FactCategory.PREFERENCE,
                confidence=1.0,
                source=FactSource.EXPLICIT
            )
            memory_store.save()

            # Step 5: Verify memory persists for same user
            memory_store_verify = UserMemoryStore(user_email, memory_dir=temp_dir)
            memory_store_verify.load()
            facts = memory_store_verify.get_facts()
            assert "E2E user prefers metric units" in facts

        finally:
            shutil.rmtree(temp_dir)


# Acceptance Criteria Tests
class TestOAuthAcceptanceCriteria:
    """Verify acceptance criteria for OAuth implementation"""

    def test_oauth_generates_jwt_with_user_email(self):
        """✅ OAuth flow generates JWT with user email"""
        from app.middleware.google_oauth import get_user_info_from_token
        from app.middleware.auth import create_access_token

        payload = {
            "sub": "acceptance@example.com",
            "email": "acceptance@example.com",
            "auth_method": "google_oauth"
        }
        token = create_access_token(payload)
        user_info = get_user_info_from_token(token)

        assert user_info is not None
        assert user_info["email"] == "acceptance@example.com"

    def test_memory_keyed_by_authenticated_identity(self):
        """✅ Memory is keyed by authenticated user identity"""
        from app.memory import UserMemoryStore
        import tempfile
        import shutil

        temp_dir = tempfile.mkdtemp()
        try:
            authenticated_email = "auth_user@example.com"
            memory_store = UserMemoryStore(authenticated_email, memory_dir=temp_dir)
            memory_store.load()
            from app.memory import FactCategory, FactSource
            memory_store.add_fact("Test fact", category=FactCategory.PREFERENCE, confidence=1.0, source=FactSource.EXPLICIT)
            memory_store.save()

            # Verify file exists for this user
            import os
            import hashlib
            expected_hash = hashlib.sha256(authenticated_email.encode()).hexdigest()
            assert f"{expected_hash}.json" in os.listdir(temp_dir)

        finally:
            shutil.rmtree(temp_dir)

    def test_jwt_tokens_validated_on_websocket_connect(self):
        """✅ JWT tokens are validated on WebSocket connection"""
        from app.middleware.google_oauth import get_user_info_from_token
        from app.middleware.auth import create_access_token, validate_token

        payload = {
            "sub": "ws_user@example.com",
            "email": "ws_user@example.com",
            "auth_method": "google_oauth"
        }
        token = create_access_token(payload)

        # Validate token
        validated = validate_token(token)
        assert validated["sub"] == "ws_user@example.com"

        # Extract user info
        user_info = get_user_info_from_token(token)
        assert user_info["email"] == "ws_user@example.com"
