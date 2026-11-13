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


def test_authentik_identity_from_trusted_peer():
    headers = {
        "X-authentik-username": "alice",
        "X-authentik-email": "alice@example.com",
    }
    networks = parse_trusted_proxy_sources("10.20.0.0/16")

    assert get_trusted_proxy_identity(headers, "10.20.1.2", networks) == (
        "alice",
        "alice@example.com",
    )
    assert get_trusted_proxy_identity(headers, "198.51.100.7", networks) == (
        None,
        None,
    )


def test_matching_authentik_and_forwarded_identity_is_accepted():
    headers = {
        "x-authentik-username": "alice",
        "x-authentik-email": "alice@example.com",
        "x-forwarded-user": "alice",
        "x-forwarded-email": "alice@example.com",
    }

    assert get_trusted_proxy_identity(
        headers, "10.20.1.2", parse_trusted_proxy_sources("10.20.0.0/16")
    ) == ("alice", "alice@example.com")


@pytest.mark.parametrize(
    "conflicting_field",
    ["x-forwarded-email", "x-forwarded-user"],
)
def test_conflicting_authentik_and_forwarded_identity_fails_closed(conflicting_field):
    headers = {
        "x-authentik-username": "alice",
        "x-authentik-email": "alice@example.com",
        "x-forwarded-user": "alice",
        "x-forwarded-email": "alice@example.com",
    }
    headers[conflicting_field] = "someone-else"

    assert get_trusted_proxy_identity(
        headers, "10.20.1.2", parse_trusted_proxy_sources("10.20.0.0/16")
    ) == (None, None)


def test_duplicate_authentik_email_is_ignored():
    headers = Headers(
        raw=[
            (b"x-authentik-username", b"alice"),
            (b"x-authentik-email", b"alice@example.com"),
            (b"x-authentik-email", b"someone-else@example.com"),
        ]
    )

    assert get_trusted_proxy_identity(
        headers, "10.20.1.2", parse_trusted_proxy_sources("10.20.0.0/16")
    ) == ("alice", None)


@pytest.mark.parametrize("raw_email", ["first,second", "first\x00second"])
def test_malformed_authentik_email_is_ignored(raw_email):
    headers = {"x-authentik-username": "alice", "x-authentik-email": raw_email}

    assert get_trusted_proxy_identity(
        headers, "10.20.1.2", parse_trusted_proxy_sources("10.20.0.0/16")
    ) == ("alice", None)


def test_forwarded_only_identity_from_trusted_peer_is_unchanged():
    headers = {
        "x-forwarded-user": "legacy-user",
        "x-forwarded-email": "legacy@example.com",
    }

    assert get_trusted_proxy_identity(
        headers, "10.20.1.2", parse_trusted_proxy_sources("10.20.0.0/16")
    ) == ("legacy-user", "legacy@example.com")


@pytest.mark.parametrize(
    "peer_host",
    [None, 167772161, "10.20.1.2,198.51.100.7", "10.20.1.2:443"],
    ids=["missing", "non-string", "forwarded-list", "host-and-port"],
)
def test_malformed_socket_peer_cannot_authorize_proxy_identity(peer_host):
    headers = {"x-forwarded-email": "victim@example.com"}

    assert get_trusted_proxy_identity(
        headers,
        peer_host,
        parse_trusted_proxy_sources("10.20.0.0/16"),
    ) == (None, None)


def test_invalid_proxy_source_configuration_is_rejected():
    with pytest.raises(ValueError):
        parse_trusted_proxy_sources("10.0.0.0/8,not-a-network")

    with pytest.raises(ValueError):
        parse_trusted_proxy_sources("10.0.0.1,")


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


@pytest.mark.parametrize(
    "raw_value",
    ["first,second", "first\x00second"],
    ids=["comma-separated", "control-character"],
)
def test_malformed_identity_header_value_is_ignored(raw_value):
    headers = {"X-Forwarded-Email": raw_value}

    assert get_trusted_proxy_identity(
        headers,
        "10.20.1.2",
        parse_trusted_proxy_sources("10.20.0.0/16"),
    ) == (None, None)


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


def test_jwt_without_email_resolves_to_its_subject():
    # OAuth-issued JWTs always carry an email claim; locally minted tokens
    # may not. Either way resolution must land on the signed subject.
    jwt_user = {"sub": "stable-subject-123", "name": "Token User"}

    assert resolve_user_identity(jwt_user, None, None) == (
        "stable-subject-123",
        "Token User",
    )


@pytest.mark.parametrize(
    "unusable_email",
    [None, "", "   ", "\t", 12345, True, {"spoofed": True}, ["a@b.example"]],
    ids=[
        "missing",
        "empty",
        "whitespace",
        "tab",
        "numeric",
        "boolean",
        "object",
        "list",
    ],
)
def test_unusable_jwt_email_claims_fall_through_to_the_subject(unusable_email):
    jwt_user = {"sub": "subject-fallback", "email": unusable_email}

    assert resolve_user_identity(jwt_user, "proxy-user", "proxy@example.com") == (
        "subject-fallback",
        "subject-fallback",
    )


def test_jwt_with_no_usable_identity_claims_falls_back_to_trusted_proxy():
    # A signed token that names nobody cannot claim the proxy identity's
    # slot, but resolution must still be deterministic: the trusted proxy
    # identity is used, proxy email before proxy user.
    jwt_user = {"email": "", "sub": "   ", "name": None}

    assert resolve_user_identity(jwt_user, "proxy-user", "proxy@example.com") == (
        "proxy@example.com",
        "proxy@example.com",
    )
    assert resolve_user_identity(jwt_user, "proxy-user", None) == (
        "proxy-user",
        "proxy-user",
    )
    assert resolve_user_identity(jwt_user, None, None) == (None, None)


def test_subject_only_jwt_beats_conflicting_trusted_proxy_identity():
    jwt_user = {"sub": "jwt-subject-wins"}

    assert resolve_user_identity(jwt_user, "proxy-user", "proxy@example.com") == (
        "jwt-subject-wins",
        "jwt-subject-wins",
    )


def test_jwt_display_name_must_be_a_usable_string():
    jwt_user = {"sub": "subject-1", "email": "user@example.com", "name": {"x": 1}}

    assert resolve_user_identity(jwt_user, None, None) == (
        "user@example.com",
        "user@example.com",
    )


def test_untrusted_peer_headers_never_reach_the_resolver():
    # A client that is not a configured proxy cannot gain an identity by
    # sending forwarded headers: nothing reaches the resolver, so there is
    # no untrusted-header fallback and the connection resolves anonymous.
    spoofed_headers = {
        "x-forwarded-user": "attacker-chosen",
        "x-forwarded-email": "attacker@example.com",
    }

    proxy_user, proxy_email = get_trusted_proxy_identity(
        spoofed_headers,
        "198.51.100.7",
        parse_trusted_proxy_sources("10.20.0.0/16"),
    )
    assert (proxy_user, proxy_email) == (None, None)
    assert resolve_user_identity(None, proxy_user, proxy_email) == (None, None)


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
