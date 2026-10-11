"""Trust-boundary and memory-isolation tests for proxy identities."""

from pathlib import Path

import pytest
from starlette.datastructures import Headers

from app.memory import FactCategory, UserMemoryStore
from app.middleware.proxy_identity import (
    get_trusted_proxy_identity,
    parse_trusted_proxy_sources,
    resolve_user_identity,
)


@pytest.mark.parametrize(
    "dockerfile",
    [
        Path(__file__).parents[2] / "dockerfile",
        Path(__file__).parents[2] / "docs/security/dockerfile.hardened",
    ],
    ids=["default-image", "hardened-image"],
)
def test_dockerfiles_preserve_the_actual_socket_peer_for_proxy_trust(dockerfile):
    command = dockerfile.read_text()

    assert "--no-proxy-headers" in command
    assert '"--proxy-headers"' not in command
    assert '"--forwarded-allow-ips"' not in command


def test_proxy_identity_requires_the_socket_peer_to_match_a_configured_network():
    networks = parse_trusted_proxy_sources("10.20.0.0/16, 2001:db8::/32")
    spoofed_headers = {
        "x-forwarded-user": "victim",
        "x-forwarded-email": "victim@example.com",
        "x-forwarded-for": "10.20.1.5",
    }

    assert get_trusted_proxy_identity(spoofed_headers, "198.51.100.7", networks) == (
        None,
        None,
    )
    assert get_trusted_proxy_identity(spoofed_headers, "10.20.8.4", networks) == (
        "victim",
        "victim@example.com",
    )
    assert get_trusted_proxy_identity(spoofed_headers, "not-an-ip", networks) == (
        None,
        None,
    )
    assert get_trusted_proxy_identity(spoofed_headers, "10.21.0.1", ()) == (None, None)


def test_invalid_proxy_source_configuration_is_rejected():
    with pytest.raises(ValueError):
        parse_trusted_proxy_sources("10.0.0.0/8,not-a-network")


def test_ambiguous_identity_header_is_ignored():
    headers = Headers(
        raw=[
            (b"x-forwarded-user", b"first"),
            (b"x-forwarded-user", b"second"),
            (b"x-forwarded-email", b"person@example.com"),
        ]
    )

    assert get_trusted_proxy_identity(
        headers,
        "10.20.1.2",
        parse_trusted_proxy_sources("10.20.0.0/16"),
    ) == (None, "person@example.com")


def test_jwt_oauth_identity_precedes_trusted_proxy_identity():
    jwt_user = {
        "sub": "oauth-subject",
        "email": "oauth@example.com",
        "name": "OAuth User",
        "auth_method": "google_oauth",
    }

    assert resolve_user_identity(jwt_user, "proxy-user", "proxy@example.com") == (
        "oauth@example.com",
        "OAuth User",
    )


def test_proxy_email_precedes_proxy_user_and_names_are_not_trusted():
    headers = {
        "x-forwarded-user": "proxy-user",
        "x-forwarded-email": "proxy@example.com",
        "x-forwarded-name": "Client Chosen Name",
    }
    proxy_user, proxy_email = get_trusted_proxy_identity(
        headers,
        "10.20.1.2",
        parse_trusted_proxy_sources("10.20.0.0/16"),
    )

    assert resolve_user_identity(None, proxy_user, proxy_email) == (
        "proxy@example.com",
        "proxy@example.com",
    )


def test_users_selected_by_trusted_proxy_get_isolated_memory(tmp_path):
    networks = parse_trusted_proxy_sources("10.20.0.0/16")
    user_facts = {
        "alice@example.com": "Alice prefers tea",
        "bob@example.com": "Bob prefers coffee",
    }

    for email, fact in user_facts.items():
        headers = {"x-forwarded-email": email}
        proxy_user, proxy_email = get_trusted_proxy_identity(
            headers,
            "10.20.1.2",
            networks,
        )
        identity, _ = resolve_user_identity(None, proxy_user, proxy_email)
        assert identity == email
        store = UserMemoryStore(identity, memory_dir=str(tmp_path))
        store.load()
        store.add_fact(fact, category=FactCategory.PREFERENCE)

    for email, own_fact in user_facts.items():
        store = UserMemoryStore(email, memory_dir=str(tmp_path))
        store.load()
        facts = store.get_facts()
        assert own_fact in facts
        assert len(facts) == 1
        assert next(iter(set(user_facts.values()) - {own_fact})) not in facts
