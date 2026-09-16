"""MemoryService: relevance-bounded retrieval across memory tiers.

Working memory is always surfaced (bounded); long-term, conversation, and
preference records are retrieved by relevance. Nothing is dumped wholesale.
"""

from __future__ import annotations

from typing import Optional

from jarvis.core.contracts import TurnContext
from jarvis.core.errors import MemoryError

from .store import MemoryStore


class MemoryService:
    """Async-friendly facade over the local SQLite memory store."""

    def __init__(self, store: MemoryStore, *, max_recall: int = 6) -> None:
        self._store = store
        self._max_recall = max_recall

    @property
    def store(self) -> MemoryStore:
        return self._store

    async def recall(self, query: str, context: TurnContext) -> list[str]:
        """Return relevance-ranked memories, bounded and preference-weighted."""
        results: list[str] = []
        for record in self._store.recall(
            query, limit=self._max_recall, exclude_tiers=("working",)
        ):
            results.append(record.content)
            self._store.touch(record.id)
        return results

    def remember(
        self,
        content: str,
        *,
        tier: str = "conversation",
        category: str = "general",
        source: str = "user",
        confidence: float = 1.0,
    ) -> int:
        return self._store.add(
            content, tier=tier, category=category, source=source, confidence=confidence
        )

    def record_turn(self, role: str, content: str) -> None:
        """Record one conversation turn (episodic memory)."""
        if not content.strip():
            return
        self._store.add(content, tier="conversation", category="turn", source=role)

    def get_preference(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self._store.get_preferences().get(key, default)

    def set_preference(self, key: str, value: str) -> None:
        self._store.set_preference(key, value)

    def promote(self, record_id: int) -> bool:
        return self._store.promote(record_id)

    def delete(self, record_id: int) -> bool:
        return self._store.delete(record_id)

    def inspect(self) -> list[dict[str, object]]:
        return [
            {
                "id": record.id,
                "tier": record.tier,
                "category": record.category,
                "content": record.content,
                "source": record.source,
                "confidence": record.confidence,
            }
            for record in self._store.list()
        ]

    def cleanup(self, retention_days: int) -> int:
        return self._store.cleanup(retention_days)

    def close(self) -> None:
        self._store.close()
