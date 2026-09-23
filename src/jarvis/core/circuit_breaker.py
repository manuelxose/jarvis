"""A minimal per-key circuit breaker for flaky external providers.

Closed: requests pass through normally. After ``failure_threshold`` consecutive
failures the circuit opens and requests are rejected without contacting the
provider. After ``cooldown_seconds`` the circuit becomes half-open: exactly one
probe is allowed through; success closes it again, failure reopens it and
restarts the cooldown.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _State:
    failures: int = 0
    opened_at: float | None = None
    probing: bool = False


class CircuitBreaker:
    """Track failure/success per provider key and gate whether to try it."""

    def __init__(self, *, failure_threshold: int = 3, cooldown_seconds: float = 30.0) -> None:
        self._failure_threshold = max(1, failure_threshold)
        self._cooldown_seconds = max(0.0, cooldown_seconds)
        self._states: dict[str, _State] = {}

    def _state(self, name: str) -> _State:
        return self._states.setdefault(name, _State())

    def allow(self, name: str) -> bool:
        """Return whether a request to *name* should be attempted now."""
        state = self._state(name)
        if state.opened_at is None:
            return True
        if state.probing:
            return False  # a probe is already in flight
        if time.monotonic() - state.opened_at >= self._cooldown_seconds:
            state.probing = True
            return True
        return False

    def record_success(self, name: str) -> None:
        self._states[name] = _State()

    def record_failure(self, name: str) -> None:
        state = self._state(name)
        state.probing = False
        state.failures += 1
        if state.failures >= self._failure_threshold:
            # Refresh opened_at on every qualifying failure (including a failed
            # half-open probe) so the cooldown restarts each time.
            state.opened_at = time.monotonic()

    def is_open(self, name: str) -> bool:
        return not self.allow(name)
