"""Real-time voice loop: listen -> transcribe -> activate -> turn -> playback.

Composes the S01-S03 adapters (VAD/STT/TTS resolution and audio renderer) into
the continuous loop the milestone requires. Each utterance is VAD-bounded and
transcribed once before activation is resolved from its text.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import TYPE_CHECKING, Any, AsyncIterator

from jarvis.core.contracts import (
    AudioCapture,
    SpeechToText,
    TurnContext,
    VoiceActivityDetector,
)
from jarvis.adapters.audio.vad import rms_int16
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
        vad: VoiceActivityDetector,
        stt: SpeechToText,
        turn_manager: TurnManager,
        activation: "ActivationManager",
        min_silence_frames: int = 6,
        max_listen_frames: int = 160,
        pre_roll_frames: int = 3,
        barge_in_frames: int = 5,
        barge_in_energy_factor: float = 3.0,
        echo_tail_frames: int = 0,
        confirmer: Any = None,
    ) -> None:
        self._audio = audio
        self._vad = vad
        self._stt = stt
        self._turn_manager = turn_manager
        self._activation = activation
        self.min_silence_frames = min_silence_frames
        self.max_listen_frames = max_listen_frames
        # Frames heard just before the VAD fires: soft onsets ("Jar-") fall
        # below the energy floor and were being cut off the transcript.
        self.pre_roll_frames = pre_roll_frames
        # Barge-in needs sustained, loud speech so Jarvis's own voice from the
        # speakers or a short noise does not cancel the reply it is playing.
        self.barge_in_frames = barge_in_frames
        self.barge_in_energy_factor = barge_in_energy_factor
        # Frames dropped right after a reply: the speaker tail and room echo of
        # Jarvis's own voice must not be heard as the user's next utterance.
        self.echo_tail_frames = echo_tail_frames
        # A pending spoken confirmation (high-risk tool) takes the next utterance.
        self._confirmer = confirmer
        self.frames_per_second = 10  # MicCapture reads sample_rate // 10 per frame
        self._stop_event = asyncio.Event()
        self._frames: AsyncIterator[bytes] | None = None
        self._phase = "idle"
        self._last_error: str | None = None
        self.turns: list[TurnResult] = []
        self.last_activity = time.monotonic()  # session start / last completed turn

    async def run(self, max_turns: int | None = None) -> None:
        """Run the loop until stopped, the audio source is exhausted, or *max_turns* turns complete."""
        context = TurnContext.fresh("voice_loop")
        self.last_activity = time.monotonic()
        self._frames = self._audio.capture(context)
        attempts = 0
        logger.info("voice loop running — listening")
        try:
            while not self._stop_event.is_set():
                text = await self._capture_utterance(context)
                if text is None:
                    break
                if self._activation.wake_word_required():
                    heard = text
                    text = self._activation.command_after_wake_word(text)
                    if text is None:
                        if heard:
                            logger.info("ignored: no wake word at start of %r", heard)
                        continue
                    self._activation.note_activation()
                    if not text:
                        text = await self._capture_utterance(context)
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
                    logger.info(
                        "turn completed: route=%s total=%.0f ms trace=%s",
                        result.route,
                        result.elapsed_ms,
                        result.trace,
                    )
                self._activation.note_turn_complete()
                self.last_activity = time.monotonic()
                for _ in range(self.echo_tail_frames):
                    if await self._next_frame(context) is None:
                        break
                attempts += 1
                if max_turns is not None and attempts >= max_turns:
                    self._stop_event.set()
                    break
        finally:
            await self._aclose_frames()
            self._phase = "stopped"

    async def _capture_utterance(self, context: TurnContext) -> str | None:
        """Capture a VAD-bounded window and transcribe it; None means exhausted."""
        self._phase = "listening"
        logger.info("listening for command")
        frames: list[bytes] = []
        pre_roll: deque[bytes] = deque(maxlen=self.pre_roll_frames)
        silence = 0
        while len(frames) < self.max_listen_frames:
            if self._stop_event.is_set():
                return None  # "sleep"/quit must not wait for the next utterance
            frame = await self._next_frame(context)
            if frame is None:
                break
            if not frames:
                if not self._vad.is_speech(frame):
                    pre_roll.append(frame)
                    continue
                frames.extend(pre_roll)
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

        started = time.monotonic()
        parts: list[str] = []
        async for transcript in self._stt.transcribe(_gen(), context):
            text = transcript.text.strip()
            if text:
                parts.append(text)
        result = " ".join(parts)
        logger.info(
            "transcript: %r (%.1fs audio, stt %.0f ms)",
            result,
            len(frames) / max(self.frames_per_second, 1),
            (time.monotonic() - started) * 1000,
        )
        return result

    async def _run_turn_with_barge_in(
        self, text: str, context: TurnContext
    ) -> TurnResult | None:
        """Run one turn, allowing new speech to interrupt it mid-flight."""
        self._phase = "thinking"
        task = asyncio.create_task(self._turn_manager.handle(text))
        # Yield once so handle() actually starts and registers the active turn
        # before any barge-in frame is processed; otherwise interrupt() sees no
        # active turn and the cancellation never reaches the in-flight work.
        await asyncio.sleep(0)
        loud_frames = 0
        try:
            while not task.done():
                if self._stop_event.is_set():
                    await self._turn_manager.interrupt()
                    break
                if self._confirmer is not None and self._confirmer.waiting:
                    reply = await self._capture_utterance(context)
                    self._confirmer.answer(reply or "")
                    loud_frames = 0
                    self._phase = "thinking"
                    continue
                frame = await self._next_frame(context)
                loud_frames = loud_frames + 1 if frame is not None and self._is_barge_in(frame) else 0
                if self.barge_in_frames and loud_frames >= self.barge_in_frames:
                    logger.info("barge-in: user speech interrupted the reply")
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

    def _is_barge_in(self, frame: bytes) -> bool:
        threshold = getattr(self._vad, "threshold", None)
        if threshold is None:
            return self._vad.is_speech(frame)
        return rms_int16(frame) > threshold * self.barge_in_energy_factor

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
