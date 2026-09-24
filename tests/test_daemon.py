"""Sentinel daemon: activation -> startup -> session -> sleep, control socket, hotkey, VRAM guard."""

import asyncio
import json
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.apps import daemon
from jarvis.config import load_config
from jarvis.core.contracts import HealthReport, HealthStatus

EVENTS = []


class FakeMixer:
    music_playing = False

    def play_sfx(self, samples):
        EVENTS.append(("chime", time.perf_counter()))

    def load(self, path):
        raise FileNotFoundError(path)

    def ramp(self, *a):
        pass

    def fade_out(self, *a):
        pass

    def stop(self):
        EVENTS.append(("mixer_stop", 0))


class FakeRuntime:
    def __init__(self, config):
        EVENTS.append(("build", time.perf_counter()))
        self.stopped = asyncio.Event()
        self.spoken = []
        self.components = SimpleNamespace(
            turn_manager=SimpleNamespace(speak_text=self._speak, _tts=None),
            audio_output=SimpleNamespace(state=lambda: {"current_turn": None}),
            workspace=None,
        )
        self.supervisor = SimpleNamespace(health_snapshot=lambda: [HealthReport(n, HealthStatus.HEALTHY) for n in ("configuration", "storage path", "audio input", "audio output", "STT", "TTS", "fast model")])
        self.voice_loop = SimpleNamespace(request_stop=self.stopped.set, request_stop_after_turn=self.stopped.set)

    async def _speak(self, text):
        self.spoken.append(text)
        EVENTS.append(("welcome", time.perf_counter()))

    async def start(self):
        pass

    async def run_until_stopped(self):
        EVENTS.append(("listening", time.perf_counter()))
        await self.stopped.wait()

    async def stop(self):
        EVENTS.append(("runtime_stop", 0))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class DaemonTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        EVENTS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "config.json").write_text(json.dumps({"runtime": {}, "memory": {"db_path": str(root / "j.db")}, "daemon": {"hotkey": ""}}))
        self.config = load_config(root / "config.json")
        self.env = mock.patch.dict(os.environ, {"LOCALAPPDATA": str(root)})
        self.env.start()
        self.mic_callbacks = []

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def sentinel(self):
        def open_mic(callback):
            self.mic_callbacks.append(callback)
            return SimpleNamespace(stop=lambda: None, close=lambda: None)

        return daemon.Sentinel(self.config, runtime_factory=FakeRuntime, mixer_factory=FakeMixer, open_mic=open_mic)

    async def test_activation_runs_startup_then_voice_session_then_sleep(self):
        sentinel = self.sentinel()
        task = asyncio.create_task(sentinel.run())
        await asyncio.sleep(0.05)
        self.assertEqual(sentinel.state, "sentinel")
        sentinel.request_activation("hotkey")
        for _ in range(200):
            await asyncio.sleep(0.01)
            if sentinel.state == "active":
                break
        self.assertEqual(sentinel.state, "active")
        names = [e[0] for e in EVENTS]
        # The runtime is built while idle, so the chime never waits for it.
        self.assertLess(names.index("build"), names.index("chime"))
        self.assertLess(names.index("welcome"), names.index("listening"))
        report = json.loads((Path(self.tmp.name) / "jarvis" / "startup-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["phase"], "ready")
        self.assertIn("first_sound", report["timings_ms_since_gesture"])
        sentinel.sleep()  # "Jarvis, a dormir"
        for _ in range(100):
            await asyncio.sleep(0.01)
            if sentinel.state == "sentinel":
                break
        self.assertEqual(sentinel.state, "sentinel")
        self.assertIn("runtime_stop", [e[0] for e in EVENTS])
        self.assertEqual(len(self.mic_callbacks), 2)  # mic reopened for the next gesture
        await asyncio.sleep(0.05)
        self.assertEqual([e[0] for e in EVENTS].count("build"), 2)  # next session prepared while idle
        sentinel.shutdown()
        await asyncio.wait_for(task, 2)

    async def test_mic_is_suspended_during_session_so_own_audio_cannot_retrigger(self):
        sentinel = self.sentinel()
        task = asyncio.create_task(sentinel.run())
        await asyncio.sleep(0.05)
        sentinel.request_activation("control")
        await asyncio.sleep(0.2)
        self.assertTrue(sentinel.detector._suspended)
        sentinel.shutdown()
        await asyncio.wait_for(task, 2)

    async def test_control_socket_and_single_instance(self):
        sentinel = self.sentinel()
        port = free_port()
        server = await daemon.serve(sentinel, port)
        runner = asyncio.create_task(sentinel.run())
        try:
            with self.assertRaises(OSError):
                await daemon.serve(sentinel, port)  # a second daemon cannot bind
            status = await asyncio.to_thread(daemon.send_command, "status", port)
            self.assertEqual(status["state"], "sentinel")
            self.assertIn("listened_seconds", status["listener"])
            bad = await asyncio.to_thread(daemon.send_command, "rm -rf", port)
            self.assertFalse(bad["ok"])
            await asyncio.to_thread(daemon.send_command, "quit", port)
            await asyncio.wait_for(runner, 2)
        finally:
            server.close()
            await server.wait_closed()


class FakeClone:
    def __init__(self):
        self.starts = self.stops = 0

    async def start(self):
        self.starts += 1

    async def stop(self):
        self.stops += 1

    async def wait_ready(self):
        return {"profile": "owner", "load_ms": 1}


class VoiceRuntime(FakeRuntime):
    def __init__(self, config, voice_clone=None):
        super().__init__(config)
        self.voice_clone = voice_clone
        EVENTS.append(("voice_clone", voice_clone))
        self.voice_loop.last_activity = time.monotonic()


class ActivationStateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        EVENTS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "config.json").write_text(json.dumps({
            "runtime": {}, "memory": {"db_path": str(root / "j.db")},
            "daemon": {"hotkey": "", "session_idle_seconds": 0.2, "events_log": False},
            "voice": {"cooldown_seconds": 60, "max_gpu_mb": 0},
        }))
        self.config = load_config(root / "config.json")
        self.env = mock.patch.dict(os.environ, {"LOCALAPPDATA": str(root)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def sentinel(self):
        from jarvis.application.voice_manager import VoiceModelManager, VoicePolicy

        sentinel = daemon.Sentinel(self.config, runtime_factory=VoiceRuntime, mixer_factory=FakeMixer,
                                   open_mic=lambda cb: SimpleNamespace(stop=lambda: None, close=lambda: None))
        sentinel.voice = VoiceModelManager(FakeClone, VoicePolicy(max_gpu_mb=0, speculative_min_interval_seconds=0),
                                           free_vram=lambda: None, running_processes=set)
        return sentinel

    async def wait_for(self, predicate, timeout=2.0):
        for _ in range(int(timeout / 0.01)):
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("condition not reached")

    async def test_first_clap_speculates_and_expiry_undoes_it(self):
        sentinel = self.sentinel()
        task = asyncio.create_task(sentinel.run())
        await self.wait_for(lambda: sentinel.state == "sentinel")
        sentinel.detector.on_candidate(SimpleNamespace(time=1.0))  # from the audio thread
        await self.wait_for(lambda: sentinel.voice.state.value == "speculative")
        self.assertEqual(sentinel.state, "candidate")
        self.assertNotIn("chime", [e[0] for e in EVENTS])  # no music/chime on one clap
        sentinel.detector.on_candidate_expired("timeout")
        await self.wait_for(lambda: sentinel.voice.state.value == "cold")
        self.assertEqual((sentinel.state, sentinel.voice.tts.stops), ("sentinel", 1))
        sentinel.shutdown()
        await asyncio.wait_for(task, 2)

    async def test_confirmed_session_shares_one_model_and_auto_sleeps(self):
        sentinel = self.sentinel()
        task = asyncio.create_task(sentinel.run())
        await self.wait_for(lambda: sentinel.state == "sentinel")
        sentinel.detector.on_candidate(SimpleNamespace(time=1.0))
        sentinel.request_activation("claps")  # second clap
        await self.wait_for(lambda: sentinel.state == "active")
        clone = sentinel.voice.tts
        self.assertEqual((clone.starts, sentinel.voice.state.value), (1, "warm"))
        self.assertTrue(all(e[1] is clone for e in EVENTS if e[0] == "voice_clone"))
        sentinel.runtime.voice_loop.last_activity -= 10  # nobody talked: idle watchdog ends it
        await self.wait_for(lambda: sentinel.state == "sentinel", timeout=3)
        self.assertEqual((sentinel.voice.state.value, clone.stops), ("cooldown", 0))  # kept warm for reuse
        sentinel.request_activation("hotkey")
        await self.wait_for(lambda: sentinel.state == "active")
        self.assertEqual(clone.starts, 1)  # reused, not reloaded
        sentinel.shutdown()
        await asyncio.wait_for(task, 3)

    async def test_duplicate_activations_during_startup_are_ignored(self):
        sentinel = self.sentinel()
        task = asyncio.create_task(sentinel.run())
        await self.wait_for(lambda: sentinel.state == "sentinel")
        for source in ("claps", "wake_word", "hotkey"):
            sentinel.request_activation(source)
        await self.wait_for(lambda: sentinel.state == "active")
        self.assertEqual([e[0] for e in EVENTS].count("chime"), 1)
        sentinel.sleep()
        await self.wait_for(lambda: sentinel.state == "sentinel")
        await asyncio.sleep(0.1)
        self.assertEqual(sentinel.state, "sentinel")  # queued duplicates were dropped
        sentinel.shutdown()
        await asyncio.wait_for(task, 2)


class InterruptAndDeviceTests(ActivationStateTests):
    async def test_sleep_during_startup_interrupts_the_welcome(self):
        sentinel = self.sentinel()

        async def slow_speak(self_runtime, text):
            await asyncio.sleep(5)

        with mock.patch.object(VoiceRuntime, "_speak", slow_speak):
            task = asyncio.create_task(sentinel.run())
            await self.wait_for(lambda: sentinel.state == "sentinel")
            sentinel.request_activation("hotkey")
            await self.wait_for(lambda: sentinel.state == "starting")
            await asyncio.sleep(0.1)
            sentinel.sleep()
            await self.wait_for(lambda: sentinel.state == "sentinel", timeout=3)
        self.assertFalse(task.done())  # the daemon keeps running
        self.assertEqual(sentinel.voice.holders, frozenset())  # voice released
        sentinel.shutdown()
        await asyncio.wait_for(task, 2)

    async def test_missing_output_device_does_not_stop_activation(self):
        sentinel = self.sentinel()

        def no_device():
            raise OSError("no output device")

        sentinel._mixer_factory = no_device
        task = asyncio.create_task(sentinel.run())
        await self.wait_for(lambda: sentinel.state == "sentinel")
        sentinel.request_activation("hotkey")
        await self.wait_for(lambda: sentinel.state == "active")
        sentinel.shutdown()
        await asyncio.wait_for(task, 2)


class HotkeyTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(daemon.parse_hotkey("ctrl+alt+j"), (0x4003, ord("J")))
        self.assertEqual(daemon.parse_hotkey("win+shift+F9"), (0x400C, 0x78))
        for bad in ("", "hyper+j", "ctrl+enter"):
            with self.assertRaises(ValueError):
                daemon.parse_hotkey(bad)


class VramGuardTests(unittest.TestCase):
    def test_ollama_preload_skipped_when_clone_owns_the_gpu(self):
        from jarvis.adapters.models.ollama import OllamaProvider
        from jarvis.application import runtime as rt

        provider = OllamaProvider(base_url="http://127.0.0.1:1", model="m")
        with mock.patch.object(OllamaProvider, "warm_up") as warm:
            rt._warm_up(SimpleNamespace(providers=[provider]), None, 5000, free_vram=lambda: 1500)
            warm.assert_not_called()
            rt._warm_up(SimpleNamespace(providers=[provider]), None, 5000, free_vram=lambda: 7000)
            warm.assert_called_once()
            rt._warm_up(SimpleNamespace(providers=[provider]), None, None, free_vram=lambda: 10)
            self.assertEqual(warm.call_count, 2)  # no clone configured: no guard


if __name__ == "__main__":
    unittest.main()
