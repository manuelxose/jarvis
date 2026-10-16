import array
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.apps.daemon import _skip_high_rate  # noqa: E402
from jarvis.observability.core_signals import MAX_FENCES, CoreSignals, pcm_envelope  # noqa: E402
from jarvis.observability.event_hub import EventHub, validate  # noqa: E402


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 100.0
        self.hub = EventHub()
        self.events: list[dict] = []
        self.hub.subscribe(self.events.append)
        self.cs = CoreSignals(hub=self.hub, clock=lambda: self.now, session="s1")

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.events if e["name"] == "core.signal"]


class CoreSignalsTests(Fixture):
    def test_seq_strictly_increasing_across_threads_in_delivery_order(self) -> None:
        def work() -> None:
            for i in range(200):
                self.cs.capture(i % 2 == 0)

        threads = [threading.Thread(target=work) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        seqs = [e["seq"] for e in self.events]
        self.assertEqual(seqs, list(range(1, 401)))

    def test_implicit_turn_for_out_of_turn_playback(self) -> None:
        self.assertTrue(self.cs.output("welcome", 0.4, 0.6, 5))
        self.assertEqual(self.kinds(), ["turn-start", "playback-start", "output"])
        self.assertTrue(self.cs.playback_end("welcome", "ended"))
        self.assertEqual(self.kinds()[-2:], ["playback-end", "turn-end"])

    def test_no_implicit_turn_end_for_producer_turn(self) -> None:
        self.cs.turn_start("t1")
        self.cs.output("t1", 0.2, 0.3)
        self.cs.playback_end("t1", "ended")
        self.assertEqual(self.kinds(), ["turn-start", "playback-start", "output", "playback-end"])

    def test_playback_start_once_with_confirmed_and_latency(self) -> None:
        self.cs.turn_start("t1")
        for _ in range(3):
            self.cs.output("t1", 0.2, 0.3, latency_ms=12)
        starts = [e for e in self.events if e.get("kind") == "playback-start"]
        self.assertEqual(len(starts), 1)
        self.assertTrue(starts[0]["confirmed"])
        self.assertEqual(starts[0]["playback"], "t1:1")
        self.assertEqual(starts[0]["latency_ms"], 12)

    def test_late_output_after_end_and_cancel_dropped(self) -> None:
        self.cs.turn_start("t1")
        self.cs.output("t1", 0.2, 0.3)
        self.cs.playback_end("t1", "cancelled")
        count = len(self.events)
        self.assertFalse(self.cs.output("t1", 0.9, 0.9))
        self.assertEqual(len(self.events), count)
        self.assertEqual(self.cs.dropped_late, 1)
        self.cs.turn_start("t2")
        self.cs.output("t2", 0.2, 0.3)
        self.cs.cancel_turn("t2")
        self.assertFalse(self.cs.output("t2", 0.9, 0.9))
        self.assertEqual(self.cs.dropped_late, 2)
        self.assertIn("t2:1", self.cs.snapshot()["fences"]["playbacks"])

    def test_playback_end_without_open_playback_is_noop(self) -> None:
        self.assertFalse(self.cs.playback_end("t1", "ended"))
        self.assertFalse(self.cs.playback_end("t1", "bogus"))
        self.assertEqual(self.events, [])

    def test_microphone_throttle_with_fake_clock(self) -> None:
        self.assertTrue(self.cs.microphone(0.1, 0.2))
        self.now += 0.01
        self.assertFalse(self.cs.microphone(0.1, 0.2))
        self.assertEqual(self.cs.throttled, 1)
        self.now += 0.05
        self.assertTrue(self.cs.microphone(0.1, 0.2))
        self.assertEqual(self.kinds().count("microphone"), 2)

    def test_level_clamp_and_non_finite_rejected(self) -> None:
        self.cs.microphone(5.0, -3.0)
        event = self.events[-1]
        self.assertEqual((event["level"], event["peak"]), (1.0, 0.0))
        count = len(self.events)
        for bad in (float("nan"), float("inf"), "x", None):
            self.now += 1
            self.assertFalse(self.cs.microphone(bad, 0.1))
            self.assertFalse(self.cs.output("t", bad, 0.1))
        self.assertEqual(len(self.events), count)
        self.assertEqual(self.cs.rejected, 8)

    def test_snapshot_shape_and_watermark(self) -> None:
        self.cs.turn_start("t1")
        self.cs.output("t1", 0.5, 0.7, age_ms=10)
        self.now += 0.5
        snap = self.cs.snapshot()
        self.assertEqual(snap["watermark"], 3)
        self.assertEqual(self.cs.snapshot()["watermark"], 3)  # snapshots consume no seq
        self.assertEqual(snap["session"], "s1")
        self.assertIsNone(snap["replaces_session"])
        self.assertEqual(snap["facts"]["playback"], {"id": "t1:1", "turn": "t1", "confirmed": True})
        self.assertEqual(snap["facts"]["turn"], "t1")
        self.assertFalse(snap["facts"]["thinking"])
        self.assertAlmostEqual(snap["output"]["age_ms"], 510.0)
        self.assertIsNone(snap["microphone"])
        for key in ("cancelled", "stopped", "cleaned_up", "turn_ended", "overflow", "playbacks", "commands", "candidates"):
            self.assertIn(key, snap["fences"])
        self.assertEqual(snap["candidate"], None)

    def test_fence_overflow_sets_flag_without_evicting(self) -> None:
        for i in range(MAX_FENCES + 2):
            self.now += 1
            self.cs.turn_start(f"t{i}")
            self.cs.output(f"t{i}", 0.1, 0.1)
            self.cs.playback_end(f"t{i}", "ended")
        fences = self.cs.snapshot()["fences"]
        self.assertTrue(fences["overflow"])
        self.assertEqual(len(fences["playbacks"]), MAX_FENCES)
        self.assertEqual(fences["playbacks"][0], "t0:1")

    def test_close_makes_everything_noop(self) -> None:
        self.cs.close()
        count = len(self.events)
        self.assertFalse(self.cs.turn_start("t"))
        self.assertFalse(self.cs.output("t", 0.1, 0.1))
        self.assertFalse(self.cs.microphone(0.1, 0.1))
        self.assertFalse(self.cs.capture(True))
        self.assertFalse(self.cs.publish_snapshot())
        self.assertEqual(len(self.events), count)

    def test_emitted_events_validate_and_snapshot_publishes(self) -> None:
        self.cs.turn_start("t1")
        self.cs.output("t1", 0.5, 0.5)
        self.cs.microphone(0.1, 0.1)
        self.cs.capture(True)
        self.cs.turn_end("t1", True)
        self.assertTrue(self.cs.publish_snapshot())
        self.assertEqual(self.events[-1]["name"], "core.snapshot")
        for event in self.events:
            payload = {k: v for k, v in event.items() if k not in ("v", "name", "ts", "mono_ms")}
            self.assertEqual(validate(event["name"], payload), "")
        self.assertEqual(validate("core.signal", {"session": "s", "generation": 1, "seq": "1", "age_ms": 0, "kind": "x"}) != "", True)

    def test_daemon_log_filter_skips_high_rate(self) -> None:
        written: list[dict] = []
        write = _skip_high_rate(written.append)
        self.hub.subscribe(write)
        self.cs.turn_start("t1")
        self.cs.output("t1", 0.5, 0.5)
        self.cs.microphone(0.1, 0.1)
        self.cs.publish_snapshot()
        self.assertEqual([e["kind"] for e in written if e["name"] == "core.signal"], ["turn-start", "playback-start"])
        self.assertFalse([e for e in written if e["name"] == "core.snapshot"])

    def test_advance_generation_fences_old_playback_and_carries_higher_generation(self) -> None:
        self.cs.output("t1", 0.5, 0.5)
        self.assertEqual(self.cs.advance_generation(), 2)
        self.assertFalse(self.cs.output("t1", 0.5, 0.5))  # late audio from the old generation
        self.assertEqual(self.cs.snapshot()["facts"]["playback"], None)
        self.assertIsNone(self.cs.snapshot()["output"])
        self.cs.turn_start("t2")
        self.assertEqual(self.events[-1]["generation"], 2)
        self.assertEqual(self.cs.snapshot()["generation"], 2)


class PcmEnvelopeTests(unittest.TestCase):
    def test_silence_full_scale_empty(self) -> None:
        self.assertEqual(pcm_envelope(b""), (0.0, 0.0))
        self.assertEqual(pcm_envelope(b"\x00" * 1280), (0.0, 0.0))
        loud = array.array("h", [32767, -32768] * 100)
        rms, peak = pcm_envelope(loud.tobytes())
        self.assertGreater(rms, 0.99)
        self.assertEqual(peak, 1.0)

    def test_odd_trailing_byte_ignored(self) -> None:
        samples = array.array("h", [16384] * 10).tobytes() + b"\x01"
        rms, peak = pcm_envelope(samples)
        self.assertAlmostEqual(rms, 0.5)
        self.assertAlmostEqual(peak, 0.5)


class StartupLifecycleTests(Fixture):
    def snaps(self) -> list[dict]:
        return [e for e in self.events if e["name"] == "core.snapshot"]

    def test_candidate_clamp_and_ordering(self) -> None:
        self.assertTrue(self.cs.candidate("c1", True, 9000))
        self.assertTrue(self.cs.candidate("c1", False, -5))
        sig = [e for e in self.events if e["kind"] == "candidate"]
        self.assertEqual([e["active"] for e in sig], [True, False])
        self.assertEqual([e["remaining_ms"] for e in sig], [2000.0, 0.0])
        self.assertLess(sig[0]["seq"], sig[1]["seq"])

    def test_candidate_in_snapshot_expires(self) -> None:
        self.cs.candidate("c1", True, 1000)
        self.assertEqual(self.cs.snapshot()["candidate"], {"id": "c1", "remaining_ms": 1000.0})
        self.now += 2
        self.assertIsNone(self.cs.snapshot()["candidate"])

    def test_startup_sequence_partial_sticky_and_unknown_rejected(self) -> None:
        for phase in ("acknowledged", "initializing", "announcing"):
            self.assertTrue(self.cs.startup(phase))
        self.assertTrue(self.cs.startup("degraded", partial=True))
        self.assertTrue(self.cs.startup("ready"))
        self.assertFalse(self.cs.startup("bogus"))
        self.assertEqual(self.cs.rejected, 1)
        sig = [e for e in self.events if e["kind"] == "startup"]
        self.assertEqual([e["seq"] for e in sig], sorted(e["seq"] for e in sig))
        self.assertTrue(sig[-1]["partial"])
        self.assertTrue(self.cs.snapshot()["facts"]["partial"])

    def test_failed_startup_is_persistent_error(self) -> None:
        self.cs.startup("failed")
        self.assertTrue(self.cs.snapshot()["facts"]["persistent_error"])

    def test_cancelled_startup_fences_playback(self) -> None:
        self.cs.output("welcome", 0.4, 0.5)
        self.assertTrue(self.cs.startup("cancelled"))
        self.assertFalse(self.cs.output("welcome", 0.4, 0.5))
        self.assertEqual(self.cs.dropped_late, 1)
        snap = self.cs.snapshot()
        self.assertTrue(snap["fences"]["stopped"])
        self.assertFalse(snap["fences"]["cleaned_up"])
        self.assertIsNone(snap["output"])

    def test_stop_then_cleaned_up(self) -> None:
        self.cs.startup("initializing")
        self.assertFalse(self.cs.cleaned_up())
        self.assertTrue(self.cs.stop())
        self.assertEqual(self.kinds()[-1], "stop")
        self.assertTrue(self.cs.cleaned_up())
        snap = self.snaps()[-1]
        self.assertTrue(snap["fences"]["stopped"] and snap["fences"]["cleaned_up"])
        self.assertEqual(snap["facts"]["startup"], "idle")

    def test_cleaned_up_keeps_cancelled_and_ready(self) -> None:
        self.cs.startup("cancelled")
        self.cs.cleaned_up()
        self.assertEqual(self.snaps()[-1]["facts"]["startup"], "cancelled")

    def test_begin_activation_resets_and_publishes(self) -> None:
        self.cs.startup("degraded", partial=True)
        self.cs.apps([("a", "launched")])
        self.cs.stop()
        gen = self.cs.generation
        before = self.cs.snapshot()["watermark"]
        self.assertEqual(self.cs.begin_activation(), gen + 1)
        snap = self.snaps()[-1]
        self.assertGreater(snap["watermark"], before)
        self.assertEqual(snap["generation"], gen + 1)
        self.assertFalse(snap["fences"]["stopped"])
        self.assertEqual(snap["facts"]["startup"], "idle")
        self.assertFalse(snap["facts"]["partial"])
        self.assertIsNone(snap["facts"]["apps"])

    def test_apps_mapping_dedupe_cap(self) -> None:
        self.cs.apps([("a", "launched"), ("a", "failed"), ("b", "timeout"), ("c", "skipped"), ("d", "weird"), ("", "opened"), ("e", "pending"), ("f", "already_running")])
        apps = self.snaps()[-1]["facts"]["apps"]
        self.assertEqual(apps, [{"id": "a", "status": "ready"}, {"id": "b", "status": "failed"}, {"id": "e", "status": "pending"}, {"id": "f", "status": "ready"}])
        self.cs.apps((f"app{i}", "opened") for i in range(33))
        self.assertEqual(len(self.snaps()[-1]["facts"]["apps"]), 32)

    def test_session_state_survives_turn_generation_bump(self) -> None:
        self.cs.startup("ready", partial=True)
        self.cs.apps([("a", "launched")])
        self.cs.turn_start("t1")
        self.cs.turn_end("t1", True)
        self.cs.turn_start("t2")
        self.cs.advance_generation()
        facts = self.cs.snapshot()["facts"]
        self.assertEqual(facts["startup"], "ready")
        self.assertTrue(facts["partial"])
        self.assertEqual(facts["apps"], [{"id": "a", "status": "ready"}])

    def test_closed_returns_false_and_snapshots_validate(self) -> None:
        self.cs.candidate("c", True, 100)
        self.cs.startup("ready")
        self.cs.apps([("a", "opened")])
        self.cs.stop()
        self.cs.cleaned_up()
        self.cs.begin_activation()
        for e in self.events:
            self.assertEqual(validate(e["name"], {k: v for k, v in e.items() if k not in ("name", "ts", "id")}), "")
        self.cs.close()
        self.assertFalse(self.cs.candidate("c", True, 1))
        self.assertFalse(self.cs.startup("ready"))
        self.assertFalse(self.cs.stop())
        self.assertFalse(self.cs.cleaned_up())
        self.assertFalse(self.cs.apps([("a", "opened")]))


if __name__ == "__main__":
    unittest.main()
