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

DECAY_THRESHOLD = 0.3       # Prune facts below this confidence on load
DECAY_PERIOD_DAYS = 30      # Days between decay steps
AUTO_DECAY_RATE = 0.1       # Confidence lost per period for auto-extracted facts
EXPLICIT_DECAY_RATE = 0.05  # Slower decay for explicitly saved facts


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

        # Apply decay and prune stale facts; persist if anything changed
        if self._apply_decay():
            self.save()

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

    def _apply_decay(self) -> bool:
        """
        Apply time-based confidence decay to all facts.
        Facts unreferenced for 30+ days lose confidence: auto at 0.1/period, explicit at 0.05/period.
        Prunes facts whose confidence drops below DECAY_THRESHOLD.
        Returns True if any facts were pruned.
        """
        now = datetime.now(timezone.utc)
        surviving = []
        for fact in self._facts:
            try:
                last_ref = datetime.fromisoformat(fact.last_referenced)
                if last_ref.tzinfo is None:
                    last_ref = last_ref.replace(tzinfo=timezone.utc)
                days_stale = (now - last_ref).days
            except (ValueError, TypeError):
                days_stale = 0

            if days_stale >= DECAY_PERIOD_DAYS:
                periods = days_stale / DECAY_PERIOD_DAYS
                rate = EXPLICIT_DECAY_RATE if fact.source == FactSource.EXPLICIT else AUTO_DECAY_RATE
                fact.confidence = max(0.0, fact.confidence - rate * periods)

            if fact.confidence >= DECAY_THRESHOLD:
                surviving.append(fact)

        pruned = len(surviving) < len(self._facts)
        self._facts = surviving
        return pruned

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
        Add a structured fact about the user with deduplication and contradiction detection.
        Returns True if fact was added, False if duplicate/skipped.
        Enforces MAX_FACTS limit.
        """
        text = text.strip()
        if not text:
            return False

        # Explicit corrections always override with full confidence
        if source == FactSource.EXPLICIT and category == FactCategory.CORRECTION:
            confidence = 1.0

        # Deduplication: skip if exact or near-exact match exists
        if self._is_duplicate(text, category):
            return False

        # Contradiction detection: remove same-category facts that likely conflict.
        # Explicit saves use a lower threshold (more aggressive replacement) since
        # the user is intentionally correcting information.
        # CORRECTION category also checks across all categories.
        new_normalized = self._normalize_text(text)
        sim_threshold = 0.2 if source == FactSource.EXPLICIT else 0.4
        categories_to_check = list(FactCategory) if category == FactCategory.CORRECTION else [category]

        facts_to_remove = []
        for fact in self._facts:
            if fact.category in categories_to_check:
                existing_normalized = self._normalize_text(fact.text)
                if self._similarity_ratio(new_normalized, existing_normalized) >= sim_threshold:
                    facts_to_remove.append(fact)
        for fact in facts_to_remove:
            self._facts.remove(fact)

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
                                await self.add_fact_with_semantic_dedup(
                                    text=text,
                                    api_key=api_key,
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
        Returns: 'duplicate' | 'contradiction' | 'refinement' | 'distinct'
        """
        prompt = (
            "Compare these two facts about a user. Return ONLY one word:\n"
            "- 'duplicate' if they express the same information\n"
            "- 'contradiction' if they directly contradict each other\n"
            "- 'refinement' if Fact 2 is a more specific or updated version of Fact 1\n"
            "- 'distinct' if they are unrelated or independently useful\n\n"
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
                if result in ("duplicate", "contradiction", "refinement", "distinct"):
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

    def _facts_hash(self) -> str:
        """Compute a hash of current facts for summary cache invalidation."""
        content = json.dumps([f.to_dict() for f in self._facts], sort_keys=True)
        return hashlib.md5(content.encode()).hexdigest()

    async def get_or_generate_summary(
        self,
        api_key: str,
        cost_tracker=None,
        session_id: str | None = None,
        on_backend_cost=None,
    ) -> str:
        """Return a cached one-paragraph user summary, regenerating if facts changed."""
        if not self._facts:
            return ""
        current_hash = self._facts_hash()
        if (
            self._data.get("summary_facts_hash") == current_hash
            and self._data.get("user_summary")
        ):
            return self._data["user_summary"]
        summary = await self._generate_summary(api_key, cost_tracker, session_id, on_backend_cost)
        if summary:
            self._data["user_summary"] = summary
            self._data["summary_facts_hash"] = current_hash
            self.save()
        return summary

    async def _generate_summary(
        self,
        api_key: str,
        cost_tracker=None,
        session_id: str | None = None,
        on_backend_cost=None,
    ) -> str:
        """Generate a one-paragraph summary of the user from current facts."""
        facts_text = "\n".join(f"- {f.text}" for f in self._facts)
        prompt = (
            "Based on these facts about a user, write a single concise paragraph summarizing "
            "who they are and what they care about. Focus on the most important details. "
            "Write in third person (e.g., 'The user...'). Keep it to 2-4 sentences.\n\n"
            f"Facts:\n{facts_text}"
        )
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
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                        "max_tokens": 150,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
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
                            pass
                        if on_backend_cost is not None:
                            try:
                                await on_backend_cost(
                                    "gpt-5.4-nano",
                                    usage.get("prompt_tokens", 0),
                                    usage.get("completion_tokens", 0),
                                )
                            except Exception:
                                pass
                return data["choices"][0]["message"]["content"].strip()
        except Exception:
            return ""

    def get_facts_by_topic(self, topic: str) -> list[str]:
        """Return facts relevant to topic using keyword substring matching."""
        topic_words = [w.lower() for w in topic.split() if len(w) > 3]
        if not topic_words:
            return []
        matched = []
        for fact in self._facts:
            fact_lower = fact.text.lower()
            if any(word in fact_lower for word in topic_words):
                matched.append(fact.text)
        return matched

    async def get_facts_by_topic_async(self, topic: str, api_key: str) -> list[str]:
        """Return topic-relevant facts. Uses keyword matching, falls back to embeddings."""
        matches = self.get_facts_by_topic(topic)
        if matches:
            return matches
        return await self._get_facts_by_embedding(topic, api_key)

    async def _get_facts_by_embedding(self, topic: str, api_key: str) -> list[str]:
        """Use OpenAI text-embedding-3-small for cosine similarity retrieval."""
        if not self._facts:
            return []
        fact_texts = [f.text for f in self._facts]
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "text-embedding-3-small",
                        "input": [topic] + fact_texts,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            embeddings = [item["embedding"] for item in data["data"]]
            topic_emb = embeddings[0]
            fact_embs = embeddings[1:]

            def cosine_sim(a: list, b: list) -> float:
                dot = sum(x * y for x, y in zip(a, b))
                norm_a = sum(x * x for x in a) ** 0.5
                norm_b = sum(x * x for x in b) ** 0.5
                return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0

            THRESHOLD = 0.4
            scored = sorted(
                [(cosine_sim(topic_emb, emb), text) for emb, text in zip(fact_embs, fact_texts)],
                reverse=True,
            )
            return [text for score, text in scored if score >= THRESHOLD]
        except Exception:
            return []

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
                if 0.1 < similarity < 0.9:
                    result = await self.semantic_compare(text, fact.text, api_key)
                    if result == "duplicate":
                        return False
                    if result in ("contradiction", "refinement"):
                        # Replace old fact with new (contradiction: new overrides; refinement: new is more specific)
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
