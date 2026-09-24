"""Runtime lifecycle states and validated transitions."""

from __future__ import annotations

import enum


class RuntimeState(str, enum.Enum):
    """Coarse state of the runtime, reported by the supervisor and ``jarvis run``."""
    STARTING = "starting"
    READY = "ready"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    DEGRADED = "degraded"
    FAILED = "failed"
    STOPPING = "stopping"


class InvalidTransition(ValueError):
    """Raised when a runtime state change is not allowed."""


_TRANSITIONS: dict[RuntimeState, frozenset[RuntimeState]] = {
    RuntimeState.STARTING: frozenset(
        {RuntimeState.READY, RuntimeState.DEGRADED, RuntimeState.FAILED, RuntimeState.STOPPING}
    ),
    RuntimeState.READY: frozenset(
        {RuntimeState.LISTENING, RuntimeState.DEGRADED, RuntimeState.FAILED, RuntimeState.STOPPING}
    ),
    RuntimeState.LISTENING: frozenset(
        {RuntimeState.THINKING, RuntimeState.INTERRUPTED, RuntimeState.DEGRADED, RuntimeState.FAILED, RuntimeState.STOPPING}
    ),
    RuntimeState.THINKING: frozenset(
        {RuntimeState.SPEAKING, RuntimeState.INTERRUPTED, RuntimeState.DEGRADED, RuntimeState.FAILED, RuntimeState.STOPPING}
    ),
    RuntimeState.SPEAKING: frozenset(
        {RuntimeState.LISTENING, RuntimeState.INTERRUPTED, RuntimeState.DEGRADED, RuntimeState.FAILED, RuntimeState.STOPPING}
    ),
    RuntimeState.INTERRUPTED: frozenset(
        {RuntimeState.LISTENING, RuntimeState.READY, RuntimeState.DEGRADED, RuntimeState.FAILED, RuntimeState.STOPPING}
    ),
    RuntimeState.DEGRADED: frozenset(
        {RuntimeState.READY, RuntimeState.FAILED, RuntimeState.STOPPING}
    ),
    RuntimeState.FAILED: frozenset({RuntimeState.STOPPING}),
    RuntimeState.STOPPING: frozenset({RuntimeState.FAILED}),
}


def transition(current: RuntimeState, target: RuntimeState) -> RuntimeState:
    """Validate and return a lifecycle transition."""
    if target not in _TRANSITIONS[current]:
        raise InvalidTransition(f"Cannot transition from {current.value} to {target.value}")
    return target
