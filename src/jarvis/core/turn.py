"""Per-turn cancellation and deadline context."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event
import time
from typing import TYPE_CHECKING
import uuid


if TYPE_CHECKING:
    from jarvis.config import RuntimeConfig


class TurnCancelled(Exception):
    """Raised when work continues after its turn was cancelled."""


class CancellationToken:
    """Thread-safe cancellation signal for a single runtime turn."""

    def __init__(self) -> None:
        self._event = Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise TurnCancelled()


@dataclass(frozen=True)
class TurnContext:
    """Immutable metadata and cooperative controls for one user turn."""

    trace_id: str
    conversation_id: str
    deadline_monotonic: float
    cancellation: CancellationToken

    @classmethod
    def from_config(cls, config: RuntimeConfig, conversation_id: str) -> TurnContext:
        deadline = time.monotonic() + config.runtime.command_deadline_ms / 1000
        return cls(
            trace_id=uuid.uuid4().hex,
            conversation_id=conversation_id,
            deadline_monotonic=deadline,
            cancellation=CancellationToken(),
        )

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self.deadline_monotonic
