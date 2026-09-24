"""Typed application events for observers (logs today, the command-center UI later).

Every event is ``{"v": 1, "name": ..., "ts": <epoch s>, "mono_ms": <monotonic ms>, **payload}``.
Payload fields are declared in :data:`SCHEMAS` (field -> accepted types); a
publish with missing or mistyped fields is dropped and logged, never raised,
so observability can never break the voice pipeline. Subscribers are plain
callables invoked synchronously (keep them cheap; hand work to your own queue).
Publishing is thread-safe: audio callbacks may publish.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("jarvis.events")

VERSION = 1
_NUM = (int, float)
_OPT_NUM = (int, float, type(None))

SCHEMAS: dict[str, dict[str, tuple[type, ...]]] = {
    # activation (sentinel)
    "activation.started": {"source": (str,)},  # first clap / wake word / hotkey
    "activation.cancelled": {"reason": (str,)},  # candidate expired or rejected
    "activation.confirmed": {"source": (str,), "confidence": _OPT_NUM},
    # startup sequence
    "startup.progress": {"phase": (str,)},
    "startup.completed": {"welcome_source": (str,), "timings_ms": (dict,)},
    "startup.degraded": {"issues": (list,), "phase": (str,)},
    # voice model lifecycle
    "voice.state": {"state": (str,), "reason": (str,)},
    "voice.loading": {"reason": (str,)},
    "voice.ready": {"load_ms": _OPT_NUM},
    "voice.evicted": {"reason": (str,)},
    # speech playback
    "speech.started": {"trace_id": (str,), "source": (str,)},  # source: live | cache
    "speech.completed": {"trace_id": (str,), "cancelled": (bool,)},
    # turns / agents
    "agent.started": {"trace_id": (str,), "route": (str,)},
    "agent.progress": {"trace_id": (str,), "detail": (str,)},
    "agent.completed": {"trace_id": (str,), "route": (str,), "ok": (bool,), "elapsed_ms": _NUM},
    # host
    "system.metrics": {"cpu_percent": _NUM, "ram_percent": _NUM, "gpu": (dict, type(None))},
}


class EventHub:
    """Thread-safe in-process publisher of versioned, schema-checked events.

    Invalid events are dropped and logged, and a failing subscriber never
    breaks the publisher.
    """

    def __init__(self) -> None:
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    @property
    def active(self) -> bool:
        return bool(self._subscribers)

    def publish(self, name: str, **payload: Any) -> Optional[dict[str, Any]]:
        problem = validate(name, payload)
        if problem:
            logger.warning("dropped event %s: %s", name, problem)
            return None
        event = {"v": VERSION, "name": name, "ts": round(time.time(), 3), "mono_ms": round(time.monotonic() * 1000, 1), **payload}
        with self._lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(event)
            except Exception:  # noqa: BLE001 - an observer never breaks the publisher
                logger.debug("event subscriber failed", exc_info=True)
        return event


def validate(name: str, payload: dict[str, Any]) -> str:
    """Return a problem description when *payload* does not match the event schema, else an empty string."""
    schema = SCHEMAS.get(name)
    if schema is None:
        return "unknown event name"
    for field, types in schema.items():
        if field not in payload:
            return f"missing field {field!r}"
        value = payload[field]
        if not isinstance(value, types) or (isinstance(value, bool) and bool not in types):
            return f"field {field!r} has type {type(value).__name__}"
    return ""


class JsonlSink:
    """Append events to a JSON-lines file, rotated at *max_bytes* (one backup)."""

    def __init__(self, path: Path, max_bytes: int = 5_000_000) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def __call__(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                    self.path.replace(self.path.with_suffix(".1.jsonl"))
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
            except OSError:
                logger.debug("event sink unavailable", exc_info=True)


hub = EventHub()  # process-wide default
