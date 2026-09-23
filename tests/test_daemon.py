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
        self.voice_loop = SimpleNamespace(request_stop=self.stopped.set)

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
        # The chime must not wait for the (slow) runtime build.
        self.assertLess(names.index("chime"), names.index("build"))
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
