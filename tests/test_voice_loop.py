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
)
from jarvis.application.routing import Router
from jarvis.application.turn_manager import TurnManager
from jarvis.application.voice_loop import VoiceLoop
from jarvis.core.contracts import Transcript


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


class _PerUtteranceSTT(ScriptedSTT):
    async def transcribe(self, audio, context):
        async for _ in audio:
            context.cancellation.raise_if_cancelled()
        yield self.transcripts.pop(0)


def _build(
    frames,
    *,
    stt=None,
    model=None,
    activation=None,
    player=None,
    min_silence=6,
    max_listen=160,
    echo_tail=0,
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
        vad=EnergyVAD(),
        stt=stt if stt is not None else ScriptedSTT(),
        turn_manager=manager,
        activation=activation
        if activation is not None
        else ActivationManager(cooldown_seconds=0.0),
        min_silence_frames=min_silence,
        max_listen_frames=max_listen,
        echo_tail_frames=echo_tail,
    )
    return loop, manager, player


class VoiceLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_deterministic_turn(self):
        player = RecordingAudioPlayer()
        loop, _, _ = _build(
            [b"\xff\x7f" * 8] + [b"\x00\x00" * 8] * 6,
            stt=ScriptedSTT([Transcript("Jarvis, hola", is_final=True)]),
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

    async def test_stop_request_ends_listening_without_waiting_for_speech(self):
        class _EndlessSilence:
            async def capture(self, context):
                while True:
                    await asyncio.sleep(0.001)
                    yield b"\x00\x00" * 160

        loop, _, _ = _build([])
        loop._audio = _EndlessSilence()
        task = asyncio.create_task(loop.run())
        await asyncio.sleep(0.05)
        loop.request_stop()  # "Jarvis, a dormir" / daemon quit while nobody speaks
        await asyncio.wait_for(task, 1.0)
        self.assertEqual(loop.state()["phase"], "stopped")

    async def test_run_stops_after_max_turns(self):
        player = RecordingAudioPlayer()
        loop, _, _ = _build(
            [b"\xff\x7f" * 8] + [b"\x00\x00" * 8] * 6,
            stt=ScriptedSTT([Transcript("Jarvis, hola", is_final=True)]),
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

    async def test_leading_wake_word_executes_remainder_in_same_utterance(self):
        loop, _, _ = _build(
            [b"\xff\x7f" * 8] + [b"\x00\x00" * 8] * 6,
            stt=ScriptedSTT([Transcript("Jarvis, abre Spotify", is_final=True)]),
        )

        await loop.run(max_turns=1)

        self.assertEqual(1, len(loop.turns))
        self.assertEqual("abre Spotify", loop.turns[0].transcript)

    async def test_non_activation_utterance_is_discarded(self):
        loop, _, _ = _build(
            [b"\xff\x7f" * 8] + [b"\x00\x00" * 8] * 6,
            stt=ScriptedSTT([Transcript("abre Spotify", is_final=True)]),
        )

        await loop.run()

        self.assertEqual([], loop.turns)

    async def test_activation_starts_cooldown(self):
        activation = ActivationManager(cooldown_seconds=1.0)
        loop, _, _ = _build(
            [b"\xff\x7f" * 8] + [b"\x00\x00" * 8] * 6,
            stt=ScriptedSTT([Transcript("Jarvis, hola", is_final=True)]),
            activation=activation,
        )

        await loop.run(max_turns=1)

        self.assertTrue(activation.in_cooldown())

    async def test_wake_only_activation_listens_for_next_phrase(self):
        utterance = [b"\xff\x7f" * 8] + [b"\x00\x00" * 8] * 6
        loop, _, _ = _build(
            utterance * 2,
            stt=_PerUtteranceSTT(
                [
                    Transcript("Jarvis", is_final=True),
                    Transcript("abre Spotify", is_final=True),
                ]
            ),
        )

        await loop.run(max_turns=1)

        self.assertEqual(1, len(loop.turns))
        self.assertEqual("abre Spotify", loop.turns[0].transcript)

    async def test_empty_transcript_skipped(self):
        # ScriptedSTT([]) falls back to its default transcript in the existing
        # fake, so produce a genuinely empty transcript to exercise the skip path.
        loop, _, _ = _build(
            [b"\xff\x7f" * 8] + [b"\x00\x00" * 8] * 6,
            stt=ScriptedSTT([Transcript("", is_final=True)]),
        )

        await loop.run()

        self.assertEqual(0, len(loop.turns))

    async def test_short_noise_does_not_barge_in(self):
        loop, _, _ = _build(
            [b"\xff\x7f" * 8] + [b"\x00\x00" * 8] * 6 + [b"\xff\x7f" * 8] * 4,
            stt=ScriptedSTT([Transcript("Jarvis, hola", is_final=True)]),
            model=_SlowModel(),
        )

        await loop.run()

        self.assertEqual(1, len(loop.turns))
        self.assertFalse(loop.turns[0].cancelled)

    async def test_pre_roll_keeps_soft_onset_before_vad_fires(self):
        seen = []

        class _RecordingSTT(ScriptedSTT):
            async def transcribe(self, audio, context):
                seen.extend([frame async for frame in audio])
                yield Transcript("Jarvis, hola", is_final=True)

        soft = [bytes([n, 0]) * 8 for n in (1, 2, 3, 4)]
        loop, _, _ = _build(soft + [b"\xff\x7f" * 8] + [b"\x00\x00" * 8] * 6, stt=_RecordingSTT())

        await loop.run()

        self.assertEqual(soft[1:], seen[:3])
        self.assertEqual(b"\xff\x7f" * 8, seen[3])

    async def test_barge_in_cancels_in_flight_turn(self):
        loop, manager, _ = _build(
            [b"\xff\x7f" * 8] + [b"\x00\x00" * 8] * 6 + [b"\xff\x7f" * 8] * 5,
            stt=ScriptedSTT([Transcript("Jarvis, hola", is_final=True)]),
            model=_SlowModel(),
        )

        await loop.run()

        self.assertEqual(1, len(loop.turns))
        self.assertTrue(loop.turns[0].cancelled)
        self.assertFalse(manager.active)

    async def test_speaker_tail_after_reply_is_not_heard_as_a_new_utterance(self):
        from jarvis.application.turn_manager import TurnResult

        class _InstantTurns:
            active = False

            async def handle(self, text, **_):
                return TurnResult(transcript=text, response="ok", route="fast_model", elapsed_ms=1, trace={})

            async def interrupt(self):
                pass

        frames = [b"\xff\x7f" * 8] + [b"\x00\x00" * 8] * 6 + [b"\xff\x7f" * 8] * 2 + [b"\x00\x00" * 8] * 6
        for tail, expected_turns in ((0, 2), (3, 1)):
            with self.subTest(echo_tail=tail):
                stt = _PerUtteranceSTT([Transcript("Jarvis, hola", is_final=True), Transcript("Jarvis, eco", is_final=True)])
                loop = VoiceLoop(
                    audio=ScriptedAudioInput(list(frames)),
                    vad=EnergyVAD(),
                    stt=stt,
                    turn_manager=_InstantTurns(),
                    activation=ActivationManager(cooldown_seconds=0.0),
                    echo_tail_frames=tail,
                )
                await loop.run()
                self.assertEqual(expected_turns, len(loop.turns))

    async def test_state_reports_stopped_and_turn_count(self):
        loop, _, _ = _build(
            [b"\xff\x7f" * 8] + [b"\x00\x00" * 8] * 6,
            stt=ScriptedSTT([Transcript("Jarvis, hola", is_final=True)]),
        )

        await loop.run()

        state = loop.state()
        self.assertEqual("stopped", state["phase"])
        self.assertEqual(1, state["turns"])
        self.assertFalse(state["active"])


if __name__ == "__main__":
    unittest.main()
