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
import io
import threading
import wave
from typing import Any, AsyncIterator, Callable, Optional

from jarvis.core.contracts import AudioPlayer, TurnContext
from jarvis.core.errors import ProviderUnavailable
from jarvis.core.turn import TurnCancelled


async def _silent_render(turn_id: str, chunk: bytes) -> None:
    """Default no-op renderer used when no audio device is available."""
    return None


def decode_wav(data: bytes) -> tuple[bytes, int, int, int]:
    """Decode a WAV blob into raw frames and its stream metadata.

    Returns ``(frames, framerate, channels, sampwidth)`` using only the stdlib
    ``wave`` module so the contract tier has no external dependency.
    """
    with wave.open(io.BytesIO(data), "rb") as wav:
        return (
            wav.readframes(wav.getnframes()),
            wav.getframerate(),
            wav.getnchannels(),
            wav.getsampwidth(),
        )


class StreamRenderer:
    """Play WAV chunks through ONE persistent output stream, abortable mid-chunk.

    Reopening the device per chunk (``sd.play``) adds a gap between chunks and
    cannot be interrupted until the chunk ends. Here chunks are written in
    short slices to a stream that stays open while the format is unchanged;
    :meth:`abort` stops the slice loop and drops the device buffer at once, and
    remembers the turn so a chunk of that turn already dequeued is skipped.
    ``sounddevice``/``numpy`` are lazy-imported so the contract tier stays
    importable without them.
    """

    def __init__(self, device: int | str | None = None, slice_seconds: float = 0.04) -> None:
        self._device = device
        self._slice_seconds = slice_seconds
        self._stream: Any = None
        self._format: Optional[tuple[int, int]] = None
        self._aborted: deque[str] = deque(maxlen=64)
        self._abort = threading.Event()
        self._playing: Optional[str] = None

    def abort(self, turn_id: str) -> None:
        self._aborted.append(turn_id)
        if self._playing == turn_id:
            self._abort.set()
            stream = self._stream
            if stream is not None:
                try:
                    stream.abort()  # discard audio already queued in the device buffer
                except Exception:
                    pass

    def close(self) -> None:
        stream, self._stream, self._format = self._stream, None, None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass

    def _open(self, sd: Any, rate: int, channels: int) -> Any:
        if self._stream is not None and self._format == (rate, channels):
            if not getattr(self._stream, "active", True):
                self._stream.start()  # restarted after an abort
            return self._stream
        self.close()
        stream = sd.OutputStream(samplerate=rate, channels=channels, dtype="int16", device=self._device)
        stream.start()
        self._stream, self._format = stream, (rate, channels)
        return stream

    def _blocking(self, turn_id: str, chunk: bytes) -> None:
        if turn_id in self._aborted:
            return
        try:
            import sounddevice as sd  # noqa: PLC0415
        except (ImportError, OSError) as error:
            # A sounddevice install without the PortAudio shared library raises
            # OSError at import, so it must read as "unavailable" too.
            raise ProviderUnavailable(
                "sounddevice/PortAudio is unavailable; audio playback unavailable",
                provider="audio",
            ) from error
        import numpy as np  # noqa: PLC0415

        frames, rate, channels, sampwidth = decode_wav(chunk)
        if sampwidth != 2:
            raise ProviderUnavailable("unsupported WAV sample width", provider="audio")
        samples = np.frombuffer(frames, dtype=np.int16).reshape(-1, channels)
        self._playing = turn_id
        self._abort.clear()
        try:
            stream = self._open(sd, rate, channels)
            step = max(1, int(rate * self._slice_seconds))
            for start in range(0, len(samples), step):
                if self._abort.is_set():
                    return
                stream.write(samples[start:start + step])
        except (getattr(sd, "PortAudioError", OSError), OSError) as error:
            if self._abort.is_set():
                return  # write interrupted by our own abort
            # Device open/write failures (unplugged device, PortAudio error) must
            # reach callers as ProviderUnavailable, not as a raw driver error.
            self.close()
            raise ProviderUnavailable(f"audio device playback failed: {error}", provider="audio") from error
        finally:
            self._playing = None

    async def __call__(self, turn_id: str, chunk: bytes) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._blocking, turn_id, chunk)


def make_sounddevice_render(device: int | str | None = None) -> StreamRenderer:
    """Build the persistent-stream device renderer (see :class:`StreamRenderer`)."""
    return StreamRenderer(device)


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
        # Also cut the chunk already on the device, not just the queued ones.
        target = turn_id or self._current_turn
        abort = getattr(self._render, "abort", None)
        if abort is not None and target is not None:
            abort(target)
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
