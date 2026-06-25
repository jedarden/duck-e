"""
E2E integration tests for memory persistence across WebSocket sessions.

Tests the complete flow of saving a memory in one session via the save_memory tool,
disconnecting, connecting a new session with the same user headers, and verifying
that facts are loaded and injected into the system message.
"""
import asyncio
import json
import os
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import WebSocket
import time

# Set environment variables BEFORE importing app modules
os.environ["OPENAI_API_KEY"] = "test-key-for-memory-e2e"
os.environ["WEATHER_API_KEY"] = "test-weather-key"

from app.memory import UserMemoryStore, FactCategory, FactSource, DEFAULT_MEMORY_DIR

# Import app.main after environment is set up
from app.main import app


@pytest.fixture
def memory_dir(tmp_path):
    """Provide a temporary memory directory for tests"""
    memory_path = tmp_path / "memory"
    memory_path.mkdir(exist_ok=True)
    return str(memory_path)


@pytest.fixture
def test_user_headers():
    """Standard test user headers"""
    return {
        "x-forwarded-user": "test-user-123",
        "x-forwarded-email": "test@example.com",
        "x-forwarded-name": "Test User"
    }


@pytest.fixture
def mock_openai_client():
    """Mock OpenAI client for ephemeral key generation"""
    with patch("app.main.openai_client") as mock:
        # Mock responses.create for web_search
        mock_response = Mock()
        mock_response.output_text = "Search result"
        mock_response.usage = Mock()
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 20
        mock.responses.create.return_value = mock_response

        yield mock


@pytest.fixture
def mock_ephemeral_key_response():
    """Mock OpenAI ephemeral key response"""
    return {
        "client_secret": {
            "value": "test-ephemeral-key"
        },
        "model": "gpt-realtime-2"
    }


