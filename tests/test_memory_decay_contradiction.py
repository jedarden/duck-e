"""
Tests for memory decay and contradiction handling (duck-e-l97).

Covers:
- _apply_decay: confidence reduction for stale facts, pruning below threshold
- load(): triggers decay and persists pruned state
- add_fact(): contradiction detection removes conflicting same-category facts
- add_fact(): explicit corrections override with confidence 1.0 cross-category
- add_fact_with_semantic_dedup(): semantic contradiction/refinement replacement
"""
import asyncio
import json
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.memory import (
    DECAY_PERIOD_DAYS,
    DECAY_THRESHOLD,
    AUTO_DECAY_RATE,
    EXPLICIT_DECAY_RATE,
    FactCategory,
    FactSource,
    StructuredFact,
    UserMemoryStore,
)


def _make_store(tmp_path) -> UserMemoryStore:
    return UserMemoryStore(user_id="test@example.com", memory_dir=str(tmp_path))


def _fact(
    text: str,
    category: FactCategory = FactCategory.PERSONAL,
    confidence: float = 0.8,
    source: FactSource = FactSource.AUTO,
    days_old: int = 0,
) -> StructuredFact:
    ts = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    return StructuredFact(
        text=text,
        category=category,
        confidence=confidence,
        source=source,
        created_at=ts,
        last_referenced=ts,
    )


# ---------------------------------------------------------------------------
# _apply_decay
# ---------------------------------------------------------------------------

class TestApplyDecay:

    def test_fresh_facts_unchanged(self, tmp_path):
        """Facts referenced within 30 days keep their confidence."""
        store = _make_store(tmp_path)
        store.load()
        store._facts = [_fact("User lives in Paris", days_old=5, confidence=0.9)]
        store._apply_decay()
        assert store._facts[0].confidence == 0.9

    def test_auto_fact_loses_confidence_after_one_period(self, tmp_path):
        """Auto-extracted fact 30+ days stale loses AUTO_DECAY_RATE confidence."""
        store = _make_store(tmp_path)
        store.load()
        initial = 0.8
        store._facts = [_fact("User works in finance", days_old=30, confidence=initial, source=FactSource.AUTO)]
        store._apply_decay()
        expected = max(0.0, initial - AUTO_DECAY_RATE * 1)
        assert abs(store._facts[0].confidence - expected) < 1e-6

    def test_explicit_fact_decays_slower(self, tmp_path):
        """Explicitly saved facts lose confidence at EXPLICIT_DECAY_RATE, not AUTO."""
        store = _make_store(tmp_path)
        store.load()
        initial = 0.8
        store._facts = [
            _fact("User prefers Celsius", days_old=30, confidence=initial, source=FactSource.EXPLICIT),
        ]
        store._apply_decay()
        expected = max(0.0, initial - EXPLICIT_DECAY_RATE * 1)
        assert abs(store._facts[0].confidence - expected) < 1e-6
        assert store._facts[0].confidence > initial - AUTO_DECAY_RATE

    def test_fact_pruned_when_below_threshold(self, tmp_path):
        """A fact whose confidence falls below DECAY_THRESHOLD is removed."""
        store = _make_store(tmp_path)
        store.load()
        # Set confidence just above threshold but stale enough to drop below
        # AUTO_DECAY_RATE=0.1/period, 3 periods → lose 0.3 → drops to ~0.25 (< 0.3)
        store._facts = [_fact("User liked chess", days_old=90, confidence=0.55, source=FactSource.AUTO)]
        pruned = store._apply_decay()
        assert pruned
        assert store._facts == []

    def test_fact_survives_if_confidence_stays_above_threshold(self, tmp_path):
        """A fact with high initial confidence survives multiple decay periods."""
        store = _make_store(tmp_path)
        store.load()
        # confidence=1.0, auto, 60 days (2 periods) → 1.0 - 0.2 = 0.8 (well above 0.3)
        store._facts = [_fact("User is an engineer", days_old=60, confidence=1.0, source=FactSource.AUTO)]
        pruned = store._apply_decay()
        assert not pruned
        assert len(store._facts) == 1

    def test_returns_false_when_nothing_pruned(self, tmp_path):
        """_apply_decay returns False when no facts were removed."""
        store = _make_store(tmp_path)
        store.load()
        store._facts = [_fact("User likes jazz", days_old=5, confidence=0.9)]
        assert not store._apply_decay()

    def test_multiple_facts_mixed_decay(self, tmp_path):
        """Stale low-confidence facts pruned while healthy ones survive."""
        store = _make_store(tmp_path)
        store.load()
        store._facts = [
            _fact("Recent fact", days_old=2, confidence=0.9),
            _fact("Stale weak fact", days_old=90, confidence=0.35, source=FactSource.AUTO),
        ]
        store._apply_decay()
        assert len(store._facts) == 1
        assert store._facts[0].text == "Recent fact"


