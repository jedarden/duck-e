"""
User memory store for DUCK-E.
Persists per-user facts in JSON files under /data/memory/.
"""
import hashlib
import httpx
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal

MAX_FACTS = 100
DEFAULT_MEMORY_DIR = "/data/memory"


class FactCategory(str, Enum):
    """Categories for user memory facts."""
    PREFERENCE = "preference"
    PERSONAL = "personal"
    CORRECTION = "correction"
    CONTEXT = "context"


class FactSource(str, Enum):
    """Source of a memory fact."""
    AUTO = "auto"       # Automatically extracted from conversation
    EXPLICIT = "explicit"  # Explicitly saved via save_memory tool


@dataclass
class StructuredFact:
    """A structured memory fact with metadata."""
    text: str
    category: FactCategory
    confidence: float
    source: FactSource
    created_at: str
    last_referenced: str

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "category": self.category.value,
            "confidence": self.confidence,
            "source": self.source.value,
            "created_at": self.created_at,
            "last_referenced": self.last_referenced,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StructuredFact":
        return cls(
            text=data["text"],
            category=FactCategory(data["category"]),
            confidence=data["confidence"],
            source=FactSource(data["source"]),
            created_at=data["created_at"],
            last_referenced=data["last_referenced"],
        )


class UserMemoryStore:
    def __init__(self, user_id: str, memory_dir: str = DEFAULT_MEMORY_DIR):
        self.user_id = user_id
        self.memory_dir = Path(memory_dir)
        # Hash user_id to avoid path traversal and PII in filenames
        user_hash = hashlib.sha256(user_id.encode()).hexdigest()
        self.file_path = self.memory_dir / f"{user_hash}.json"
        self._data: dict = {}
        self._facts: list[StructuredFact] = []

    def load(self) -> None:
        """Load memory from disk. No-op if file doesn't exist."""
        if self.file_path.exists():
            try:
                with open(self.file_path, "r") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        if "facts" not in self._data:
            self._data["facts"] = []
        if "user_id" not in self._data:
            self._data["user_id"] = self.user_id
        if "created_at" not in self._data:
            self._data["created_at"] = datetime.now(timezone.utc).isoformat()

        # Load facts, handling both old and new formats
        self._facts = []
        for f in self._data.get("facts", []):
            if isinstance(f, dict):
                if "category" in f:
                    # New structured format
                    self._facts.append(StructuredFact.from_dict(f))
                else:
                    # Legacy format - migrate to new format
                    self._facts.append(StructuredFact(
                        text=f["text"],
                        category=FactCategory.CONTEXT,
                        confidence=0.7,
                        source=FactSource.AUTO,
                        created_at=f.get("created_at", datetime.now(timezone.utc).isoformat()),
                        last_referenced=f.get("created_at", datetime.now(timezone.utc).isoformat()),
                    ))

    def save(self) -> None:
        """Persist memory to disk."""
        try:
            self.memory_dir.mkdir(parents=True, exist_ok=True)
            self._data["facts"] = [f.to_dict() for f in self._facts]
            self._data["updated_at"] = datetime.now(timezone.utc).isoformat()
            with open(self.file_path, "w") as f:
                json.dump(self._data, f, indent=2)
        except OSError:
            pass  # Memory is best-effort; don't crash the session

    def _normalize_text(self, text: str) -> str:
        """Normalize text for comparison."""
        return " ".join(text.lower().split())

    def _is_duplicate(self, new_fact: str, category: FactCategory) -> bool:
        """Check if a fact already exists (exact or near-exact match)."""
        new_normalized = self._normalize_text(new_fact)
        for fact in self._facts:
            if fact.category == category:
                existing_normalized = self._normalize_text(fact.text)
                # Check for exact match or one containing the other
                if new_normalized == existing_normalized:
                    return True
                # Near-exact: one is a prefix of the other with significant overlap
                if len(new_normalized) > 20 and len(existing_normalized) > 20:
                    shorter, longer = sorted([new_normalized, existing_normalized], key=len)
                    if longer.startswith(shorter) or shorter in longer:
                        return True
        return False

    def add_fact(
        self,
        text: str,
        category: FactCategory = FactCategory.CONTEXT,
        confidence: float = 0.7,
        source: FactSource = FactSource.AUTO,
    ) -> bool:
        """
        Add a structured fact about the user with deduplication.
        Returns True if fact was added, False if duplicate/skipped.
        Enforces MAX_FACTS limit.
        """
        text = text.strip()
        if not text:
            return False

        # Deduplication: skip if exact or near-exact match exists
        if self._is_duplicate(text, category):
            return False

        # Trim oldest facts if at limit
        if len(self._facts) >= MAX_FACTS:
            self._facts = self._facts[-(MAX_FACTS - 1):]

        now = datetime.now(timezone.utc).isoformat()
        fact = StructuredFact(
            text=text,
            category=category,
            confidence=confidence,
            source=source,
            created_at=now,
            last_referenced=now,
        )
        self._facts.append(fact)
        self.save()
        return True

    def get_facts(self) -> list[str]:
        """Return list of fact strings, updating last_referenced timestamps."""
        now = datetime.now(timezone.utc).isoformat()
        for fact in self._facts:
            fact.last_referenced = now
        return [f.text for f in self._facts]

    def get_structured_facts(self) -> list[StructuredFact]:
        """Return list of structured facts with full metadata."""
        now = datetime.now(timezone.utc).isoformat()
        for fact in self._facts:
            fact.last_referenced = now
        return self._facts.copy()

    async def extract_and_save(
        self,
        user_text: str,
        assistant_text: str,
        api_key: str,
        cost_tracker=None,
        session_id: str | None = None,
        on_backend_cost=None,
    ) -> None:
        """
        Extract memorable facts from a conversation turn and save them.

        Calls gpt-5.4-nano with a focused extraction prompt to identify user-specific
        facts worth persisting (location, preferences, interests, personal details).
        Returns structured facts with category and confidence.
        Fire-and-forget safe — all errors are suppressed to avoid disrupting the session.
        """
        if not user_text.strip():
            return

        prompt = (
            "Extract facts about the USER worth remembering for future conversations. "
            "Categorize each fact and assign a confidence score (0.0-1.0).\n\n"
            "Categories:\n"
            "- preference: User's likes, dislikes, choices (e.g., 'prefers dark mode')\n"
            "- personal: Personal details (e.g., 'lives in Berlin', 'has two cats')\n"
            "- correction: User correcting previous assistant behavior\n"
            "- context: Other contextual information (e.g., 'working on Python project')\n\n"
            "Confidence scoring:\n"
            "- 0.9-1.0: Explicitly stated preference or fact\n"
            "- 0.7-0.9: Reasonably inferred from conversation\n\n"
            "Return ONLY a valid JSON array of objects with 'text', 'category', and 'confidence', e.g.\n"
            '[{"text": "User lives in Paris", "category": "personal", "confidence": 0.9}, '
            '{"text": "User prefers metric units", "category": "preference", "confidence": 0.85}]\n'
            "Return [] if nothing is worth saving."
        )
        turn = f"[User]: {user_text}\n[Assistant]: {assistant_text}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-5.4-nano",
                        "messages": [
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": turn},
                        ],
                        "temperature": 0,
                        "max_tokens": 512,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                # Track token usage if cost_tracker is provided
                if cost_tracker is not None and session_id is not None:
                    usage = data.get("usage", {})
                    if usage:
                        try:
                            await cost_tracker.track_usage(
                                session_id=session_id,
                                model="gpt-5.4-nano",
                                input_tokens=usage.get("prompt_tokens", 0),
                                output_tokens=usage.get("completion_tokens", 0),
                            )
                        except Exception:
                            pass  # Never crash the session over cost tracking
                        if on_backend_cost is not None:
                            try:
                                await on_backend_cost(
                                    "gpt-5.4-nano",
                                    usage.get("prompt_tokens", 0),
                                    usage.get("completion_tokens", 0),
                                )
                            except Exception:
                                pass

                content = data["choices"][0]["message"]["content"].strip()
                facts = json.loads(content)
                if isinstance(facts, list):
                    for fact in facts:
                        if isinstance(fact, dict) and "text" in fact:
                            text = fact["text"].strip()
                            if text:
                                try:
                                    category = FactCategory(fact.get("category", "context"))
                                except ValueError:
                                    category = FactCategory.CONTEXT
                                confidence = min(1.0, max(0.0, float(fact.get("confidence", 0.7))))
                                self.add_fact(
                                    text=text,
                                    category=category,
                                    confidence=confidence,
                                    source=FactSource.AUTO,
                                )
        except Exception:
            pass  # Extraction is best-effort; never crash the session

    async def semantic_compare(
        self,
        text1: str,
        text2: str,
        api_key: str,
    ) -> str:
        """
        Use gpt-5.4-nano to compare two facts semantically.
        Returns: 'duplicate' | 'contradiction' | 'distinct'
        """
        prompt = (
            "Compare these two facts about a user. Return ONLY one word:\n"
            "- 'duplicate' if they are the same or very similar\n"
            "- 'contradiction' if they contradict each other\n"
            "- 'distinct' if they are different but not contradictory\n\n"
            f"Fact 1: {text1}\nFact 2: {text2}"
        )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-5.4-nano",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0,
                        "max_tokens": 10,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                result = data["choices"][0]["message"]["content"].strip().lower()
                if result in ("duplicate", "contradiction", "distinct"):
                    return result
                return "distinct"
        except Exception:
            return "distinct"  # Fallback to safe default

    def _similarity_ratio(self, s1: str, s2: str) -> float:
        """Calculate simple Jaccard similarity between two strings."""
        if not s1 or not s2:
            return 0.0
        words1 = set(s1.split())
        words2 = set(s2.split())
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return intersection / union if union > 0 else 0.0

    async def add_fact_with_semantic_dedup(
        self,
        text: str,
        api_key: str,
        category: FactCategory = FactCategory.CONTEXT,
        confidence: float = 0.7,
        source: FactSource = FactSource.AUTO,
    ) -> bool:
        """
        Add a fact with semantic deduplication using gpt-5.4-nano.
        Falls back to simple dedup if API call fails.
        Returns True if fact was added, False if duplicate/skipped.
        """
        text = text.strip()
        if not text:
            return False

        # First check simple dedup
        if self._is_duplicate(text, category):
            return False

        # For borderline cases, use semantic comparison
        new_normalized = self._normalize_text(text)
        for fact in self._facts:
            if fact.category == category:
                existing_normalized = self._normalize_text(fact.text)
                # If strings are somewhat similar but not exact, use semantic comparison
                similarity = self._similarity_ratio(new_normalized, existing_normalized)
                if 0.3 < similarity < 0.9:
                    result = await self.semantic_compare(text, fact.text, api_key)
                    if result == "duplicate":
                        return False
                    if result == "contradiction":
                        # Remove old fact, will add new one
                        self._facts.remove(fact)
                        break

        # Trim oldest facts if at limit
        if len(self._facts) >= MAX_FACTS:
            self._facts = self._facts[-(MAX_FACTS - 1):]

        now = datetime.now(timezone.utc).isoformat()
        new_fact = StructuredFact(
            text=text,
            category=category,
            confidence=confidence,
            source=source,
            created_at=now,
            last_referenced=now,
        )
        self._facts.append(new_fact)
        self.save()
        return True