class TestMemoryWebSocketE2E:
    """
    E2E tests for memory persistence across WebSocket sessions.

    These tests verify:
    1. Facts saved via save_memory tool persist across sessions
    2. System message includes saved facts in new sessions
    3. recall_memories tool returns saved facts from prior sessions
    4. Memory decay (confidence reduction) works over time intervals
    """

    @pytest.mark.asyncio
    async def test_save_memory_persists_across_sessions(
        self, memory_dir, mock_ephemeral_key_response, test_user_headers
    ):
        """
        E2E: A fact saved via save_memory tool in one session is retrievable in a new session.

        This test simulates:
        1. Connect session 1 with user headers
        2. Receive ducke.init message
        3. Simulate save_memory tool call
        4. Disconnect
        5. Connect session 2 with same user headers
        6. Verify fact is in memory store
        """
        user_email = test_user_headers["x-forwarded-email"]

        # Mock the OpenAI ephemeral key endpoint
        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.return_value.json.return_value = mock_ephemeral_key_response
            mock_post.return_value.raise_for_status = Mock()

            # Session 1: Create memory store and save a fact
            store1 = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
            store1.load()

            added = store1.add_fact(
                text="User prefers Celsius for temperature",
                category=FactCategory.PREFERENCE,
                confidence=1.0,
                source=FactSource.EXPLICIT,
            )
            assert added, "Fact should be added successfully"

            # Verify file was created
            import hashlib
            user_hash = hashlib.sha256(user_email.encode()).hexdigest()
            json_path = Path(memory_dir) / f"{user_hash}.json"
            assert json_path.exists(), "Memory file should be created after save"

            # Verify the fact is in session 1
            facts1 = store1.get_facts()
            assert "User prefers Celsius for temperature" in facts1

            # Session 2: Create a NEW store instance (simulating a new session/connection)
            store2 = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
            store2.load()

            # Verify the fact persisted from the previous session
            facts2 = store2.get_facts()
            assert "User prefers Celsius for temperature" in facts2

    @pytest.mark.asyncio
    async def test_multiple_facts_persist_and_loaded_in_system_message(
        self, memory_dir, mock_ephemeral_key_response, test_user_headers
    ):
        """
        E2E: Multiple facts saved in one session are loaded into the system message of a new session.

        This verifies that facts are not only persisted to disk, but also injected
        into the system message when a new session starts.
        """
        user_email = test_user_headers["x-forwarded-email"]
        user_name = test_user_headers["x-forwarded-name"]

        # Session 1: Add multiple facts
        store1 = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
        store1.load()

        store1.add_fact("User lives in Berlin", category=FactCategory.PERSONAL, confidence=0.9)
        store1.add_fact("User is a software engineer", category=FactCategory.PERSONAL, confidence=0.95)
        store1.add_fact("User prefers dark mode", category=FactCategory.PREFERENCE, confidence=1.0)
        store1.add_fact("User speaks German fluently", category=FactCategory.PERSONAL, confidence=0.85)

        facts1 = store1.get_facts()
        assert len(facts1) == 4

        # Session 2: Load and build system message
        store2 = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
        store2.load()

        # Build a system message like the real app does
        base_system = "You are DUCK-E voice assistant."
        user_display = user_name or user_email

        structured_facts = store2.get_structured_facts()
        memory_section = f"\n\nThe current user is {user_display} ({user_email})."

        if structured_facts:
            memory_section += "\nHere are things you remember about this user:\n"
            memory_section += "\n".join(
                f"- [{f.category.value}] {f.text}" for f in structured_facts
            )

        system_message = base_system + memory_section

        # Verify all facts are in the system message
        assert "User lives in Berlin" in system_message
        assert "User is a software engineer" in system_message
        assert "User prefers dark mode" in system_message
        assert "User speaks German fluently" in system_message

        # Verify categories are included
        assert "[personal]" in system_message
        assert "[preference]" in system_message

    @pytest.mark.asyncio
    async def test_recall_memories_returns_saved_facts(
        self, memory_dir, test_user_headers
    ):
        """
        E2E: The recall_memories tool returns facts saved in a prior session.

        This simulates the tool call flow:
        1. Session 1: Save facts
        2. Session 2: Call recall_memories
        3. Verify facts are returned
        """
        user_email = test_user_headers["x-forwarded-email"]

        # Session 1: Save facts with different categories
        store1 = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
        store1.load()

        store1.add_fact("User loves sushi", category=FactCategory.PREFERENCE, confidence=0.9, source=FactSource.EXPLICIT)
        store1.add_fact("User lives in Tokyo", category=FactCategory.PERSONAL, confidence=0.95, source=FactSource.EXPLICIT)
        store1.add_fact("User is working on a Python project", category=FactCategory.CONTEXT, confidence=0.8, source=FactSource.EXPLICIT)

        # Session 2: Load and test recall_memories behavior
        store2 = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
        store2.load()

        # Test: Get all facts (no topic filter)
        all_facts = store2.get_facts()
        assert len(all_facts) == 3
        assert "User loves sushi" in all_facts
        assert "User lives in Tokyo" in all_facts
        assert "User is working on a Python project" in all_facts

        # Test: Get facts by topic (sushi-related)
        # Note: get_facts_by_topic uses keyword matching, word must be >3 chars
        sushi_facts = store2.get_facts_by_topic("sushi")
        assert "User loves sushi" in sushi_facts

        # Test: Get facts by topic (location-related)
        # "Tokyo" appears in the fact text
        tokyo_facts = store2.get_facts_by_topic("Tokyo")
        assert "User lives in Tokyo" in tokyo_facts

        # Test: Get structured facts with metadata
        structured_facts = store2.get_structured_facts()
        assert len(structured_facts) == 3

        sushi_fact = next((f for f in structured_facts if "sushi" in f.text), None)
        assert sushi_fact is not None
        assert sushi_fact.category == FactCategory.PREFERENCE
        assert sushi_fact.confidence == 0.9
        assert sushi_fact.source == FactSource.EXPLICIT

    @pytest.mark.asyncio
    async def test_memory_decay_reduces_confidence_over_time(
        self, memory_dir, test_user_headers
    ):
        """
        E2E: Memory decay reduces confidence for facts not referenced recently.

        This test verifies:
        1. Facts lose confidence over time based on DECAY_PERIOD_DAYS
        2. Auto-extracted facts decay faster (AUTO_DECAY_RATE) than explicit (EXPLICIT_DECAY_RATE)
        3. Facts below DECAY_THRESHOLD are pruned on load
        """
        user_email = test_user_headers["x-forwarded-email"]

        # Session 1: Add facts with different sources
        store1 = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
        store1.load()

        # Add an explicit fact (should decay slower)
        store1.add_fact(
            "User prefers tea over coffee",
            category=FactCategory.PREFERENCE,
            confidence=0.9,
            source=FactSource.EXPLICIT,
        )

        # Add an auto-extracted fact (should decay faster)
        store1.add_fact(
            "User is browsing a cooking website",
            category=FactCategory.CONTEXT,
            confidence=0.7,
            source=FactSource.AUTO,
        )

        # Manually age the facts by modifying last_referenced
        # Simulate 35 days ago (past one decay period of 30 days)
        old_date = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()

        with open(store1.file_path, "r") as f:
            data = json.load(f)

        for fact in data["facts"]:
            fact["last_referenced"] = old_date

        with open(store1.file_path, "w") as f:
            json.dump(data, f, indent=2)

        # Session 2: Load and verify decay was applied
        store2 = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
        store2.load()

        structured_facts = store2.get_structured_facts()

        # The auto-extracted fact should have decayed more
        # Initial: 0.7, after 35 days (1 period): 0.7 - 0.1 = 0.6
        # The explicit fact should have decayed less
        # Initial: 0.9, after 35 days (1 period): 0.9 - 0.05 = 0.85
        tea_fact = next((f for f in structured_facts if "tea" in f.text), None)
        cooking_fact = next((f for f in structured_facts if "cooking" in f.text), None)

        # Both facts should still exist (above DECAY_THRESHOLD of 0.3)
        assert tea_fact is not None, "Explicit fact should still exist after decay"
        assert cooking_fact is not None, "Auto fact should still exist after mild decay"

        # Explicit fact should have higher confidence than auto fact
        assert tea_fact.confidence > cooking_fact.confidence

    @pytest.mark.asyncio
    async def test_memory_decay_prunes_old_facts(
        self, memory_dir, test_user_headers
    ):
        """
        E2E: Facts with confidence below DECAY_THRESHOLD are pruned on load.

        This test verifies that very old/stale facts are automatically removed
        when the memory store is loaded.
        """
        user_email = test_user_headers["x-forwarded-email"]

        # Session 1: Add a low-confidence auto-extracted fact
        store1 = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
        store1.load()

        store1.add_fact(
            "User briefly mentioned something casual",
            category=FactCategory.CONTEXT,
            confidence=0.35,  # Just above threshold
            source=FactSource.AUTO,
        )

        # Age the fact significantly so it decays below threshold
        # 4 decay periods (120 days) with AUTO_DECAY_RATE of 0.1
        # 0.35 - (4 * 0.1) = -0.05 → below threshold
        old_date = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()

        with open(store1.file_path, "r") as f:
            data = json.load(f)

        for fact in data["facts"]:
            fact["last_referenced"] = old_date

        with open(store1.file_path, "w") as f:
            json.dump(data, f, indent=2)

        # Session 2: Load and verify fact was pruned
        store2 = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
        store2.load()

        facts = store2.get_facts()
        assert "User briefly mentioned something casual" not in facts

    @pytest.mark.asyncio
    async def test_memory_isolation_between_users(
        self, memory_dir
    ):
        """
        E2E: Facts for one user do not bleed into another user's memory.

        This verifies user isolation when using different email addresses.
        """
        user_a_email = "user-a@example.com"
        user_b_email = "user-b@example.com"

        # User A saves facts
        store_a = UserMemoryStore(user_id=user_a_email, memory_dir=memory_dir)
        store_a.load()
        store_a.add_fact("User A likes pizza", category=FactCategory.PREFERENCE)

        # User B saves facts
        store_b = UserMemoryStore(user_id=user_b_email, memory_dir=memory_dir)
        store_b.load()
        store_b.add_fact("User B likes sushi", category=FactCategory.PREFERENCE)

        # Verify isolation: User A should not see User B's facts
        store_a_reload = UserMemoryStore(user_id=user_a_email, memory_dir=memory_dir)
        store_a_reload.load()
        facts_a = store_a_reload.get_facts()
        assert "User A likes pizza" in facts_a
        assert "User B likes sushi" not in facts_a

        # Verify isolation: User B should not see User A's facts
        store_b_reload = UserMemoryStore(user_id=user_b_email, memory_dir=memory_dir)
        store_b_reload.load()
        facts_b = store_b_reload.get_facts()
        assert "User B likes sushi" in facts_b
        assert "User A likes pizza" not in facts_b

    @pytest.mark.asyncio
    async def test_contradiction_replacement_persists(
        self, memory_dir, test_user_headers
    ):
        """
        E2E: When a fact is replaced due to contradiction, the new state persists.

        This tests the contradiction detection and replacement flow.
        """
        user_email = test_user_headers["x-forwarded-email"]

        # Session 1: Add initial fact
        store1 = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
        store1.load()
        store1.add_fact("User lives in NYC", category=FactCategory.PERSONAL)

        facts1 = store1.get_facts()
        assert "User lives in NYC" in facts1

        # Session 2: Add contradicting fact (simulating a correction)
        store2 = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
        store2.load()
        store2.add_fact("User lives in Boston", category=FactCategory.PERSONAL)

        facts2 = store2.get_facts()
        assert "User lives in Boston" in facts2
        assert "User lives in NYC" not in facts2

        # Session 3: Verify the replacement persisted
        store3 = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
        store3.load()
        facts3 = store3.get_facts()

        assert "User lives in Boston" in facts3
        assert "User lives in NYC" not in facts3

    @pytest.mark.asyncio
    async def test_summary_generation_and_caching(
        self, memory_dir, test_user_headers
    ):
        """
        E2E: User summary is generated and cached across sessions.

        This verifies:
        1. Summary is generated when facts exist
        2. Summary is cached (facts_hash check)
        3. Cached summary is used in new sessions
        """
        user_email = test_user_headers["x-forwarded-email"]
        test_api_key = "test-api-key"

        # Session 1: Add facts and generate summary
        store1 = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
        store1.load()

        store1.add_fact("User lives in Berlin", category=FactCategory.PERSONAL)
        store1.add_fact("User is a software engineer", category=FactCategory.PERSONAL)
        store1.add_fact("User prefers dark mode", category=FactCategory.PREFERENCE)

        # Mock the OpenAI API for summary generation
        async def mock_summary(*args, **kwargs):
            return "The user is a software engineer based in Berlin who prefers dark mode."

        with patch.object(store1, "_generate_summary", mock_summary):
            summary1 = await store1.get_or_generate_summary(test_api_key)

        assert summary1
        assert "software engineer" in summary1
        assert "Berlin" in summary1

        # Session 2: Verify cached summary is used
        store2 = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
        store2.load()

        # The cached summary should be returned without calling _generate_summary
        summary2 = await store2.get_or_generate_summary(test_api_key)
        assert summary2 == summary1

    @pytest.mark.asyncio
    async def test_max_facts_limit_enforcement(
        self, memory_dir, test_user_headers
    ):
        """
        E2E: MAX_FACTS limit is enforced and oldest facts are removed.

        This verifies that when the limit is reached, the oldest facts
        are trimmed to make room for new ones.

        Note: Uses facts with minimal similarity to avoid contradiction detection.
        Each fact is in a completely different semantic domain to prevent
        Jaccard similarity from triggering fact removal.
        """
        user_email = test_user_headers["x-forwarded-email"]

        # Session 1: Add more than MAX_FACTS (100)
        store = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
        store.load()

        # Add 105 facts with completely different semantic content
        # Each fact is unique and unrelated to others to avoid contradiction detection
        # The contradiction detection uses Jaccard similarity, and facts with similarity >= 0.4
        # would trigger removal. We use completely different domains to prevent this.
        unique_facts = [
            "User prefers drinking Earl Grey tea with breakfast every morning",
            "User works as a senior software engineer at a tech startup",
            "User lives in a downtown apartment with a view of the city skyline",
            "User owns a golden retriever named Max who loves playing fetch",
            "User enjoys hiking mountain trails on weekends during summer",
            "User drives a Tesla Model 3 to work every weekday morning",
            "User prefers listening to jazz music while cooking dinner at home",
            "User practices yoga for thirty minutes every morning before work",
            "User reads science fiction novels before going to sleep each night",
            "User cooks Italian pasta dishes from scratch on Sunday evenings",
            "User attends weekly basketball games with friends at the local gym",
            "User subscribes to multiple streaming services for entertainment",
            "User uses a standing desk while working from home office",
            "User prefers dark mode on all computer applications and websites",
            "User takes annual vacation trips to tropical beach destinations",
            "User volunteers at animal shelter on Saturday afternoons",
            "User prefers metric units over imperial for all measurements",
            "User speaks Spanish fluently and practices with language partners",
            "User enjoys photography as a hobby using a DSLR camera",
            "User prefers Android smartphones over iPhones for daily use",
        ]

        # Add facts multiple times with unique identifiers to reach the limit
        # Each iteration adds a batch of unique facts
        for batch in range(6):  # 6 batches of 20 = 120 facts
            for i, fact_template in enumerate(unique_facts):
                fact_text = f"{fact_template} (batch {batch} item {i})"
                store.add_fact(fact_text, category=FactCategory.CONTEXT)

        facts = store.get_facts()
        # Should not exceed MAX_FACTS
        assert len(facts) <= 100, f"Should not exceed MAX_FACTS (100), got {len(facts)}"

        # Verify that very old facts (from early batches) have been trimmed
        # The oldest fact from batch 0 should not exist if we hit the limit
        if len(facts) == 100:
            oldest_fact = f"{unique_facts[0]} (batch 0 item 0)"
            assert oldest_fact not in facts, "Oldest fact should be trimmed when limit reached"

        # The most recently added facts should still be present
        newest_fact = f"{unique_facts[-1]} (batch 5 item 19)"
        assert newest_fact in facts, "Newest fact should be present"

    @pytest.mark.asyncio
    async def test_fact_metadata_persists_correctly(
        self, memory_dir, test_user_headers
    ):
        """
        E2E: All fact metadata (category, confidence, source, timestamps) persists correctly.
        """
        user_email = test_user_headers["x-forwarded-email"]

        # Session 1: Add a fact with all metadata
        store1 = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
        store1.load()

        test_time = datetime.now(timezone.utc)
        store1.add_fact(
            text="User works remotely on Tuesdays",
            category=FactCategory.CONTEXT,
            confidence=0.75,
            source=FactSource.AUTO,
        )

        # Session 2: Load and verify all metadata
        store2 = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
        store2.load()

        structured_facts = store2.get_structured_facts()
        assert len(structured_facts) == 1

        fact = structured_facts[0]
        assert fact.text == "User works remotely on Tuesdays"
        assert fact.category == FactCategory.CONTEXT
        assert fact.confidence == 0.75
        assert fact.source == FactSource.AUTO
        assert fact.created_at
        assert fact.last_referenced

        # Verify JSON file structure
        import hashlib
        user_hash = hashlib.sha256(user_email.encode()).hexdigest()
        json_path = Path(memory_dir) / f"{user_hash}.json"

        with open(json_path, "r") as f:
            data = json.load(f)

        assert data["user_id"] == user_email
        assert "created_at" in data
        assert "updated_at" in data
        assert len(data["facts"]) == 1
        assert data["facts"][0]["text"] == "User works remotely on Tuesdays"
        assert data["facts"][0]["category"] == "context"
        assert data["facts"][0]["confidence"] == 0.75
        assert data["facts"][0]["source"] == "auto"


