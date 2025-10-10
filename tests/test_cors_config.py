"""
Unit tests for CORS configuration.
"""
import os
import pytest
from app.middleware import CORSConfig, get_cors_config


class TestCORSConfig:
    """Test CORS configuration class."""

    def test_default_development_origins(self, monkeypatch):
        """Test default origins in development environment."""
        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "development")

        config = CORSConfig()

        assert "http://localhost:3000" in config.allowed_origins
        assert "http://localhost:8000" in config.allowed_origins

    def test_production_requires_explicit_origins(self, monkeypatch):
        """Test production environment requires explicit origin configuration."""
        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "production")

        config = CORSConfig()

        # Production should have no default origins (strict)
        assert config.allowed_origins == []

    def test_environment_variable_origins(self, monkeypatch):
        """Test origins from environment variable."""
        monkeypatch.setenv("ALLOWED_ORIGINS", "https://example.com,https://app.example.com")

        config = CORSConfig()

        assert "https://example.com" in config.allowed_origins
        assert "https://app.example.com" in config.allowed_origins
        assert len(config.allowed_origins) == 2

    def test_wildcard_origin(self):
        """Test wildcard origin configuration."""
        config = CORSConfig(allowed_origins="*")

        assert config.allowed_origins == ["*"]

    def test_direct_list_origins(self):
        """Test direct list of origins."""
        origins = ["https://example.com", "https://app.example.com"]
        config = CORSConfig(allowed_origins=origins)

        assert config.allowed_origins == origins

    def test_origin_allowed_exact_match(self):
        """Test exact origin matching."""
        config = CORSConfig(allowed_origins=["https://example.com"])

        assert config.is_origin_allowed("https://example.com") is True
        assert config.is_origin_allowed("https://other.com") is False

    def test_origin_allowed_wildcard(self):
        """Test wildcard origin matching."""
        config = CORSConfig(allowed_origins=["*"])

        assert config.is_origin_allowed("https://example.com") is True
        assert config.is_origin_allowed("https://any.com") is True

    def test_origin_allowed_subdomain_wildcard(self):
        """Test subdomain wildcard pattern."""
        config = CORSConfig(allowed_origins=["https://*.example.com"])

        assert config.is_origin_allowed("https://app.example.com") is True
        assert config.is_origin_allowed("https://api.example.com") is True
        assert config.is_origin_allowed("https://example.com") is False

    def test_middleware_kwargs(self):
        """Test middleware kwargs generation."""
        config = CORSConfig(
            allowed_origins=["https://example.com"],
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            max_age=600
        )

        kwargs = config.get_middleware_kwargs()

        assert kwargs["allow_origins"] == ["https://example.com"]
        assert kwargs["allow_credentials"] is True
        assert kwargs["allow_methods"] == ["GET", "POST"]
        assert kwargs["max_age"] == 600


class TestGetCorsConfig:
    """Test get_cors_config factory function."""

    def test_get_cors_config_from_env(self, monkeypatch):
        """Test CORS config creation from environment variables."""
        monkeypatch.setenv("ALLOWED_ORIGINS", "https://example.com,https://app.example.com")
        monkeypatch.setenv("ALLOW_CREDENTIALS", "true")
        monkeypatch.setenv("CORS_MAX_AGE", "1200")

        config = get_cors_config()

        assert "https://example.com" in config.allowed_origins
        assert "https://app.example.com" in config.allowed_origins
        assert config.allow_credentials is True
        assert config.max_age == 1200

    def test_get_cors_config_credentials_false(self, monkeypatch):
        """Test CORS config with credentials disabled."""
        monkeypatch.setenv("ALLOW_CREDENTIALS", "false")

        config = get_cors_config()

        assert config.allow_credentials is False
