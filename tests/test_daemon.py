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

    def prime(self):
        EVENTS.append(("prime", time.perf_counter()))

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


class SentinelCase(unittest.IsolatedAsyncioTestCase):
    claps = {}

    def setUp(self):
        EVENTS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "config.json").write_text(json.dumps({
            "runtime": {}, "memory": {"db_path": str(root / "j.db")},
            "daemon": {"hotkey": "", "session_idle_seconds": 0.2, "events_log": False},
            "voice": {"cooldown_seconds": 60, "max_gpu_mb": 0},
            "claps": self.claps,
        }))
        self.config = load_config(root / "config.json")
        self.env = mock.patch.dict(os.environ, {"LOCALAPPDATA": str(root)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def sentinel(self, runtime=VoiceRuntime):
        from jarvis.application.voice_manager import VoiceModelManager, VoicePolicy

        sentinel = daemon.Sentinel(self.config, runtime_factory=runtime, mixer_factory=FakeMixer,
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


class ActivationStateTests(SentinelCase):
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

    async def test_expiry_queued_behind_the_load_does_not_evict_a_real_activation(self):
        sentinel = self.sentinel()
        task = asyncio.create_task(sentinel.run())
        await self.wait_for(lambda: sentinel.state == "sentinel")
        sentinel.detector.on_candidate(SimpleNamespace(time=1.0))
        await self.wait_for(lambda: sentinel.voice.state.value == "speculative")
        sentinel.detector.on_candidate_expired("timeout")
        sentinel.state = "starting"  # the real claps landed before the queued cancel ran
        await asyncio.sleep(0.05)
        self.assertEqual((sentinel.voice.state.value, sentinel.voice.tts.stops), ("speculative", 0))
        sentinel.state = "sentinel"
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
        self.assertEqual(sentinel._output_status, "unavailable")
        sentinel.shutdown()
        await asyncio.wait_for(task, 2)

    async def test_output_is_primed_once_before_activation_and_again_after_sleep(self):
        sentinel = self.sentinel()
        task = asyncio.create_task(sentinel.run())
        await self.wait_for(lambda: sentinel._output_status == "ready")
        names = [e[0] for e in EVENTS]
        self.assertEqual((names.count("prime"), names.count("chime")), (1, 0))  # warm while idle, silent
        sentinel.request_activation("hotkey")
        await self.wait_for(lambda: sentinel.state == "active")
        names = [e[0] for e in EVENTS]
        self.assertEqual(names.count("prime"), 1)  # activation never re-opens the stream
        self.assertLess(names.index("prime"), names.index("chime"))
        sentinel.sleep()
        await self.wait_for(lambda: sentinel.state == "sentinel")  # mixer.stop closed the stream
        await self.wait_for(lambda: sentinel._output_status == "ready")
        self.assertEqual([e[0] for e in EVENTS].count("prime"), 2)  # re-primed for the next gesture
        sentinel.request_activation("hotkey")
        await self.wait_for(lambda: sentinel.state == "active")
        self.assertEqual([e[0] for e in EVENTS].count("chime"), 2)
        self.assertEqual(sentinel.status()["output_prime"], "ready")
        sentinel.shutdown()
        await asyncio.wait_for(task, 2)
        self.assertEqual([e[0] for e in EVENTS][-1:], ["mixer_stop"])  # idle stream closed on shutdown

    async def test_prime_failure_is_visible_and_does_not_block_activation(self):
        sentinel = self.sentinel()

        class BrokenPrime(FakeMixer):
            def prime(self):
                raise OSError("device busy")

        sentinel._mixer_factory = BrokenPrime
        with self.assertLogs("jarvis.daemon", "WARNING") as logs:
            task = asyncio.create_task(sentinel.run())
            await self.wait_for(lambda: sentinel._output_status == "unavailable")
            sentinel.request_activation("hotkey")
            await self.wait_for(lambda: sentinel.state == "active")
        self.assertTrue(any("device busy" in line for line in logs.output))
        self.assertIn("mixer_stop", [e[0] for e in EVENTS])  # partial stream discarded
        self.assertIn("chime", [e[0] for e in EVENTS])  # activation continues
        sentinel.shutdown()
        await asyncio.wait_for(task, 2)

    async def test_activation_during_prime_waits_for_the_single_open_then_chimes(self):
        import threading

        release = threading.Event()

        class SlowPrime(FakeMixer):
            def prime(self):
                EVENTS.append(("prime_start", 0))
                release.wait(2)
                EVENTS.append(("prime", 0))

        sentinel = self.sentinel()
        sentinel._mixer_factory = SlowPrime
        task = asyncio.create_task(sentinel.run())
        await self.wait_for(lambda: sentinel.state == "sentinel")
        self.assertEqual(sentinel._output_status, "warming")
        sentinel.request_activation("hotkey")
        await self.wait_for(lambda: sentinel.state == "starting")
        await asyncio.sleep(0.1)
        self.assertNotIn("chime", [e[0] for e in EVENTS])  # not hot until priming has completed
        release.set()
        await self.wait_for(lambda: sentinel.state == "active")
        names = [e[0] for e in EVENTS]
        self.assertEqual(names.count("prime_start"), 1)
        self.assertLess(names.index("prime"), names.index("chime"))
        sentinel.shutdown()
        await asyncio.wait_for(task, 2)

    async def test_shutdown_while_priming_waits_then_closes_the_stream(self):
        import threading

        release = threading.Event()

        class SlowPrime(FakeMixer):
            def prime(self):
                release.wait(2)
                EVENTS.append(("prime", 0))

        sentinel = self.sentinel()
        sentinel._mixer_factory = SlowPrime
        task = asyncio.create_task(sentinel.run())
        await self.wait_for(lambda: sentinel.state == "sentinel")
        sentinel.shutdown()
        await asyncio.sleep(0.1)
        self.assertFalse(task.done())  # cannot abort an in-flight PortAudio open
        release.set()
        await asyncio.wait_for(task, 2)
        self.assertEqual([e[0] for e in EVENTS][-2:], ["prime", "mixer_stop"])

    async def test_chime_does_not_wait_for_a_slow_service_build(self):
        class SlowRuntime(VoiceRuntime):
            def __init__(self, config, voice_clone=None):
                time.sleep(0.4)
                super().__init__(config, voice_clone)

        sentinel = self.sentinel()
        sentinel._runtime_factory = SlowRuntime
        task = asyncio.create_task(sentinel.run())
        await self.wait_for(lambda: sentinel._output_status == "ready")
        sentinel.request_activation("hotkey")
        await self.wait_for(lambda: sentinel.state == "active", timeout=3)
        names = [e[0] for e in EVENTS]
        self.assertLess(names.index("chime"), names.index("build"))
        self.assertLess(names.index("build"), names.index("welcome"))  # health-gated welcome after services
        sentinel.shutdown()
        await asyncio.wait_for(task, 3)


def synthetic_claps(times, total, amplitude=0.5, rate=16000):
    """Quiet noise floor with clap-like decaying bursts at *times* (seconds)."""
    import numpy as np

    rng = np.random.default_rng(11)
    signal = (rng.standard_normal(int(rate * total)) * 0.001).astype(np.float32)
    t = np.arange(int(rate * 0.15)) / rate
    for at in times:
        burst = np.diff(rng.standard_normal(t.size) * np.exp(-t / 0.012), prepend=0.0)
        burst = (amplitude * burst / np.abs(burst).max()).astype(np.float32)
        start = int(at * rate)
        signal[start:start + burst.size] += burst[: signal.size - start]
    return signal


class StartRuntime(VoiceRuntime):
    live = 0
    peak = 0

    async def start(self):
        EVENTS.append(("start", time.perf_counter()))

    async def run_until_stopped(self):
        type(self).live += 1
        type(self).peak = max(type(self).peak, type(self).live)
        try:
            await super().run_until_stopped()
        finally:
            type(self).live -= 1


class DegradedRuntime(StartRuntime):
    def __init__(self, config, voice_clone=None):
        super().__init__(config, voice_clone)
        self.supervisor = SimpleNamespace(health_snapshot=lambda: [
            HealthReport("configuration", HealthStatus.HEALTHY),
            HealthReport("STT", HealthStatus.DEGRADED, "model missing"),
        ])


class SessionLifecycleTests(SentinelCase):
    def setUp(self):
        super().setUp()
        StartRuntime.live = StartRuntime.peak = 0

    async def test_control_activation_runs_the_full_loop_one_session_at_a_time(self):
        sentinel = self.sentinel(StartRuntime)
        port = free_port()
        server = await daemon.serve(sentinel, port)
        runner = asyncio.create_task(sentinel.run())
        try:
            await self.wait_for(lambda: sentinel.state == "sentinel")
            self.assertEqual((await asyncio.to_thread(daemon.send_command, "activate", port))["ok"], True)
            await self.wait_for(lambda: sentinel.state == "active")
            names = [e[0] for e in EVENTS]
            # chime first, then service init, then the welcome, then the voice loop
            self.assertEqual([n for n in names if n in ("chime", "start", "welcome", "listening")], ["chime", "start", "welcome", "listening"])
            status = await asyncio.to_thread(daemon.send_command, "status", port)
            self.assertEqual((status["state"], status["last_report"]["phase"]), ("active", "ready"))
            self.assertEqual(status["last_report"]["issues"], [])
            self.assertNotIn("limitaciones", sentinel.runtime.spoken[0])
            for _ in range(3):  # repeated activations while awake never start a second session
                await asyncio.to_thread(daemon.send_command, "activate", port)
            await asyncio.sleep(0.1)
            self.assertEqual(([e[0] for e in EVENTS].count("chime"), StartRuntime.peak), (1, 1))
            await asyncio.to_thread(daemon.send_command, "sleep", port)
            await self.wait_for(lambda: sentinel.state == "sentinel")
            await asyncio.sleep(0.1)
            self.assertEqual(sentinel.state, "sentinel")  # the queued presses were dropped, not replayed
            self.assertEqual([e[0] for e in EVENTS].count("runtime_stop"), 1)
            await asyncio.to_thread(daemon.send_command, "activate", port)
            await self.wait_for(lambda: sentinel.state == "active")
            self.assertEqual(([e[0] for e in EVENTS].count("chime"), [e[0] for e in EVENTS].count("start"), StartRuntime.peak), (2, 2, 1))
            await asyncio.to_thread(daemon.send_command, "quit", port)
            await asyncio.wait_for(runner, 3)
            self.assertEqual([e[0] for e in EVENTS].count("runtime_stop"), 2)  # every session runtime was stopped
            self.assertEqual([e[0] for e in EVENTS][-1], "mixer_stop")
            self.assertEqual(StartRuntime.live, 0)
        finally:
            sentinel.shutdown()
            server.close()
            await server.wait_closed()
            if not runner.done():
                await asyncio.wait_for(runner, 3)
        # owned cleanup: the port is free again for the next daemon
        again = await daemon.serve(sentinel, port)
        again.close()
        await again.wait_closed()

    async def test_second_daemon_is_rejected_and_the_first_keeps_serving(self):
        first, second = self.sentinel(StartRuntime), self.sentinel(StartRuntime)
        port = free_port()
        server = await daemon.serve(first, port)
        try:
            with self.assertRaises(OSError):
                await daemon.serve(second, port)
            with mock.patch.object(daemon, "Sentinel", return_value=second):
                self.assertEqual(await daemon.run_daemon(SimpleNamespace(daemon={"control_port": port})), 3)
            self.assertEqual((await asyncio.to_thread(daemon.send_command, "status", port))["state"], "idle")
        finally:
            server.close()
            await server.wait_closed()

    async def test_degraded_health_is_spoken_and_reported_not_claimed_operational(self):
        from jarvis.observability.event_hub import hub

        events = []
        unsubscribe = hub.subscribe(events.append)
        sentinel = self.sentinel(DegradedRuntime)
        task = asyncio.create_task(sentinel.run())
        try:
            await self.wait_for(lambda: sentinel.state == "sentinel")
            with self.assertLogs("jarvis.daemon", "WARNING") as logs:
                sentinel.request_activation("hotkey")
                await self.wait_for(lambda: sentinel.state == "active")
            self.assertTrue(any("startup degraded" in line for line in logs.output))
            report = sentinel.last_report
            self.assertEqual(report["phase"], "degraded")
            self.assertTrue(report["issues"])
            self.assertIn("limitaciones", sentinel.runtime.spoken[0])
            self.assertIn("startup.degraded", [e["name"] for e in events])
        finally:
            unsubscribe()
            sentinel.shutdown()
            await asyncio.wait_for(task, 3)

    async def test_voice_loop_failure_returns_to_sentinel_and_the_next_activation_works(self):
        crashes = []

        class Crash(StartRuntime):
            async def run_until_stopped(self):
                if not crashes:
                    crashes.append(1)
                    raise RuntimeError("voice loop crashed")
                await super().run_until_stopped()

        sentinel = self.sentinel(Crash)
        task = asyncio.create_task(sentinel.run())
        try:
            await self.wait_for(lambda: sentinel.state == "sentinel")
            with self.assertLogs("jarvis.daemon", "ERROR"):
                sentinel.request_activation("hotkey")
                await self.wait_for(lambda: [e[0] for e in EVENTS].count("runtime_stop") == 1)
                await self.wait_for(lambda: sentinel.state == "sentinel")
            sentinel.request_activation("hotkey")
            await self.wait_for(lambda: sentinel.state == "active")
            self.assertFalse(task.done())
        finally:
            sentinel.shutdown()
            await asyncio.wait_for(task, 3)

    async def test_quit_with_a_pending_first_clap_cancels_the_speculation(self):
        sentinel = self.sentinel(StartRuntime)
        task = asyncio.create_task(sentinel.run())
        await self.wait_for(lambda: sentinel.state == "sentinel")
        sentinel.detector.on_candidate(SimpleNamespace(time=1.0))
        await self.wait_for(lambda: sentinel.state == "candidate")
        sentinel.shutdown()
        await asyncio.wait_for(task, 3)
        self.assertNotIn("chime", [e[0] for e in EVENTS])  # a lone clap never reaches the startup sequence
        self.assertEqual(sentinel.voice.holders, frozenset())
        self.assertEqual([e[0] for e in EVENTS][-1], "mixer_stop")

    async def test_stop_while_startup_is_waiting_on_the_welcome_releases_everything(self):
        async def hung(self_runtime, text):
            await asyncio.sleep(30)

        sentinel = self.sentinel(StartRuntime)
        with mock.patch.object(VoiceRuntime, "_speak", hung):
            task = asyncio.create_task(sentinel.run())
            await self.wait_for(lambda: sentinel.state == "sentinel")
            sentinel.request_activation("control")
            await self.wait_for(lambda: sentinel.state == "starting")
            await self.wait_for(lambda: "start" in [e[0] for e in EVENTS])
            sentinel.shutdown()  # daemon quits mid-welcome
            await asyncio.wait_for(task, 3)
        self.assertIn("runtime_stop", [e[0] for e in EVENTS])
        self.assertEqual([e[0] for e in EVENTS][-1], "mixer_stop")
        self.assertEqual(sentinel.voice.holders, frozenset())


class ClapHandoff:
    async def feed(self, sentinel, times):
        signal = synthetic_claps([6.0 + t for t in times], 9.5)  # 6 s of quiet outlasts the post-resume cooldown
        for start in range(0, signal.size, daemon._BLOCK):
            sentinel._on_audio(signal[start:start + daemon._BLOCK])
            if start % 3200 == 0:
                await asyncio.sleep(0)

    async def run_with(self, times):
        sentinel = self.sentinel()
        task = asyncio.create_task(sentinel.run())
        await self.wait_for(lambda: sentinel.state == "sentinel")
        await self.feed(sentinel, times)
        await asyncio.sleep(0.2)
        return sentinel, task

    async def finish(self, sentinel, task):
        sentinel.shutdown()
        await asyncio.wait_for(task, 3)


class TwoClapDefaultTests(ClapHandoff, SentinelCase):
    def test_default_tuning_is_two_claps_and_unchanged_by_the_daemon(self):
        from jarvis.adapters.audio.claps import ClapTuning

        self.assertEqual(ClapTuning().claps_required, 2)
        self.assertEqual(daemon.clap_tuning(self.config), ClapTuning())  # no calibration file, nothing pinned

    async def test_two_claps_activate_by_default(self):
        sentinel, task = await self.run_with([0.0, 0.45])
        self.assertEqual(sentinel.detector.tuning.claps_required, 2)
        self.assertEqual(sentinel.state, "active")
        self.assertGreaterEqual(sentinel._last_gesture["confidence"], 0.55)
        await self.finish(sentinel, task)


class ThreeClapHandoffTests(ClapHandoff, SentinelCase):
    claps = {"claps_required": 3}

    def test_three_claps_are_a_config_choice_that_leaves_other_defaults_alone(self):
        from dataclasses import replace

        from jarvis.adapters.audio.claps import ClapTuning

        self.assertEqual(daemon.clap_tuning(self.config), replace(ClapTuning(), claps_required=3))

    async def test_two_claps_do_not_activate_but_three_do(self):
        sentinel, task = await self.run_with([0.0, 0.45])
        self.assertNotEqual(sentinel.state, "active")
        self.assertNotIn("chime", [e[0] for e in EVENTS])
        await self.finish(sentinel, task)
        EVENTS.clear()
        sentinel, task = await self.run_with([0.0, 0.45, 0.9])
        self.assertEqual(sentinel.state, "active")
        self.assertEqual(sentinel.detector.tuning.claps_required, 3)
        await self.finish(sentinel, task)

    def test_unsupported_claps_required_is_rejected_at_config_load(self):
        root = Path(self.tmp.name)
        (root / "bad.json").write_text(json.dumps({"runtime": {}, "claps": {"claps_required": 4}}))
        with self.assertRaises(ValueError):
            load_config(root / "bad.json")


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

    def test_cpu_only_fallback_is_preloaded_even_when_vram_is_low(self):
        from jarvis.adapters.models.ollama import OllamaProvider
        from jarvis.application import runtime as rt

        primary = OllamaProvider(base_url="http://127.0.0.1:1", model="big")
        cpu = OllamaProvider(base_url="http://127.0.0.1:1", model="small", extra_body={"options": {"num_gpu": 0}})
        gpu = OllamaProvider(base_url="http://127.0.0.1:1", model="mid", extra_body={"options": {"num_gpu": 20}})
        cloud = SimpleNamespace(name="cloud")
        warmed = []
        with mock.patch.object(OllamaProvider, "warm_up", autospec=True, side_effect=lambda self, *a, **k: warmed.append(self.model) or True):
            rt._warm_up(SimpleNamespace(providers=[cloud, cpu, gpu]), None, 5000, free_vram=lambda: 100)
        self.assertEqual(["small"], warmed)
        warmed.clear()
        with mock.patch.object(OllamaProvider, "warm_up", autospec=True, side_effect=lambda self, *a, **k: warmed.append(self.model) or True):
            rt._warm_up(SimpleNamespace(providers=[primary, cpu]), None, 5000, free_vram=lambda: 100)
        self.assertEqual(["small"], warmed)  # primary skipped by the VRAM gate, CPU fallback is not gated

    def test_failed_cpu_fallback_preload_only_logs_a_warning(self):
        from jarvis.adapters.models.ollama import OllamaProvider
        from jarvis.application import runtime as rt

        cpu = OllamaProvider(base_url="http://127.0.0.1:1", model="small", extra_body={"options": {"num_gpu": 0}})
        with mock.patch.object(OllamaProvider, "warm_up", return_value=False):
            with self.assertLogs("jarvis.runtime", level="WARNING") as logs:
                rt._warm_up(SimpleNamespace(providers=[SimpleNamespace(name="cloud"), cpu]), None)
        self.assertTrue(any("CPU-resident fallback preload failed" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
