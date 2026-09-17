"""Real-time voice loop: wake -> listen -> transcribe -> turn -> playback.

Composes the S01-S03 adapters (wake detector, VAD/STT/TTS resolution, audio
renderer) into the continuous loop the milestone requires. The loop arms on the
wake word, captures a VAD-bounded listen window, transcribes it, and runs one
turn through :class:`TurnManager`, with wake-word cooldown and barge-in
interrupting an in-flight turn.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, AsyncIterator

from jarvis.core.contracts import (
    AudioCapture,
    SpeechToText,
    TurnContext,
    VoiceActivityDetector,
    WakeDetector,
)
from jarvis.core.errors import ProviderUnavailable

from .turn_manager import TurnManager, TurnResult

if TYPE_CHECKING:
    from jarvis.adapters.audio.activation import ActivationManager

logger = logging.getLogger("jarvis.voice_loop")


class VoiceLoop:
    """Orchestrate the wake -> listen -> transcribe -> turn real-time loop."""

    def __init__(
        self,
        *,
        audio: AudioCapture,
        wake: WakeDetector,
        vad: VoiceActivityDetector,
        stt: SpeechToText,
        turn_manager: TurnManager,
        activation: "ActivationManager",
        min_silence_frames: int = 6,
        max_listen_frames: int = 160,
    ) -> None:
        self._audio = audio
        self._wake = wake
        self._vad = vad
        self._stt = stt
        self._turn_manager = turn_manager
        self._activation = activation
        self.min_silence_frames = min_silence_frames
        self.max_listen_frames = max_listen_frames
        self._stop_event = asyncio.Event()
        self._frames: AsyncIterator[bytes] | None = None
        self._phase = "idle"
        self._last_error: str | None = None
        self.turns: list[TurnResult] = []

    async def run(self, max_turns: int | None = None) -> None:
        """Run the loop until stopped, the audio source is exhausted, or *max_turns* turns complete."""
        context = TurnContext.fresh("voice_loop")
        self._frames = self._audio.capture(context)
        attempts = 0
        logger.info("voice loop running — listening")
        try:
            while not self._stop_event.is_set():
                if not await self._arm(context):
                    break
                text = await self._listen_and_transcribe(context)
                if text is None:
                    break
                if not text:
                    continue
                try:
                    result = await self._run_turn_with_barge_in(text, context)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._last_error = str(error)
                    logger.warning("turn failed: %s", error)
                    continue
                if result is not None:
                    self.turns.append(result)
                    logger.info("turn completed: route=%s", result.route)
                self._activation.note_turn_complete()
                attempts += 1
                if max_turns is not None and attempts >= max_turns:
                    self._stop_event.set()
                    break
        finally:
            await self._aclose_frames()
            self._phase = "stopped"

    async def _arm(self, context: TurnContext) -> bool:
        """Wait until a turn may begin; return False on exhaustion or stop."""
        if not self._activation.wake_word_required():
            return True
        frame_index = 0
        while not self._stop_event.is_set():
            if self._activation.in_cooldown():
                await asyncio.sleep(0.01)
                continue
            frame = await self._next_frame(context)
            if frame is None:
                return False
            if self._wake.detected(frame):
                self._activation.note_activation()
                logger.info("wake word detected")
                return True
            frame_index += 1
            if frame_index % 10 == 0:
                logger.info(
                    "wake score: %.3f (threshold %.3f)",
                    getattr(self._wake, "last_score", 0.0),
                    getattr(self._wake, "threshold", 0.0),
                )
        return False

    async def _listen_and_transcribe(self, context: TurnContext) -> str | None:
        """Capture a VAD-bounded window and transcribe it; None means exhausted."""
        self._phase = "listening"
        logger.info("listening for command")
        frames: list[bytes] = []
        silence = 0
        while len(frames) < self.max_listen_frames:
            frame = await self._next_frame(context)
            if frame is None:
                break
            frames.append(frame)
            if self._vad.is_speech(frame):
                silence = 0
            else:
                silence += 1
                if silence >= self.min_silence_frames:
                    break
        if not frames:
            return None

        async def _gen() -> AsyncIterator[bytes]:
            for frame in frames:
                yield frame

        parts: list[str] = []
        async for transcript in self._stt.transcribe(_gen(), context):
            text = transcript.text.strip()
            if text:
                parts.append(text)
        result = " ".join(parts)
        logger.info("transcript: %r", result)
        return result

    async def _run_turn_with_barge_in(
        self, text: str, context: TurnContext
    ) -> TurnResult | None:
        """Run one turn, allowing the wake word to interrupt it mid-flight."""
        self._phase = "thinking"
        task = asyncio.create_task(self._turn_manager.handle(text))
        # Yield once so handle() actually starts and registers the active turn
        # before any barge-in frame is processed; otherwise interrupt() sees no
        # active turn and the cancellation never reaches the in-flight work.
        await asyncio.sleep(0)
        try:
            while not task.done():
                if self._stop_event.is_set():
                    await self._turn_manager.interrupt()
                    break
                frame = await self._next_frame(context)
                if frame is not None and self._wake.detected(frame):
                    await self._turn_manager.interrupt()
                    self._activation.note_activation()
                    break
                await asyncio.sleep(0)
        finally:
            if not task.done():
                await task
        if task.cancelled():
            return None
        return task.result()

    async def _next_frame(self, context: TurnContext) -> bytes | None:
        """Read one frame; None on exhaustion or a transient provider failure."""
        try:
            return await anext(self._frames)
        except StopAsyncIteration:
            return None
        except ProviderUnavailable as error:
            self._last_error = str(error)
            return None

    async def _aclose_frames(self) -> None:
        if self._frames is None:
            return
        aclose = getattr(self._frames, "aclose", None)
        if aclose is None:
            return
        try:
            await aclose()
        except Exception:
            pass

    def request_stop(self) -> None:
        self._stop_event.set()

    def state(self) -> dict[str, object]:
        return {
            "phase": self._phase,
            "turns": len(self.turns),
            "active": self._turn_manager.active,
            "last_error": self._last_error,
        }