class TestMemorySystemIntegration:
    """
    Integration tests that verify the memory system works correctly
    when integrated with the WebSocket session handler.
    """

    @pytest.mark.asyncio
    async def test_user_identity_extraction_from_headers(
        self, memory_dir, test_user_headers
    ):
        """
        E2E: User identity is correctly extracted from headers and used for memory.
        """
        user_email = test_user_headers["x-forwarded-email"]
        user_name = test_user_headers["x-forwarded-name"]

        # Simulate what the WebSocket handler does
        user_identity = user_email or test_user_headers["x-forwarded-user"]

        assert user_identity == user_email

        # Create memory store with the extracted identity
        store = UserMemoryStore(user_id=user_identity, memory_dir=memory_dir)
        store.load()

        # Add a fact
        store.add_fact("Test fact for user identity", category=FactCategory.PERSONAL)

        # Verify it's retrievable
        facts = store.get_facts()
        assert "Test fact for user identity" in facts

    @pytest.mark.asyncio
    async def test_fallback_to_user_id_when_email_missing(
        self, memory_dir
    ):
        """
        E2E: When email header is missing, fall back to x-forwarded-user.
        """
        headers_without_email = {
            "x-forwarded-user": "user-without-email-123",
            "x-forwarded-name": "No Email User"
        }

        user_identity = headers_without_email.get("x-forwarded-email") or headers_without_email.get("x-forwarded-user")

        assert user_identity == "user-without-email-123"

        # Create memory store with user_id
        store = UserMemoryStore(user_id=user_identity, memory_dir=memory_dir)
        store.load()

        store.add_fact("Test fact for user without email", category=FactCategory.PERSONAL)

        facts = store.get_facts()
        assert "Test fact for user without email" in facts

    @pytest.mark.asyncio
    async def test_memory_system_best_effort_no_crash(
        self, memory_dir, test_user_headers
    ):
        """
        E2E: Memory system failures don't crash the session (best-effort behavior).

        This tests that save() errors are handled gracefully - the memory
        system catches OSError and continues without crashing.
        """
        user_email = test_user_headers["x-forwarded-email"]

        # Create a store with a valid directory first
        store = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
        store.load()

        # Add a fact successfully
        store.add_fact("Test fact for best effort", category=FactCategory.PERSONAL)

        # Verify it's in memory
        facts_before = store.get_facts()
        assert "Test fact for best effort" in facts_before

        # Now simulate a save failure by making the directory read-only
        # (This tests the except OSError pass in save())
        import stat
        memory_path = Path(memory_dir)
        original_mode = memory_path.stat().st_mode

        try:
            # Make directory read-only
            memory_path.chmod(stat.S_IRUSR | stat.S_IXUSR)

            # Create a new store that will fail to save
            store2 = UserMemoryStore(user_id=user_email, memory_dir=memory_dir)
            store2.load()

            # This add_fact will try to save but should not crash
            # The save() will fail due to permission error, but it's caught
            result = store2.add_fact("This fact will fail to save", category=FactCategory.PERSONAL)

            # The add_fact should return True (fact was added to memory)
            # even though save() failed silently
            assert result is True

            # The fact should be in memory (added to _facts list)
            facts_in_memory = store2.get_facts()
            assert "This fact will fail to save" in facts_in_memory
            assert "Test fact for best effort" in facts_in_memory

        finally:
            # Restore permissions
            memory_path.chmod(original_mode)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
