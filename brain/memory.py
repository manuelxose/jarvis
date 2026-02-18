from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_MEMORY = {
    "conversations": [],
    "facts": {
        "nombre_usuario": "Manuel",
        "preferencias": {},
    },
}


class MemoryStore:
    """Simple persistent JSON memory for conversations and facts."""

    def __init__(self, memory_file: str | Path) -> None:
        self.memory_file = Path(memory_file)
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.memory_file.exists():
            self._write(DEFAULT_MEMORY)

    def _read(self) -> dict[str, Any]:
        with self._lock:
            with self.memory_file.open("r", encoding="utf-8") as handle:
                return json.load(handle)

    def _write(self, data: dict[str, Any]) -> None:
        with self._lock:
            with self.memory_file.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)

    def add_message(self, role: str, content: str) -> None:
        data = self._read()
        data.setdefault("conversations", []).append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "role": role,
                "content": content,
            }
        )
        self._write(data)

    def get_recent_messages(self, n: int = 10) -> list[dict[str, str]]:
        data = self._read()
        conversations = data.get("conversations", [])
        return conversations[-n:]

    def save_fact(self, key: str, value: Any) -> None:
        data = self._read()
        data.setdefault("facts", {})[key] = value
        self._write(data)

    def get_fact(self, key: str, default: Any = None) -> Any:
        data = self._read()
        return data.get("facts", {}).get(key, default)

    def clear_old_messages(self, days: int = 7) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        data = self._read()
        conversations = data.get("conversations", [])

        kept: list[dict[str, Any]] = []
        removed = 0
        for item in conversations:
            timestamp_raw = item.get("timestamp")
            try:
                timestamp = datetime.fromisoformat(timestamp_raw)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
            except Exception:
                # Keep malformed entries to avoid accidental data loss.
                kept.append(item)
                continue

            if timestamp >= cutoff:
                kept.append(item)
            else:
                removed += 1

        data["conversations"] = kept
        self._write(data)
        return removed

