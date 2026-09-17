"""Deterministic tests for the real-time voice loop orchestration.

The loop composes the S01-S03 adapters (wake detector, VAD-bounded listen
window, STT, turn manager, TTS, playback) into a continuous wake -> listen ->
transcribe -> turn cycle. These tests drive the loop with the deterministic
fakes from ``jarvis.adapters.fakes`` so barge-in, cooldown, exhaustion, and the
full-turn path can be exercised without audio hardware or provider credentials.
"""

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.audio.activation import ActivationManager
from jarvis.adapters.audio.vad import EnergyVAD
from jarvis.adapters.fakes import (
    EchoTTS,
    RecordingAudioPlayer,
    ScriptedAudioInput,
    ScriptedModel,
    ScriptedSTT,
    ScriptedWakeDetector,
)
from jarvis.application.routing import Router
from jarvis.application.turn_manager import TurnManager
from jarvis.application.voice_loop import VoiceLoop
from jarvis.core.contracts import Transcript, TurnContext


async def _noop_tools(name, arguments, context):
    context.cancellation.raise_if_cancelled()
    return "ok"


class _SlowModel:
    """Stream tokens slowly so a concurrent interrupt can barge in."""

    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay

    async def generate(self, prompt, context):
        for word in ("hola", "que", "tal", "amigo"):
            context.cancellation.raise_if_cancelled()
            yield word + " "
            await asyncio.sleep(self.delay)


class _RecordingWake:
    """Count ``detected`` calls and always report a wake word."""

    def __init__(self) -> None:
        self.calls = 0

    def detected(self, audio: bytes) -> bool:
        self.calls += 1
        return True


def _build(
    frames,
    *,
    wake=None,
    stt=None,
    model=None,
    activation=None,
    player=None,
    min_silence=6,
    max_listen=160,
):
    """Assemble a VoiceLoop around the deterministic fakes."""
    player = player if player is not None else RecordingAudioPlayer()
    manager = TurnManager(
        router=Router(),
        tools=_noop_tools,
        model=model if model is not None else ScriptedModel(),
        tts=EchoTTS(),
        audio=player,
    )
    loop = VoiceLoop(
        audio=ScriptedAudioInput(frames),
        wake=wake if wake is not None else ScriptedWakeDetector([True]),
        vad=EnergyVAD(),
        stt=stt if stt is not None else ScriptedSTT(),
        turn_manager=manager,
        activation=activation
        if activation is not None
        else ActivationManager(cooldown_seconds=0.0),
        min_silence_frames=min_silence,
        max_listen_frames=max_listen,
    )
    return loop, manager, player


class VoiceLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_deterministic_turn(self):
        player = RecordingAudioPlayer()
        loop, _, _ = _build(
            [b"\x00\x00" * 8] * 7,
            wake=ScriptedWakeDetector([True]),
            stt=ScriptedSTT([Transcript("hola jarvis", is_final=True)]),
            model=ScriptedModel(),
            player=player,
        )

        await loop.run()

        self.assertEqual(1, len(loop.turns))
        turn = loop.turns[0]
        self.assertEqual("fast_model", turn.route)
        self.assertTrue(turn.response)
        self.assertFalse(turn.cancelled)
        self.assertTrue(player.played)

    async def test_run_stops_after_max_turns(self):
        player = RecordingAudioPlayer()
        loop, _, _ = _build(
            [b"\x00\x00" * 8] * 14,
            wake=ScriptedWakeDetector([True, True, True]),
            stt=ScriptedSTT([Transcript("hola jarvis", is_final=True)]),
            model=ScriptedModel(),
            player=player,
        )

        await loop.run(max_turns=1)

        self.assertEqual(1, len(loop.turns))
        self.assertEqual("stopped", loop.state()["phase"])

    async def test_audio_exhaustion_clean_exit(self):
        loop, _, _ = _build([])

        await loop.run()

        self.assertEqual(0, len(loop.turns))
        self.assertEqual("stopped", loop.state()["phase"])

    async def test_empty_transcript_skipped(self):
        # ScriptedSTT([]) falls back to its default transcript in the existing
        # fake, so produce a genuinely empty transcript to exercise the skip path.
        loop, _, _ = _build(
            [b"\x00\x00" * 8] * 7,
            stt=ScriptedSTT([Transcript("", is_final=True)]),
        )

        await loop.run()

        self.assertEqual(0, len(loop.turns))

    async def test_barge_in_cancels_in_flight_turn(self):
        loop, manager, _ = _build(
            [b"\x00\x00" * 8] * 8,
            wake=ScriptedWakeDetector([True, True]),
            stt=ScriptedSTT([Transcript("hola jarvis", is_final=True)]),
            model=_SlowModel(),
        )

        await loop.run()

        self.assertEqual(1, len(loop.turns))
        self.assertTrue(loop.turns[0].cancelled)
        self.assertFalse(manager.active)

    async def test_arm_waits_out_cooldown_then_detects_once(self):
        wake = _RecordingWake()
        activation = ActivationManager(cooldown_seconds=0.02)
        activation.note_activation()  # already in cooldown
        audio = ScriptedAudioInput([b"\x00\x00" * 8])
        manager = TurnManager(
            router=Router(),
            tools=_noop_tools,
            model=ScriptedModel(),
            tts=EchoTTS(),
            audio=RecordingAudioPlayer(),
        )
        loop = VoiceLoop(
            audio=audio,
            wake=wake,
            vad=EnergyVAD(),
            stt=ScriptedSTT(),
            turn_manager=manager,
            activation=activation,
        )
        loop._frames = audio.capture(TurnContext.fresh("test"))

        result = await loop._arm(TurnContext.fresh("test"))

        self.assertTrue(result)
        self.assertEqual(1, wake.calls)

    async def test_state_reports_stopped_and_turn_count(self):
        loop, _, _ = _build(
            [b"\x00\x00" * 8] * 7,
            stt=ScriptedSTT([Transcript("hola jarvis", is_final=True)]),
        )

        await loop.run()

        state = loop.state()
        self.assertEqual("stopped", state["phase"])
        self.assertEqual(1, state["turns"])
        self.assertFalse(state["active"])


if __name__ == "__main__":
    unittest.main()
