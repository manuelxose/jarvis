"""Typed in-process runtime event records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias

from .contracts import HealthReport
from .state import RuntimeState
from .turn import TurnContext


@dataclass(frozen=True)
class RuntimeStateChanged:
    previous: RuntimeState
    current: RuntimeState


@dataclass(frozen=True)
class HealthChanged:
    report: HealthReport


@dataclass(frozen=True)
class TurnStarted:
    context: TurnContext


@dataclass(frozen=True)
class TurnCancelled:
    context: TurnContext


@dataclass(frozen=True)
class TurnCompleted:
    context: TurnContext
    elapsed_ms: float


RuntimeEvent: TypeAlias = (
    RuntimeStateChanged | HealthChanged | TurnStarted | TurnCancelled | TurnCompleted
)


class EventSink(Protocol):
    async def publish(self, event: RuntimeEvent) -> None: ...
