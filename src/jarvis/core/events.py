"""Typed in-process runtime event records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias

from .contracts import HealthReport
from .state import RuntimeState
from .turn import TurnContext


@dataclass(frozen=True)
class RuntimeStateChanged:
    """The supervisor moved between runtime states."""
    previous: RuntimeState
    current: RuntimeState


@dataclass(frozen=True)
class HealthChanged:
    """A component reported a new health status."""
    report: HealthReport


@dataclass(frozen=True)
class TurnStarted:
    """A turn began."""
    context: TurnContext


@dataclass(frozen=True)
class TurnCancelled:
    """A turn was cancelled (barge-in or stop)."""
    context: TurnContext


@dataclass(frozen=True)
class TurnCompleted:
    """A turn finished, with its duration."""
    context: TurnContext
    elapsed_ms: float


RuntimeEvent: TypeAlias = (
    RuntimeStateChanged | HealthChanged | TurnStarted | TurnCancelled | TurnCompleted
)


class EventSink(Protocol):
    """Receiver of runtime lifecycle events."""

    async def publish(self, event: RuntimeEvent) -> None: ...