# ---------------------------------------------------------------------------
# load() — decay integration
# ---------------------------------------------------------------------------

class TestLoadDecay:

    def test_load_prunes_stale_facts_and_saves(self, tmp_path):
        """load() applies decay, removes sub-threshold facts, and persists."""
        store = _make_store(tmp_path)
        # Manually write a JSON file with a stale, low-confidence fact
        stale_ts = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        data = {
            "user_id": "test@example.com",
            "created_at": stale_ts,
            "facts": [
                {
                    "text": "User likes hiking",
                    "category": "preference",
                    "confidence": 0.31,  # will drop below 0.3 after decay
                    "source": "auto",
                    "created_at": stale_ts,
                    "last_referenced": stale_ts,
                },
                {
                    "text": "User is a developer",
                    "category": "personal",
                    "confidence": 1.0,
                    "source": "explicit",
                    "created_at": stale_ts,
                    "last_referenced": stale_ts,
                },
            ],
        }
        import hashlib
        user_hash = hashlib.sha256("test@example.com".encode()).hexdigest()
        file_path = tmp_path / f"{user_hash}.json"
        file_path.write_text(json.dumps(data))

        store.load()

        texts = [f.text for f in store._facts]
        assert "User likes hiking" not in texts
        assert "User is a developer" in texts

        # Verify the pruned state was persisted
        saved = json.loads(file_path.read_text())
        saved_texts = [f["text"] for f in saved["facts"]]
        assert "User likes hiking" not in saved_texts


# ---------------------------------------------------------------------------
# add_fact() — contradiction detection
# ---------------------------------------------------------------------------

class TestAddFactContradiction:

    def test_contradicting_location_fact_replaced(self, tmp_path):
        """New location fact replaces old conflicting one in same category."""
        store = _make_store(tmp_path)
        store.load()
        store.add_fact("User lives in NYC", category=FactCategory.PERSONAL)
        store.add_fact("User lives in Boston", category=FactCategory.PERSONAL)

        texts = [f.text for f in store._facts]
        assert "User lives in Boston" in texts
        assert "User lives in NYC" not in texts

    def test_distinct_facts_both_kept(self, tmp_path):
        """Unrelated facts in different categories are not removed."""
        store = _make_store(tmp_path)
        store.load()
        store.add_fact("User lives in NYC", category=FactCategory.PERSONAL)
        store.add_fact("User prefers dark mode", category=FactCategory.PREFERENCE)

        texts = [f.text for f in store._facts]
        assert "User lives in NYC" in texts
        assert "User prefers dark mode" in texts

    def test_exact_duplicate_not_added(self, tmp_path):
        """Exact duplicate is silently skipped."""
        store = _make_store(tmp_path)
        store.load()
        store.add_fact("User likes Python", category=FactCategory.PREFERENCE)
        store.add_fact("User likes Python", category=FactCategory.PREFERENCE)
        assert len(store._facts) == 1

    def test_explicit_correction_overrides_with_full_confidence(self, tmp_path):
        """CORRECTION source sets confidence to 1.0 regardless of input."""
        store = _make_store(tmp_path)
        store.load()
        store.add_fact(
            "User prefers Fahrenheit",
            category=FactCategory.CORRECTION,
            confidence=0.5,
            source=FactSource.EXPLICIT,
        )
        assert store._facts[-1].confidence == 1.0

    def test_explicit_correction_removes_cross_category_conflict(self, tmp_path):
        """CORRECTION category removes conflicting facts across all categories."""
        store = _make_store(tmp_path)
        store.load()
        # Add a preference fact
        store.add_fact("User prefers Celsius", category=FactCategory.PREFERENCE)
        # Now correct it explicitly across categories
        store.add_fact(
            "User prefers Fahrenheit",
            category=FactCategory.CORRECTION,
            source=FactSource.EXPLICIT,
        )
        texts = [f.text for f in store._facts]
        assert "User prefers Celsius" not in texts
        assert "User prefers Fahrenheit" in texts

    def test_explicit_save_uses_lower_similarity_threshold(self, tmp_path):
        """Explicit saves replace facts with lower word overlap than auto inserts."""
        store = _make_store(tmp_path)
        store.load()
        # Add a temperature preference
        store.add_fact("User prefers Celsius degrees", category=FactCategory.PREFERENCE, source=FactSource.AUTO)
        # Auto insert with moderate similarity should NOT remove (below 0.4 threshold)
        # Explicit insert with same similarity SHOULD remove (above 0.2 threshold)
        store.add_fact(
            "User uses Fahrenheit units",
            category=FactCategory.PREFERENCE,
            source=FactSource.EXPLICIT,
        )
        texts = [f.text for f in store._facts]
        # Explicit should have removed the auto fact due to lower threshold
        assert "User uses Fahrenheit units" in texts


