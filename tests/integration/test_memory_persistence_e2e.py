"""
E2E integration tests for memory persistence across sessions (duck-e-genesis).

Tests the complete flow of saving a memory in one session and retrieving it
in a new session, ensuring the JSON file store correctly persists data.
"""
import json
import pytest
from pathlib import Path

from app.memory import UserMemoryStore, FactCategory, FactSource


class TestMemoryPersistenceE2E:
    """Test that memories persist across UserMemoryStore instances (simulating new sessions)."""

    def test_fact_persists_across_store_instances(self, tmp_path):
        """
        E2E: A fact added in one UserMemoryStore instance is retrievable in a new instance.

        This simulates a user saving a memory in one session and having it available
        in a subsequent session after the server restarts or a new connection is made.
        """
        user_id = "test@example.com"

        # Session 1: Create a store, add a fact, and save
        store_session1 = UserMemoryStore(user_id=user_id, memory_dir=str(tmp_path))
        store_session1.load()

        added = store_session1.add_fact(
            text="User prefers Celsius for temperature",
            category=FactCategory.PREFERENCE,
            confidence=0.9,
            source=FactSource.EXPLICIT,
        )
        assert added, "Fact should be added successfully"

        # Verify the fact is in the first session
        facts_session1 = store_session1.get_facts()
        assert "User prefers Celsius for temperature" in facts_session1

        # Session 2: Create a NEW store instance (simulating a new session/connection)
        store_session2 = UserMemoryStore(user_id=user_id, memory_dir=str(tmp_path))
        store_session2.load()

        # Verify the fact persisted from the previous session
        facts_session2 = store_session2.get_facts()
        assert "User prefers Celsius for temperature" in facts_session2

    def test_multiple_facts_persist_across_sessions(self, tmp_path):
        """
        E2E: Multiple facts saved in one session are all retrievable in a new session.
        """
        user_id = "multi-fact-user@example.com"

        # Session 1: Add multiple facts
        store1 = UserMemoryStore(user_id=user_id, memory_dir=str(tmp_path))
        store1.load()

        store1.add_fact("User lives in London", category=FactCategory.PERSONAL)
        store1.add_fact("User is a software engineer", category=FactCategory.PERSONAL)
        store1.add_fact("User prefers dark mode", category=FactCategory.PREFERENCE)

        facts1 = store1.get_facts()
        assert len(facts1) == 3

        # Session 2: Load and verify all facts persisted
        store2 = UserMemoryStore(user_id=user_id, memory_dir=str(tmp_path))
        store2.load()

        facts2 = store2.get_facts()
        assert len(facts2) == 3
        assert "User lives in London" in facts2
        assert "User is a software engineer" in facts2
        assert "User prefers dark mode" in facts2

    def test_fact_metadata_persists_across_sessions(self, tmp_path):
        """
        E2E: Fact metadata (category, confidence, source) persists across sessions.
        """
        user_id = "metadata-test@example.com"

        # Session 1: Add a fact with specific metadata
        store1 = UserMemoryStore(user_id=user_id, memory_dir=str(tmp_path))
        store1.load()

        store1.add_fact(
            text="User works remotely on Tuesdays",
            category=FactCategory.CONTEXT,
            confidence=0.75,
            source=FactSource.AUTO,
        )

        # Session 2: Load and verify metadata persisted
        store2 = UserMemoryStore(user_id=user_id, memory_dir=str(tmp_path))
        store2.load()

        structured_facts = store2.get_structured_facts()
        assert len(structured_facts) == 1

        fact = structured_facts[0]
        assert fact.text == "User works remotely on Tuesdays"
        assert fact.category == FactCategory.CONTEXT
        assert fact.confidence == 0.75
        assert fact.source == FactSource.AUTO

    def test_user_isolation_different_users_no_bleed(self, tmp_path):
        """
        E2E: Facts for one user do not bleed into another user's memory.
        """
        # User A's session
        store_a = UserMemoryStore(user_id="user-a@example.com", memory_dir=str(tmp_path))
        store_a.load()
        store_a.add_fact("User A likes pizza", category=FactCategory.PREFERENCE)

        # User B's session
        store_b = UserMemoryStore(user_id="user-b@example.com", memory_dir=str(tmp_path))
        store_b.load()
        store_b.add_fact("User B likes sushi", category=FactCategory.PREFERENCE)

        # Verify isolation: User A should not see User B's facts
        store_a_reload = UserMemoryStore(user_id="user-a@example.com", memory_dir=str(tmp_path))
        store_a_reload.load()
        facts_a = store_a_reload.get_facts()
        assert "User A likes pizza" in facts_a
        assert "User B likes sushi" not in facts_a

        # Verify isolation: User B should not see User A's facts
        store_b_reload = UserMemoryStore(user_id="user-b@example.com", memory_dir=str(tmp_path))
        store_b_reload.load()
        facts_b = store_b_reload.get_facts()
        assert "User B likes sushi" in facts_b
        assert "User A likes pizza" not in facts_b

    def test_file_format_persistence_and_reload(self, tmp_path):
        """
        E2E: Verify the JSON file format is correct and can be reloaded.
        """
        user_id = "format-test@example.com"

        # Session 1: Create and save
        store1 = UserMemoryStore(user_id=user_id, memory_dir=str(tmp_path))
        store1.load()
        store1.add_fact("Test fact", category=FactCategory.PERSONAL)

        # Verify the JSON file was created and has correct structure
        import hashlib
        user_hash = hashlib.sha256(user_id.encode()).hexdigest()
        json_path = tmp_path / f"{user_hash}.json"

        assert json_path.exists(), "Memory file should be created"

        with open(json_path, "r") as f:
            data = json.load(f)

        assert data["user_id"] == user_id
        assert "created_at" in data
        assert "facts" in data
        assert len(data["facts"]) == 1
        assert data["facts"][0]["text"] == "Test fact"
        assert data["facts"][0]["category"] == "personal"

        # Session 2: Reload from the JSON file
        store2 = UserMemoryStore(user_id=user_id, memory_dir=str(tmp_path))
        store2.load()

        facts = store2.get_facts()
        assert "Test fact" in facts

    def test_updated_at_persists_across_sessions(self, tmp_path):
        """
        E2E: The updated_at timestamp is persisted correctly across sessions.
        """
        user_id = "timestamp-test@example.com"

        # Session 1: Create and save
        store1 = UserMemoryStore(user_id=user_id, memory_dir=str(tmp_path))
        store1.load()
        store1.add_fact("Initial fact", category=FactCategory.PERSONAL)

        # Get the initial updated_at
        import hashlib
        user_hash = hashlib.sha256(user_id.encode()).hexdigest()
        json_path = tmp_path / f"{user_hash}.json"

        with open(json_path, "r") as f:
            data1 = json.load(f)
        initial_updated_at = data1.get("updated_at")

        # Session 2: Add another fact
        store2 = UserMemoryStore(user_id=user_id, memory_dir=str(tmp_path))
        store2.load()
        store2.add_fact("Second fact", category=FactCategory.PREFERENCE)

        # Verify updated_at changed
        with open(json_path, "r") as f:
            data2 = json.load(f)
        second_updated_at = data2.get("updated_at")

        assert second_updated_at is not None
        assert second_updated_at >= initial_updated_at

    def test_contradaction_replacement_persists(self, tmp_path):
        """
        E2E: When a fact is replaced due to contradiction, the new state persists.
        """
        user_id = "contradiction-test@example.com"

        # Session 1: Add initial fact
        store1 = UserMemoryStore(user_id=user_id, memory_dir=str(tmp_path))
        store1.load()
        store1.add_fact("User lives in NYC", category=FactCategory.PERSONAL)

        facts1 = store1.get_facts()
        assert "User lives in NYC" in facts1

        # Session 2: Add contradicting fact
        store2 = UserMemoryStore(user_id=user_id, memory_dir=str(tmp_path))
        store2.load()
        store2.add_fact("User lives in Boston", category=FactCategory.PERSONAL)

        facts2 = store2.get_facts()
        assert "User lives in Boston" in facts2
        assert "User lives in NYC" not in facts2

        # Session 3: Verify the replacement persisted
        store3 = UserMemoryStore(user_id=user_id, memory_dir=str(tmp_path))
        store3.load()
        facts3 = store3.get_facts()

        assert "User lives in Boston" in facts3
        assert "User lives in NYC" not in facts3
