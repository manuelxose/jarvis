"""Local, user-controlled SQLite memory store with FTS5 retrieval.

Tiers are separated deliberately: working memory is bounded and ephemeral,
conversation history is episodic, long-term memory holds durable facts, and
preferences are typed user settings. Records carry provenance, confidence,
recency, and category so retrieval can be relevance-bounded instead of dumping
the whole conversation log into every prompt.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from jarvis.core.errors import MemoryError


TIERS = ("working", "conversation", "long_term", "preference", "operational")

# Conservative secret refusal: never persist obvious credentials or tokens.
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|apikey|authorization|bearer|token|password|passwd|secret)"
               r"\s*[:=]\s*\S+"),
)


@dataclass(frozen=True)
class MemoryRecord:
    """One stored memory row (tier, category, content and usage timestamps)."""
    id: int
    tier: str
    category: str
    content: str
    source: str
    confidence: float
    created_at: float
    last_used: float


class MemoryStore:
    """Thread-safe SQLite memory store with an FTS5 retrieval index."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tier TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'general',
                    content TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'user',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    created_at REAL NOT NULL,
                    last_used REAL NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
                    content, content='records', content_rowid='id'
                );
                CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
                    INSERT INTO records_fts(rowid, content) VALUES (new.id, new.content);
                END;
                CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
                    INSERT INTO records_fts(records_fts, rowid, content)
                    VALUES ('delete', old.id, old.content);
                END;
                CREATE TRIGGER IF NOT EXISTS records_au AFTER UPDATE OF content ON records BEGIN
                    INSERT INTO records_fts(records_fts, rowid, content)
                    VALUES ('delete', old.id, old.content);
                    INSERT INTO records_fts(rowid, content)
                    VALUES (new.id, new.content);
                END;
                """
            )
            self._conn.commit()

    @staticmethod
    def _looks_sensitive(content: str) -> bool:
        return any(pattern.search(content) for pattern in _SECRET_PATTERNS)

    def add(
        self,
        content: str,
        *,
        tier: str = "conversation",
        category: str = "general",
        source: str = "user",
        confidence: float = 1.0,
    ) -> int:
        if tier not in TIERS:
            raise MemoryError(f"unknown memory tier: {tier}")
        if self._looks_sensitive(content):
            raise MemoryError("refusing to store content that looks like a secret")
        now = time.time()
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO records (tier, category, content, source, confidence, created_at, last_used)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (tier, category, content, source, confidence, now, now),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def touch(self, record_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE records SET last_used = ? WHERE id = ?", (time.time(), record_id)
            )
            self._conn.commit()

    def get(self, record_id: int) -> Optional[MemoryRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM records WHERE id = ?", (record_id,)
            ).fetchone()
        return self._from_row(row) if row else None

    def list(self, tier: Optional[str] = None, limit: int = 100) -> list[MemoryRecord]:
        with self._lock:
            if tier is None:
                rows = self._conn.execute(
                    "SELECT * FROM records ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM records WHERE tier = ? ORDER BY created_at DESC LIMIT ?",
                    (tier, limit),
                ).fetchall()
        return [self._from_row(row) for row in rows]

    def recall(self, query: str, *, limit: int = 6, exclude_tiers: Iterable[str] = ()) -> list[MemoryRecord]:
        """Relevance-ranked recall across long-term, conversation, and preference tiers."""
        tokens = _query_tokens(query)
        excluded = tuple(exclude_tiers)
        if not tokens:
            return []
        match_expr = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        placeholders = ",".join("?" for _ in excluded)
        sql = (
            "SELECT r.* FROM records r JOIN records_fts f ON f.rowid = r.id"
            " WHERE records_fts MATCH ?"
        )
        params: list[Any] = [match_expr]
        if excluded:
            sql += " AND r.tier NOT IN ({})".format(placeholders)
            params.extend(excluded)
        sql += " ORDER BY bm25(records_fts) LIMIT ?"
        params.append(limit)
        with self._lock:
            try:
                rows = self._conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                # FTS5 can throw on malformed match expressions; degrade safely.
                rows = []
        records = [self._from_row(row) for row in rows]
        seen = {record.id for record in records}
        if len(records) < limit:
            for row in self._fallback_recall(query, limit - len(records), excluded):
                record = self._from_row(row)
                if record.id not in seen:
                    records.append(record)
                    seen.add(record.id)
        return records

    def _fallback_recall(self, query: str, limit: int, exclude_tiers: tuple[str, ...]) -> list[sqlite3.Row]:
        """LIKE-based fallback when FTS5 returns too few matches."""
        tokens = _query_tokens(query)
        clauses = " OR ".join("content LIKE ?" for _ in tokens)
        params: list[Any] = [f"%{token}%" for token in tokens]
        sql = "SELECT * FROM records WHERE ({})".format(clauses)
        if exclude_tiers:
            sql += " AND tier NOT IN ({})".format(",".join("?" for _ in exclude_tiers))
            params.extend(exclude_tiers)
        sql += " ORDER BY last_used DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def delete(self, record_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def correct(self, record_id: int, content: str) -> bool:
        if self._looks_sensitive(content):
            raise MemoryError("refusing to store content that looks like a secret")
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE records SET content = ?, last_used = ? WHERE id = ?",
                (content, time.time(), record_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def get_preferences(self) -> dict[str, str]:
        result: dict[str, str] = {}
        with self._lock:
            rows = self._conn.execute(
                "SELECT category, content FROM records WHERE tier = 'preference'"
            ).fetchall()
        for row in rows:
            result[row["category"]] = row["content"]
        return result

    def set_preference(self, key: str, value: str) -> None:
        if self._looks_sensitive(f"{key}={value}"):
            raise MemoryError("refusing to store a secret preference")
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM records WHERE tier = 'preference' AND category = ?", (key,)
            ).fetchone()
            now = time.time()
            if existing:
                self._conn.execute(
                    "UPDATE records SET content = ?, last_used = ? WHERE id = ?",
                    (value, now, existing["id"]),
                )
            else:
                self._conn.execute(
                    "INSERT INTO records (tier, category, content, source, confidence, created_at, last_used)"
                    " VALUES ('preference', ?, ?, 'user', 1.0, ?, ?)",
                    (key, value, now, now),
                )
            self._conn.commit()

    def promote(self, record_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE records SET tier = 'long_term' WHERE id = ?", (record_id,)
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def cleanup(self, retention_days: int) -> int:
        """Remove conversation/episodic records whose last_used predates retention."""
        cutoff = time.time() - retention_days * 86400
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM records WHERE tier = 'conversation' AND last_used < ?", (cutoff,)
            )
            self._conn.commit()
            return cursor.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=int(row["id"]),
            tier=str(row["tier"]),
            category=str(row["category"]),
            content=str(row["content"]),
            source=str(row["source"]),
            confidence=float(row["confidence"]),
            created_at=float(row["created_at"]),
            last_used=float(row["last_used"]),
        )


def _query_tokens(query: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", query or "")
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9\s]", " ", without_accents.lower())
    tokens = [token for token in cleaned.split() if len(token) > 1]
    return tokens[:8]