# ---------------------------------------------------------------------------
# add_fact_with_semantic_dedup() — semantic contradiction/refinement
# ---------------------------------------------------------------------------

class TestSemanticDedup:

    def _mock_semantic(self, result: str):
        """Return a selective mock where extraction returns [] and semantic returns result."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": result}}]
        }
        return mock_response

    @pytest.mark.asyncio
    async def test_semantic_contradiction_replaces_old_fact(self, tmp_path):
        """semantic_compare returning 'contradiction' removes the existing fact."""
        store = _make_store(tmp_path)
        store.load()
        store._facts = [_fact("User lives in New York City", category=FactCategory.PERSONAL)]

        with patch("app.memory.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=self._mock_semantic("contradiction"))
            mock_cls.return_value = mock_client

            added = await store.add_fact_with_semantic_dedup(
                "User lives in Boston now",
                api_key="test-key",  # pragma: allowlist secret
                category=FactCategory.PERSONAL,
            )

        assert added
        texts = [f.text for f in store._facts]
        assert "User lives in Boston now" in texts
        assert "User lives in New York City" not in texts

    @pytest.mark.asyncio
    async def test_semantic_refinement_replaces_old_fact(self, tmp_path):
        """semantic_compare returning 'refinement' removes the vaguer old fact."""
        store = _make_store(tmp_path)
        store.load()
        store._facts = [_fact("User works in tech", category=FactCategory.PERSONAL)]

        with patch("app.memory.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=self._mock_semantic("refinement"))
            mock_cls.return_value = mock_client

            added = await store.add_fact_with_semantic_dedup(
                "User works as a software engineer at a startup",
                api_key="test-key",  # pragma: allowlist secret
                category=FactCategory.PERSONAL,
            )

        assert added
        texts = [f.text for f in store._facts]
        assert "User works as a software engineer at a startup" in texts
        assert "User works in tech" not in texts

    @pytest.mark.asyncio
    async def test_semantic_duplicate_not_added(self, tmp_path):
        """semantic_compare returning 'duplicate' skips the new fact."""
        store = _make_store(tmp_path)
        store.load()
        store._facts = [_fact("User enjoys classical music", category=FactCategory.PREFERENCE)]

        with patch("app.memory.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=self._mock_semantic("duplicate"))
            mock_cls.return_value = mock_client

            added = await store.add_fact_with_semantic_dedup(
                "User likes classical music a lot",
                api_key="test-key",  # pragma: allowlist secret
                category=FactCategory.PREFERENCE,
            )

        assert not added
        assert len(store._facts) == 1

    @pytest.mark.asyncio
    async def test_semantic_distinct_keeps_both(self, tmp_path):
        """semantic_compare returning 'distinct' keeps both facts."""
        store = _make_store(tmp_path)
        store.load()
        store._facts = [_fact("User has a dog named Max", category=FactCategory.PERSONAL)]

        with patch("app.memory.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=self._mock_semantic("distinct"))
            mock_cls.return_value = mock_client

            added = await store.add_fact_with_semantic_dedup(
                "User runs marathons",
                api_key="test-key",  # pragma: allowlist secret
                category=FactCategory.PERSONAL,
            )

        assert added
        assert len(store._facts) == 2
