"""
User memory store for DUCK-E.
Persists per-user facts in JSON files under /data/memory/.
"""
import hashlib
import httpx
import json
import os
from datetime import datetime, timezone
from pathlib import Path

MAX_FACTS = 100
DEFAULT_MEMORY_DIR = "/data/memory"


class UserMemoryStore:
    def __init__(self, user_id: str, memory_dir: str = DEFAULT_MEMORY_DIR):
        self.user_id = user_id
        self.memory_dir = Path(memory_dir)
        # Hash user_id to avoid path traversal and PII in filenames
        user_hash = hashlib.sha256(user_id.encode()).hexdigest()
        self.file_path = self.memory_dir / f"{user_hash}.json"
        self._data: dict = {}

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

    def save(self) -> None:
        """Persist memory to disk."""
        try:
            self.memory_dir.mkdir(parents=True, exist_ok=True)
            self._data["updated_at"] = datetime.now(timezone.utc).isoformat()
            with open(self.file_path, "w") as f:
                json.dump(self._data, f, indent=2)
        except OSError:
            pass  # Memory is best-effort; don't crash the session

    def add_fact(self, text: str) -> None:
        """Append a fact about the user. Enforces MAX_FACTS limit."""
        text = text.strip()
        if not text:
            return
        facts = self._data.get("facts", [])
        # Trim oldest facts if at limit
        if len(facts) >= MAX_FACTS:
            facts = facts[-(MAX_FACTS - 1):]
        facts.append({
            "text": text,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        self._data["facts"] = facts
        self.save()

    def get_facts(self) -> list[str]:
        """Return list of fact strings."""
        return [f["text"] for f in self._data.get("facts", [])]

    async def extract_and_save(
        self,
        user_text: str,
        assistant_text: str,
        api_key: str,
        cost_tracker=None,
        session_id: str | None = None,
    ) -> None:
        """
        Extract memorable facts from a conversation turn and save them.

        Calls gpt-5.4-nano with a focused extraction prompt to identify user-specific
        facts worth persisting (location, preferences, interests, personal details).
        Fire-and-forget safe — all errors are suppressed to avoid disrupting the session.
        """
        if not user_text.strip():
            return

        prompt = (
            "Extract facts about the USER worth remembering for future conversations. "
            "Focus on: location, preferences, interests, personal details, goals. "
            "Only extract facts about the USER — not information the assistant provided. "
            "Return ONLY a valid JSON array of concise fact strings, e.g. "
            '[\"User lives in Paris\", \"User prefers metric units\"]. '
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
                        "max_tokens": 256,
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

                content = data["choices"][0]["message"]["content"].strip()
                facts = json.loads(content)
                if isinstance(facts, list):
                    for fact in facts:
                        if isinstance(fact, str) and fact.strip():
                            self.add_fact(fact)
        except Exception:
            pass  # Extraction is best-effort; never crash the session
