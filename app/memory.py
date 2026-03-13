"""
User memory store for DUCK-E.
Persists per-user facts in JSON files under /data/memory/.
"""
import hashlib
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
