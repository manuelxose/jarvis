"""MemoryService: relevance-bounded retrieval across memory tiers.

``MemoryService`` is a thin async facade over :class:`~jarvis.adapters.memory.store.MemoryStore`.
It satisfies the core :class:`~jarvis.core.contracts.MemoryProvider` contract
(``async def recall(query, context) -> list[str]``) and layers service-level
concerns on top of the store:

- **Relevance-bounded retrieval** — ``recall`` never dumps the store wholesale;
  it is capped at ``max_recall`` and skips the ``working`` and ``operational``
  tiers (ephemeral/operational records are not "relevant context").
- **Promotion criteria** — episodic (``conversation``) records graduate to
  ``long_term`` once they prove durable: either recalled repeatedly or stored
  with explicitly high confidence. See :meth:`promotion_criteria`.
- **Async consolidation** — :meth:`consolidate` applies the promotion criteria
  as a background maintenance pass.
- **Redaction** — :meth:`redact` removes a record and its search index, and
  :meth:`redact_content` scrubs a substring from every stored record.
"""

from __future__ import annotations

from typing import Optional

from jarvis.core.contracts import TurnContext

from .store import MemoryRecord, MemoryStore

# Confidence assigned to raw episodic turns. Kept low so a single unremarkable
# utterance never auto-promotes by the confidence criterion alone; only turns
# that are recalled repeatedly (touch count) or that were explicitly remembered
# with high confidence graduate to long_term.
_EPISODIC_CONFIDENCE = 0.3


class MemoryService:
    """Async-friendly facade over the local SQLite memory store."""

    def __init__(
        self,
        store: MemoryStore,
        *,
        max_recall: int = 6,
        promotion_min_touches: int = 2,
        promotion_min_confidence: float = 0.95,
    ) -> None:
        self._store = store
        self._max_recall = max_recall
        self._promotion_min_touches = promotion_min_touches
        self._promotion_min_confidence = promotion_min_confidence
        # In-memory promotion signal: how many distinct recalls touched a record
        # since it was ingested. Ephemeral by design (reset on restart).
        self._touch_counts: dict[int, int] = {}

    @property
    def store(self) -> MemoryStore:
        return self._store

    @property
    def promotion_criteria(self) -> dict[str, object]:
        """The thresholds used to graduate conversation -> long_term."""
        return {
            "min_touches": self._promotion_min_touches,
            "min_confidence": self._promotion_min_confidence,
        }

    def meets_promotion(self, record: MemoryRecord, touches: int) -> bool:
        """True if a conversation record qualifies for promotion to long_term."""
        return (
            touches >= self._promotion_min_touches
            or record.confidence >= self._promotion_min_confidence
        )

    async def recall(self, query: str, context: TurnContext) -> list[str]:
        """Return relevance-ranked memories, bounded and preference-weighted.

        Excludes ``working`` and ``operational`` tiers; those are not surfaced
        as prompt context. Each returned record is touched so its ``last_used``
        advances and its in-memory promotion signal increments.
        """
        results: list[str] = []
        for record in self._store.recall(
            query, limit=self._max_recall, exclude_tiers=("working", "operational")
        ):
            results.append(record.content)
            self._store.touch(record.id)
            self._touch_counts[record.id] = self._touch_counts.get(record.id, 0) + 1
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
        """Explicitly store a memory. High-confidence entries are promotion-eligible."""
        return self._store.add(
            content, tier=tier, category=category, source=source, confidence=confidence
        )

    def record_turn(self, role: str, content: str) -> Optional[int]:
        """Record one conversation turn (episodic memory).

        Stored at low confidence so raw chatter only promotes to long_term when
        it is recalled repeatedly, not merely because it was spoken once.
        Returns the new record id, or ``None`` if the turn was empty.
        """
        if not content.strip():
            return None
        return self._store.add(
            content,
            tier="conversation",
            category="turn",
            source=role,
            confidence=_EPISODIC_CONFIDENCE,
        )

    async def consolidate(self) -> dict[str, int]:
        """Apply promotion criteria to conversation records.

        Background maintenance: promote episodic memories that meet the
        promotion criteria to ``long_term``. Returns a summary of the pass.
        """
        inspected = 0
        promoted = 0
        for record in self._store.list(tier="conversation", limit=1000):
            inspected += 1
            touches = self._touch_counts.get(record.id, 0)
            if self.meets_promotion(record, touches):
                if self._store.promote(record.id):
                    promoted += 1
                    self._touch_counts.pop(record.id, None)
        return {
            "inspected": inspected,
            "promoted": promoted,
            "remaining": inspected - promoted,
        }

    async def redact(self, record_id: int) -> bool:
        """Permanently remove a record and its search index (privacy redaction)."""
        self._touch_counts.pop(record_id, None)
        return self._store.delete(record_id)

    async def redact_content(self, token: str) -> int:
        """Scrub a substring from every stored record's content.

        Returns the number of records changed. A secret still present after the
        replacement is refused by the store (``MemoryError``) rather than kept,
        preserving the no-secret invariant.
        """
        token = (token or "").strip()
        if not token:
            return 0
        changed = 0
        for record in self._store.list(limit=1000):
            if token in record.content:
                new_content = record.content.replace(token, "[redacted]")
                if self._store.correct(record.id, new_content):
                    changed += 1
        return changed

    def get_preference(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self._store.get_preferences().get(key, default)

    def set_preference(self, key: str, value: str) -> None:
        self._store.set_preference(key, value)

    def promote(self, record_id: int) -> bool:
        """Manually promote a record to long_term (explicit escape hatch)."""
        if self._store.promote(record_id):
            self._touch_counts.pop(record_id, None)
            return True
        return False

    def delete(self, record_id: int) -> bool:
        if self._store.delete(record_id):
            self._touch_counts.pop(record_id, None)
            return True
        return False

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


__all__ = ["MemoryService"]
