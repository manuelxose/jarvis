"""Ordered, cancellable, turn-tagged audio playback queue.

Every chunk is tagged with its turn ID. Cancellation (barge-in) flushes pending
chunks immediately so a cancelled turn never plays leftover audio later. A
single worker drains the queue in FIFO order, which prevents overlapping
responses. Device errors are swallowed per-chunk and reflected in the queue
state so a single bad frame cannot wedge playback.
"""

from __future__ import annotations

import asyncio
from collections import deque
import inspect
from typing import Any, AsyncIterator, Callable, Optional

from jarvis.core.contracts import AudioPlayer, TurnContext
from jarvis.core.turn import TurnCancelled


async def _silent_render(turn_id: str, chunk: bytes) -> None:
    """Default no-op renderer used when no audio device is available."""
    return None


class AudioOutputQueue:
    """Controlled audio playback queue satisfying the AudioPlayer contract."""

    def __init__(
        self,
        render: Optional[Callable[[str, bytes], Any]] = None,
        max_pending: int = 32,
        render_timeout_seconds: float = 5.0,
    ) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be at least 1")
        if render_timeout_seconds <= 0:
            raise ValueError("render_timeout_seconds must be positive")
        self._render = render or _silent_render
        self._max_pending = max_pending
        self._render_timeout_seconds = render_timeout_seconds
        self._pending: deque[tuple[str, bytes]] = deque()
        self._worker: Optional[asyncio.Task] = None
        self._stats: dict[str, int] = {
            "enqueued": 0,
            "played": 0,
            "flushed": 0,
            "render_errors": 0,
            "render_timeouts": 0,
        }
        self._current_turn: Optional[str] = None

    def state(self) -> dict[str, Any]:
        """Observable queue state for health and telemetry."""
        return {
            "pending": len(self._pending),
            "capacity": self._max_pending,
            "current_turn": self._current_turn,
            **self._stats,
        }

    async def play(self, audio: AsyncIterator[bytes], context: TurnContext) -> None:
        turn_id = context.trace_id
        try:
            async for chunk in audio:
                context.cancellation.raise_if_cancelled()
                if chunk:
                    while len(self._pending) >= self._max_pending:
                        context.cancellation.raise_if_cancelled()
                        await asyncio.sleep(0.001)
                    self._pending.append((turn_id, chunk))
                    self._stats["enqueued"] += 1
                    self._wake_worker()
            await self._wait_drained(turn_id, context)
        except TurnCancelled:
            # A cancelled turn must not leave queued chunks behind.
            self.flush(turn_id)
            raise

    def flush(self, turn_id: Optional[str] = None) -> int:
        """Drop pending chunks for *turn_id* (or all turns) immediately."""
        before = len(self._pending)
        if turn_id is None:
            self._pending.clear()
        else:
            self._pending = deque(item for item in self._pending if item[0] != turn_id)
        self._stats["flushed"] += before - len(self._pending)
        return before - len(self._pending)

    def _wake_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            while self._pending:
                turn_id, chunk = self._pending.popleft()
                self._current_turn = turn_id
                try:
                    result = self._render(turn_id, chunk)
                    if inspect.isawaitable(result):
                        await asyncio.wait_for(
                            result, timeout=self._render_timeout_seconds
                        )
                except asyncio.TimeoutError:
                    self._stats["render_errors"] += 1
                    self._stats["render_timeouts"] += 1
                except Exception:
                    # A device error must not wedge the queue; keep draining.
                    self._stats["render_errors"] += 1
                self._stats["played"] += 1
        finally:
            self._current_turn = None

    async def _wait_drained(self, turn_id: str, context: TurnContext) -> None:
        while self._current_turn == turn_id or any(
            item[0] == turn_id for item in self._pending
        ):
            context.cancellation.raise_if_cancelled()
            await asyncio.sleep(0.001)
        context.cancellation.raise_if_cancelled()
