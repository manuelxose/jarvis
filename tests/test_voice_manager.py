"""Shared voice model lifecycle: speculative, warm, cooldown, eviction, policies."""

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.application.voice_manager import VoiceModelManager, VoicePolicy, VoiceState
from jarvis.observability.event_hub import hub


class FakeClone:
    instances = 0

    def __init__(self):
        FakeClone.instances += 1
        self.starts = 0
        self.stops = 0
        self.running = False
        self.busy = False

    async def start(self):
        if not self.running:
            self.starts += 1
            self.running = True

    async def stop(self):
        self.stops += 1
        self.running = False

    async def wait_ready(self):
        return {"load_ms": 5}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def manager(free=8000.0, processes=(), clock=None, **policy):
    FakeClone.instances = 0
    return VoiceModelManager(
        FakeClone,
        VoicePolicy(**policy),
        free_vram=lambda: free,
        running_processes=lambda: set(processes),
        clock=clock or Clock(),
    )


class VoiceManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.unsubscribe = hub.subscribe(lambda e: self.events.append(e["name"]))

    async def asyncTearDown(self):
        self.unsubscribe()

    async def test_first_clap_speculates_second_clap_keeps_it(self):
        m = manager()
        self.assertTrue(await m.speculate("first clap"))
        self.assertEqual((m.state, m.tts.starts), (VoiceState.SPECULATIVE, 1))
        await m.acquire("session")
        self.assertEqual((m.state, m.tts.starts), (VoiceState.WARM, 1))  # no second load
        await m.cancel_speculation("late expiry")  # held: must not evict
        self.assertEqual(m.tts.stops, 0)
        await asyncio.sleep(0)
        self.assertIn("voice.loading", self.events)

    async def test_false_activation_releases_the_speculative_load(self):
        m = manager()
        await m.speculate("first clap")
        await m.cancel_speculation("timeout")
        self.assertEqual((m.state, m.tts.stops), (VoiceState.COLD, 1))
        self.assertIn("voice.evicted", self.events)

    async def test_speculation_never_unloads_a_model_it_did_not_load(self):
        m = manager()
        await m.acquire("s1")
        await m.release("s1")  # cooldown, still loaded
        self.assertTrue(await m.speculate("first clap"))  # reuse, no new load
        await m.cancel_speculation("timeout")
        self.assertEqual((m.state, m.tts.stops, m.tts.starts), (VoiceState.COOLDOWN, 0, 1))

    async def test_cooldown_reuse_then_eviction(self):
        m = manager(cooldown_seconds=0.05)
        await m.acquire("s1")
        await m.release("s1")
        self.assertEqual(m.state, VoiceState.COOLDOWN)
        await m.acquire("s2")  # reused warm, eviction timer cancelled
        self.assertEqual((m.tts.starts, m.state), (1, VoiceState.WARM))
        await asyncio.sleep(0.1)
        self.assertEqual(m.tts.stops, 0)
        await m.release("s2")
        await asyncio.sleep(0.1)
        self.assertEqual((m.state, m.tts.stops), (VoiceState.COLD, 1))
        self.assertEqual(FakeClone.instances, 1)  # one shared model object, ever

    async def test_no_eviction_during_synthesis(self):
        m = manager(cooldown_seconds=0.01)
        await m.acquire("s1")
        m.tts.busy = True
        await m.release("s1")
        await asyncio.sleep(0.05)
        self.assertEqual(m.tts.stops, 0)
        m.tts.busy = False
        await asyncio.sleep(2.1)  # deferred eviction retries
        self.assertEqual(m.tts.stops, 1)

    async def test_speculation_is_rate_limited(self):
        clock = Clock()
        m = manager(clock=clock, speculative_min_interval_seconds=20)
        await m.speculate("clap")
        await m.cancel_speculation("timeout")
        clock.t += 5
        self.assertFalse(await m.speculate("stray clap"))
        clock.t += 20
        self.assertTrue(await m.speculate("clap"))

    async def test_gpu_budget_and_busy_apps_block_loading(self):
        low = manager(free=2000.0)
        self.assertFalse(await low.speculate("clap"))
        await low.acquire("s")  # session still runs, on the fallback voice
        self.assertEqual(low.tts.starts, 0)
        gaming = manager(processes={"cyberpunk2077.exe"}, gpu_busy_processes=("cyberpunk2077.exe",))
        self.assertFalse(await gaming.speculate("clap"))

    async def test_pressure_evicts_only_idle_model(self):
        m = manager()
        await m.acquire("s")
        m._free_vram = lambda: 100.0
        await m.check_pressure()
        self.assertEqual(m.tts.stops, 0)  # held: never
        await m.release("s")
        await m.check_pressure()
        self.assertEqual((m.state, m.tts.stops), (VoiceState.COLD, 1))

    async def test_modes(self):
        always = manager(preload="always")
        await always.start_always()
        self.assertEqual((always.state, always.tts.starts), (VoiceState.COOLDOWN, 1))
        await always.acquire("s")
        await always.release("s")
        always._free_vram = lambda: 1.0
        await always.check_pressure()
        self.assertEqual(always.tts.stops, 0)
        on_demand = manager(preload="on_demand")
        self.assertFalse(await on_demand.speculate("clap"))
        await on_demand.acquire("s")
        await on_demand.release("s")
        self.assertEqual((on_demand.state, on_demand.tts.stops), (VoiceState.COLD, 1))

    async def test_policy_validation(self):
        with self.assertRaises(ValueError):
            VoicePolicy.from_config({"preload": "sometimes"})
        with self.assertRaises(TypeError):
            VoicePolicy.from_config({"unknown": 1})
        self.assertEqual(VoicePolicy.from_config({"gpu_busy_processes": ["Game.EXE"]}).gpu_busy_processes, ("game.exe",))

    async def test_no_clone_configured(self):
        m = VoiceModelManager(lambda: None)
        self.assertFalse(await m.speculate("clap"))
        await m.acquire("s")
        await m.release("s")
        self.assertEqual(m.state, VoiceState.COLD)


class EventHubTests(unittest.TestCase):
    def test_schema_validation_drops_bad_events_without_raising(self):
        seen = []
        unsubscribe = hub.subscribe(seen.append)
        try:
            self.assertIsNotNone(hub.publish("voice.evicted", reason="x"))
            self.assertIsNone(hub.publish("voice.evicted"))  # missing field
            self.assertIsNone(hub.publish("voice.ready", load_ms="slow"))  # wrong type
            self.assertIsNone(hub.publish("made.up", x=1))
            self.assertIsNone(hub.publish("speech.completed", trace_id="t", cancelled=1))
        finally:
            unsubscribe()
        self.assertEqual([e["name"] for e in seen], ["voice.evicted"])
        self.assertEqual(seen[0]["v"], 1)

    def test_subscriber_errors_are_isolated(self):
        def boom(event):
            raise RuntimeError("observer bug")

        unsubscribe = hub.subscribe(boom)
        try:
            self.assertIsNotNone(hub.publish("activation.cancelled", reason="timeout"))
        finally:
            unsubscribe()


if __name__ == "__main__":
    unittest.main()
