"""
Tests asserting Redis initialization failures never log REDIS_URL content.

redis-py's URL parser raises exceptions whose text echoes parts of the URL
it was handed (e.g. ``Port could not be cast to integer value as '<fragment>'``,
where the fragment can be a slice of the credential). The middleware init
handlers therefore log only the exception TYPE, never str(e). These tests
fail if any part of a malformed REDIS_URL reaches a log record.
"""
import importlib
import logging

import pytest

# Distinctive marker placed in BOTH the password and the port position of a
# malformed URL. redis-py raises
# ``ValueError("Port could not be cast to integer value as '<marker>'")`` —
# the production failure shape (the echoed fragment was part of the stored
# REDIS_URL). If any handler ever logs str(e), the marker shows up.
MARKER = "duck-e-log-sanitization-marker"
MALFORMED_URL = (
    f"redis://:{MARKER}@valkey-service.valkey.svc.cluster.local:{MARKER}"
)


def _reload_auth():
    """Re-execute app.middleware.auth so its import-time init block runs."""
    import app.middleware.auth as auth_module

    return importlib.reload(auth_module)


@pytest.fixture
def malformed_redis_url(monkeypatch):
    """Set a malformed REDIS_URL carrying the marker, then restore state."""
    monkeypatch.setenv("REDIS_URL", MALFORMED_URL)
    yield
    # Leave the module in a clean state for later tests: no REDIS_URL set.
    monkeypatch.delenv("REDIS_URL", raising=False)
    _reload_auth()


class TestAuthRedisInitLogSanitization:
    def test_init_failure_logs_no_url_content(
        self, malformed_redis_url, caplog
    ):
        """The revocation init handler must log the type, never the URL."""
        with caplog.at_level(logging.WARNING, logger="app.middleware.auth"):
            module = _reload_auth()

        assert module._redis_client is None  # init failed as intended
        assert "Redis initialization failed, revocation disabled" in caplog.text
        assert "ValueError" in caplog.text  # exception type is logged
        assert MARKER not in caplog.text  # neither the password...
        assert "valkey-service" not in caplog.text  # ...nor any URL content


class TestCostProtectionRedisInitLogSanitization:
    def test_init_failure_logs_no_url_content(
        self, malformed_redis_url, monkeypatch, caplog
    ):
        """The cost-tracker init handler must log the type, never the URL."""
        import app.middleware.cost_protection as cost_protection

        # Force get_cost_tracker() to build a fresh tracker so the init
        # block runs against the malformed URL.
        monkeypatch.setattr(cost_protection, "_cost_tracker", None)

        with caplog.at_level(
            logging.ERROR, logger="app.middleware.cost_protection"
        ):
            tracker = cost_protection.get_cost_tracker()

        assert tracker.redis_client is None  # init failed as intended
        assert (
            "Failed to initialize Redis for cost tracking" in caplog.text
        )
        assert "ValueError" in caplog.text  # exception type is logged
        assert MARKER not in caplog.text  # neither the password...
        assert "valkey-service" not in caplog.text  # ...nor any URL content
