"""Local, user-controlled memory adapters (SQLite + FTS5)."""

from .service import MemoryService
from .store import MemoryStore

__all__ = ["MemoryService", "MemoryStore"]
