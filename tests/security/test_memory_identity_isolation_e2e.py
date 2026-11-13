"""End-to-end memory-isolation regression tests for the /session request path.

The resolution helpers are unit-tested elsewhere; these tests drive the real
WebSocket handler — origin validation, socket-peer proxy trust, JWT
validation, identity resolution, per-user memory wiring, and the
save_memory / recall_memories tools — so a regression in how the handler
wires a resolved identity to per-user memory cannot pass silently.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("OPENAI_API_KEY", "test-key-memory-isolation-e2e")  # pragma: allowlist secret

import pytest
from fastapi.testclient import TestClient

from app.memory import FactCategory, UserMemoryStore
from app.middleware.proxy_identity import parse_trusted_proxy_sources
from app.realtime_session import RealtimeSession

ORIGIN = "http://localhost:8000"
E2E_JWT_SECRET = "memory-isolation-e2e-jwt-signing-secret"  # pragma: allowlist secret
TRUSTED_PEER = "10.20.1.2"       # inside the configured proxy network
UNTRUSTED_PEER = "198.51.100.7"  # outside it, presenting spoofed headers

TOOL_CALL_TYPE = "response.function_call_arguments.done"


class _SocketPeerOverride:
    """ASGI wrapper pinning the socket peer so tests control proxy trust."""

    def __init__(self, app, peer_host: str):
        self._app = app
        self._peer_host = peer_host

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            scope["client"] = (self._peer_host, 51000)
        await self._app(scope, receive, send)


def _memory_file_name(user_id: str) -> str:
    user_hash = hashlib.sha256(user_id.encode()).hexdigest()
    return f"{user_hash}.json"


def _seed_memory(memory_dir, user_id: str, fact: str) -> None:
    store = UserMemoryStore(user_id, memory_dir=str(memory_dir))
    store.load()
    assert store.add_fact(fact, category=FactCategory.PERSONAL, confidence=1.0)


def _reload_store(memory_dir, user_id: str) -> UserMemoryStore:
    store = UserMemoryStore(user_id, memory_dir=str(memory_dir))
    store.load()
    return store


def _stored_fact_texts(memory_dir, user_id: str) -> list[str]:
    return _reload_store(memory_dir, user_id).get_facts()


def _send_tool_call(websocket, name: str, call_id: str, arguments: dict) -> dict:
    """Drive one client-side tool call and return the parsed handler output."""
    websocket.send_json({
        "type": TOOL_CALL_TYPE,
        "name": name,
        "call_id": call_id,
        "arguments": json.dumps(arguments),
    })
    response = websocket.receive_json()
    assert response["type"] == "conversation.item.create"
    assert response["item"]["call_id"] == call_id
    assert response["item"]["type"] == "function_call_output"
    return json.loads(response["item"]["output"])


@pytest.fixture
def memory_dir(tmp_path):
    path = tmp_path / "memory"
    path.mkdir()
    return path


@pytest.fixture
def session_env(monkeypatch, memory_dir):
    """Wire the real handler to an isolated memory directory.

    The OpenAI touchpoints on the session path are replaced with
    deterministic equivalents: the ephemeral-key exchange captures the
    per-session instructions (where remembered facts are injected) and the
    registered tool names, and summary generation is stubbed so the system
    message falls back to listing stored facts.
    """
    import app.main as main
    from app.middleware import auth

    captured_sessions: list[dict] = []

    async def fake_ephemeral_key(self, voice=None):
        captured_sessions.append({
            "instructions": self.system_message,
            "tools": sorted(tool["name"] for tool in self.tools),
        })
        return {"client_secret": {"value": "test-ephemeral-value"}, "model": self.model}

    async def no_summary(self, api_key):
        return ""

    async def distinct_compare(self, text1, text2, api_key):
        # save_memory uses semantic dedup; a borderline-similarity pair would
        # otherwise call the OpenAI API from these tests. Isolation is the
        # behavior under test here, not dedup — pin the comparison so the
        # suite is offline-deterministic.
        return "distinct"

    monkeypatch.setattr(RealtimeSession, "_get_ephemeral_key", fake_ephemeral_key)
    monkeypatch.setattr(UserMemoryStore, "get_or_generate_summary", no_summary)
    monkeypatch.setattr(UserMemoryStore, "semantic_compare", distinct_compare)
    monkeypatch.setattr(
        main,
        "UserMemoryStore",
        lambda user_id: UserMemoryStore(user_id, memory_dir=str(memory_dir)),
    )
    monkeypatch.setattr(auth, "JWT_SECRET_KEY", E2E_JWT_SECRET)
    monkeypatch.setattr(auth, "_redis_client", None)

    # Earlier tests in the suite can leave the app-wide tracker tripped,
    # which would mask identity results with a service_unavailable close.
    cost_tracker = main.cost_tracker
    cost_tracker.circuit_breaker_active = False
    cost_tracker.circuit_breaker_reset_time = None
    cost_tracker.total_cost_last_hour = 0.0

    return SimpleNamespace(sessions=captured_sessions, memory_dir=memory_dir)


@pytest.fixture
def connect_as(session_env):
    from app.main import app

    def _connect(peer_host: str) -> TestClient:
        return TestClient(_SocketPeerOverride(app, peer_host))

    return _connect


def _mint_token(**claims) -> str:
    from app.middleware import auth

    return auth.create_access_token({"sub": "memory-e2e-user", **claims})


def _trust_proxy_network(monkeypatch, sources: str = "10.20.0.0/16") -> None:
    import app.main as main

    monkeypatch.setattr(main, "TRUSTED_PROXY_NETWORKS", parse_trusted_proxy_sources(sources))


_RESTART_PROCESS = r'''
import json
import os
import sys

from fastapi.testclient import TestClient

from app.memory import UserMemoryStore
from app.middleware import auth
from app.realtime_session import RealtimeSession

import app.main as main

FACT = "The user prefers a quiet workspace"
USER_EMAIL = "restart-user@example.com"
PHASE = sys.argv[1]


async def fake_ephemeral_key(self, voice=None):
    if PHASE == "read":
        assert FACT in self.system_message
    return {"client_secret": {"value": "test-ephemeral-value"}, "model": self.model}


async def no_summary(self, api_key):
    return ""


async def distinct_compare(self, text1, text2, api_key):
    return "distinct"


RealtimeSession._get_ephemeral_key = fake_ephemeral_key
UserMemoryStore.get_or_generate_summary = no_summary
UserMemoryStore.semantic_compare = distinct_compare
main.UserMemoryStore = lambda user_id: UserMemoryStore(
    user_id, memory_dir=os.environ["DUCKE_TEST_MEMORY_DIR"]
)
main.cost_tracker.circuit_breaker_active = False
main.cost_tracker.circuit_breaker_reset_time = None
main.cost_tracker.total_cost_last_hour = 0.0
auth.JWT_SECRET_KEY = "memory-restart-e2e-jwt-secret"

token = auth.create_access_token({"sub": USER_EMAIL, "email": USER_EMAIL})
with TestClient(main.app) as client:
    with client.websocket_connect(
        f"/session?token={token}", headers={"Origin": "http://localhost:8000"}
    ) as websocket:
        assert websocket.receive_json()["type"] == "ducke.init"
        if PHASE == "write":
            websocket.send_json({
                "type": "response.function_call_arguments.done",
                "name": "save_memory",
                "call_id": "restart-save-1",
                "arguments": json.dumps({"fact": FACT, "category": "context"}),
            })
            response = websocket.receive_json()
            assert response["type"] == "conversation.item.create"
            assert json.loads(response["item"]["output"])["status"] == "ok"
'''


def test_authenticated_memory_survives_an_application_restart(tmp_path):
    """A durable memory written by one app process is loaded by the next."""
    memory_dir = tmp_path / "configured-memory"
    memory_dir.mkdir()
    repo_root = str(Path(__file__).resolve().parents[2])
    env = os.environ.copy()
    env.update(
        {
            "DUCKE_TEST_MEMORY_DIR": str(memory_dir),
            "JWT_SECRET_KEY": "memory-restart-e2e-jwt-secret",
            "OPENAI_API_KEY": "test-key-memory-restart-e2e",
        }
    )

    for phase in ("write", "read"):
        result = subprocess.run(
            [sys.executable, "-c", _RESTART_PROCESS, phase],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, (
            f"restart phase {phase!r} failed with exit code {result.returncode}:\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )

    assert len(list(memory_dir.glob("*.json"))) == 1


def test_spoofed_headers_from_untrusted_peer_cannot_select_victim_memory(
    session_env, connect_as, monkeypatch
):
    """A direct client naming another user's forwarded identity stays anonymous.

    The proxy network is configured (as a deployment behind a proxy would be),
    but the socket peer is not on it — so the client-supplied forwarded
    headers must not select the victim's memory namespace.
    """
    _trust_proxy_network(monkeypatch)
    victim_fact = "Victim sweetens every coffee with hazelnut syrup"
    _seed_memory(session_env.memory_dir, "victim@example.com", victim_fact)

    client = connect_as(UNTRUSTED_PEER)
    with client.websocket_connect(
        "/session",
        headers={
            "Origin": ORIGIN,
            "X-Forwarded-User": "victim@example.com",
            "X-Forwarded-Email": "victim@example.com",
            "X-authentik-username": "victim",
            "X-authentik-email": "victim@example.com",
            "X-Forwarded-For": TRUSTED_PEER,  # forged hop claim
        },
    ) as websocket:
        assert websocket.receive_json()["type"] == "ducke.init"

    assert len(session_env.sessions) == 1
    session = session_env.sessions[0]

    # Nothing from the victim's namespace reached the model: the session
    # resolved anonymous, so no memory section is injected at all.
    assert victim_fact not in session["instructions"]
    assert "The current user is" not in session["instructions"]

    # An anonymous session gets no memory tools to write or read through.
    assert "save_memory" not in session["tools"]
    assert "recall_memories" not in session["tools"]

    # The victim's stored memory is unchanged, and no namespace was created
    # for the attacker-chosen identity.
    assert [p.name for p in session_env.memory_dir.iterdir()] == [
        _memory_file_name("victim@example.com")
    ]
    assert _stored_fact_texts(session_env.memory_dir, "victim@example.com") == [victim_fact]


def test_conflicting_forwarded_headers_cannot_override_jwt_memory(
    session_env, connect_as, monkeypatch
):
    """Even a trusted proxy forwarding another identity loses to the JWT.

    Reads (system-message injection and recall_memories) and writes
    (save_memory) must all key on the authenticated identity, never on the
    conflicting forwarded one.
    """
    _trust_proxy_network(monkeypatch)
    victim_email = "victim@example.com"
    victim_fact = "Victim sweetens every coffee with hazelnut syrup"
    authed_email = "authed@example.com"
    authed_fact = "Authed user rehearses with a community choir"
    new_fact = "Authed user collects vintage keyboards"
    _seed_memory(session_env.memory_dir, victim_email, victim_fact)
    _seed_memory(session_env.memory_dir, authed_email, authed_fact)

    token = _mint_token(sub="authed-subject", email=authed_email, name="Authed User")

    client = connect_as(TRUSTED_PEER)  # the forwarded headers would be trusted here
    with client.websocket_connect(
        "/session",
        headers={
            "Origin": ORIGIN,
            "Authorization": f"Bearer {token}",
            "X-Forwarded-User": victim_email,
            "X-Forwarded-Email": victim_email,
        },
    ) as websocket:
        assert websocket.receive_json()["type"] == "ducke.init"

        session = session_env.sessions[0]
        assert "save_memory" in session["tools"]
        assert "recall_memories" in session["tools"]

        # Read 1 — session start: the JWT user's memory is injected, the
        # forwarded identity's is not.
        assert authed_fact in session["instructions"]
        assert "Authed User (authed@example.com)" in session["instructions"]
        assert victim_fact not in session["instructions"]
        assert victim_email not in session["instructions"]

        # Read 2 — on-demand recall returns only the JWT user's facts.
        recalled = _send_tool_call(websocket, "recall_memories", "recall-1", {})
        assert recalled == {"facts": [authed_fact], "topic": None}

        # Write — the new fact must land in the JWT identity's namespace.
        saved = _send_tool_call(
            websocket, "save_memory", "save-1", {"fact": new_fact, "category": "context"}
        )
        assert saved["status"] == "ok"

    assert [p.name for p in session_env.memory_dir.iterdir()] == sorted([
        _memory_file_name(victim_email),
        _memory_file_name(authed_email),
    ])

    # The write landed with the authenticated user, not the forwarded one.
    assert _stored_fact_texts(session_env.memory_dir, authed_email) == [
        authed_fact,
        new_fact,
    ]
    assert _stored_fact_texts(session_env.memory_dir, victim_email) == [victim_fact]


def test_two_resolved_identities_remain_isolated_on_the_request_path(
    session_env, connect_as, monkeypatch
):
    """Sessions for two identities on the same path never share memory.

    One identity arrives through the trusted proxy's Authentik headers, the
    other through a JWT from an untrusted client; both read and write their
    own namespace and nothing else.
    """
    _trust_proxy_network(monkeypatch)
    alice_email = "alice@example.com"
    alice_seeded = "Alice commutes by cargo bike"
    alice_saved = "Alice ferments her own hot sauce"
    bob_email = "bob@example.com"
    bob_seeded = "Bob cooks with a wok every evening"
    bob_saved = "Bob is learning to play the theremin"
    _seed_memory(session_env.memory_dir, alice_email, alice_seeded)
    _seed_memory(session_env.memory_dir, bob_email, bob_seeded)

    # Session 1: Authentik identity from a trusted peer, without a JWT.
    alice_client = connect_as(TRUSTED_PEER)
    with alice_client.websocket_connect(
        "/session",
        headers={
            "Origin": ORIGIN,
            "X-authentik-username": "alice",
            "X-authentik-email": alice_email,
        },
    ) as websocket:
        assert websocket.receive_json()["type"] == "ducke.init"

        alice_session = session_env.sessions[0]
        assert "save_memory" in alice_session["tools"]
        assert "recall_memories" in alice_session["tools"]
        assert alice_seeded in alice_session["instructions"]
        assert alice_email in alice_session["instructions"]
        assert bob_seeded not in alice_session["instructions"]

        recalled = _send_tool_call(websocket, "recall_memories", "alice-recall-1", {})
        assert recalled == {"facts": [alice_seeded], "topic": None}

        saved = _send_tool_call(
            websocket,
            "save_memory",
            "alice-save-1",
            {"fact": alice_saved, "category": "context"},
        )
        assert saved["status"] == "ok"

    # Session 2: JWT-resolved identity from an untrusted client that also
    # tries to name someone else with forwarded headers.
    bob_token = _mint_token(sub="bob-subject", email=bob_email)
    bob_client = connect_as(UNTRUSTED_PEER)
    with bob_client.websocket_connect(
        "/session",
        headers={
            "Origin": ORIGIN,
            "Authorization": f"Bearer {bob_token}",
            "X-Forwarded-Email": alice_email,
        },
    ) as websocket:
        assert websocket.receive_json()["type"] == "ducke.init"

        bob_session = session_env.sessions[1]
        assert bob_seeded in bob_session["instructions"]
        assert alice_seeded not in bob_session["instructions"]
        assert alice_email not in bob_session["instructions"]

        saved = _send_tool_call(
            websocket,
            "save_memory",
            "bob-save-1",
            {"fact": bob_saved, "category": "context"},
        )
        assert saved["status"] == "ok"

    assert [p.name for p in session_env.memory_dir.iterdir()] == sorted([
        _memory_file_name(alice_email),
        _memory_file_name(bob_email),
    ])
    assert _stored_fact_texts(session_env.memory_dir, alice_email) == [
        alice_seeded,
        alice_saved,
    ]
    assert _stored_fact_texts(session_env.memory_dir, bob_email) == [
        bob_seeded,
        bob_saved,
    ]
