"""Hardware-free cloud STT outage through real VAD, activation, and turn routing."""

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.audio.activation import ActivationManager
from jarvis.adapters.audio.vad import EnergyVAD
from jarvis.adapters.fakes import EchoTTS, RecordingAudioPlayer, ScriptedAudioInput
from jarvis.adapters.stt.fallback import STTChain
from jarvis.application import runtime
from jarvis.application.routing import Router
from jarvis.application.turn_manager import TurnManager
from jarvis.application.voice_loop import VoiceLoop
from jarvis.core.contracts import Transcript
from jarvis.core.errors import ProviderConfigError, ProviderUnavailable


SPEECH = b"\xff\x7f" * 8
SILENCE = b"\x00\x00" * 8
UTTERANCE = [SPEECH] + [SILENCE] * 6


class OfflineSTT:
    name = "local"

    def __init__(self, text="Jarvis, sube el volumen", *, unavailable=False):
        self.text = text
        self.unavailable = unavailable
        self.calls = 0
        self.audio = []

    async def transcribe(self, audio, context):
        self.calls += 1
        self.audio.extend([frame async for frame in audio])
        if self.unavailable:
            raise ProviderUnavailable("local STT unavailable", provider=self.name)
        yield Transcript(self.text, is_final=True)


class DownSTT:
    name = "cloud"

    def __init__(self):
        self.calls = 0

    async def transcribe(self, audio, context):
        self.calls += 1
        async for _ in audio:
            break  # prove the fallback replays consumed audio
        raise ProviderConfigError("cloud STT unavailable", provider=self.name)
        yield  # pragma: no cover


class ForbiddenModel:
    def __init__(self):
        self.calls = 0

    async def generate(self, prompt, context):
        self.calls += 1
        raise AssertionError("deterministic voice control invoked remote model")
        yield  # pragma: no cover


class OfflineVoiceTests(unittest.IsolatedAsyncioTestCase):
    def make_loop(self, frames, local=None, *, slow_tool=False):
        cloud = DownSTT()
        local = local or OfflineSTT()
        chain = STTChain([cloud, local], backoff_seconds=0)
        model = ForbiddenModel()
        calls = []

        async def tool(name, arguments, context):
            calls.append((name, arguments))
            if slow_tool:
                await asyncio.sleep(0.05)
                context.cancellation.raise_if_cancelled()
            return True

        manager = TurnManager(router=Router(), tools=tool, model=model,
                              tts=EchoTTS(), audio=RecordingAudioPlayer())
        loop = VoiceLoop(audio=ScriptedAudioInput(frames), vad=EnergyVAD(), stt=chain,
                         turn_manager=manager, activation=ActivationManager(cooldown_seconds=0),
                         barge_in_frames=5)
        return loop, manager, cloud, local, model, calls

    async def test_cloud_failure_replays_audio_to_local_and_dispatches_once(self):
        loop, manager, cloud, local, model, calls = self.make_loop(list(UTTERANCE))
        await loop.run()
        self.assertEqual(1, cloud.calls)
        self.assertEqual(1, local.calls)
        self.assertEqual(UTTERANCE, local.audio)
        self.assertEqual([("volume_up", {})], calls)
        self.assertEqual(1, len(loop.turns))
        self.assertEqual("fast_command", loop.turns[0].route)
        self.assertEqual("sube el volumen", loop.turns[0].transcript)
        self.assertEqual(0, model.calls)
        self.assertFalse(manager.active)

    async def test_total_stt_outage_is_observable_and_never_executes(self):
        local = OfflineSTT(unavailable=True)
        loop, manager, cloud, local, model, calls = self.make_loop(list(UTTERANCE), local)
        with self.assertLogs("jarvis.voice_loop", level="WARNING") as logs:
            await asyncio.wait_for(loop.run(), 1)
        self.assertEqual(1, cloud.calls)
        self.assertEqual(2, local.calls)  # bounded transient retry; no second utterance
        self.assertEqual([], calls)
        self.assertEqual([], loop.turns)
        self.assertEqual(0, model.calls)
        self.assertFalse(manager.active)
        self.assertIn("all STT providers failed", loop.state()["last_error"])
        self.assertTrue(any("STT failed; skipping utterance" in line for line in logs.output))

    async def test_noise_rejected_wake_rejected_and_empty_stt_skip_tools(self):
        for frames, text, expected in (
            ([SILENCE] * 8, "Jarvis, sube el volumen", 0),
            (list(UTTERANCE), "sube el volumen", 1),
            (list(UTTERANCE), "", 1),
        ):
            with self.subTest(text=text, frames=len(frames)):
                loop, _, cloud, local, model, calls = self.make_loop(frames, OfflineSTT(text))
                await loop.run()
                self.assertEqual(expected, cloud.calls)
                self.assertEqual(expected, local.calls)
                self.assertEqual([], calls)
                self.assertEqual([], loop.turns)
                self.assertEqual(0, model.calls)

    async def test_loud_barge_in_cancels_in_flight_fast_command(self):
        loop, manager, cloud, local, model, calls = self.make_loop(
            list(UTTERANCE) + [SPEECH] * 5, slow_tool=True)
        await loop.run()
        self.assertEqual(1, cloud.calls)
        self.assertEqual(1, local.calls)
        self.assertEqual([("volume_up", {})], calls)
        self.assertEqual(1, len(loop.turns))
        self.assertTrue(loop.turns[0].cancelled)
        self.assertFalse(manager.active)
        self.assertEqual(0, model.calls)

    def test_runtime_cloud_stt_composition_orders_local_fallback(self):
        cloud, local = DownSTT(), OfflineSTT()
        config = SimpleNamespace(stt=SimpleNamespace(model="tiny", language="es", device="cpu"),
                                 audio=SimpleNamespace(sample_rate=16000),
                                 activation=SimpleNamespace(wake_word="jarvis"))
        with patch.object(runtime, "resolve_stt", return_value=cloud), \
             patch.object(runtime, "stt_provider", return_value="alibaba_qwen"), \
             patch.object(runtime, "WhisperSTT", return_value=local) as whisper:
            chain = runtime._build_stt_chain(config)
        self.assertIsInstance(chain, STTChain)
        self.assertEqual((cloud, local), chain.providers)
        whisper.assert_called_once_with(model="tiny", language="es", device="cpu",
                                        sample_rate=16000, hotwords="jarvis")


if __name__ == "__main__":
    unittest.main()
