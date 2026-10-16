import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.observability.core_signals import CoreSignals  # noqa: E402
from jarvis.observability.event_hub import EventHub  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "ui" / "node-tests" / "fixtures" / "core-signals.json"


def record() -> tuple[list[dict], CoreSignals]:
    clock = [100.0]
    hub = EventHub()
    events: list[dict] = []
    hub.subscribe(events.append)
    cs = CoreSignals(hub=hub, clock=lambda: clock[0], session="fixture-session")

    def step(seconds: float = 0.1) -> None:
        clock[0] += seconds

    cs.publish_snapshot()
    cs.capture(True)
    step()
    cs.microphone(0.3, 0.4, 5)
    step()
    cs.capture(False)
    cs.turn_start("t1")
    for level, peak in ((0.2, 0.3), (0.5, 0.6), (0.4, 0.5)):
        step()
        cs.output("t1", level, peak, 8, latency_ms=12)
    cs.playback_end("t1", "ended")
    cs.turn_end("t1", True)
    cs.turn_start("t2")
    cs.output("t2", 0.5, 0.6, 8)
    cs.playback_end("t2", "cancelled")
    cs.cancel_turn("t2")
    late = cs.output("t2", 0.5, 0.6, 8)
    assert late is False and cs.dropped_late == 1
    cs.output("welcome", 0.4, 0.6, 5)
    cs.playback_end("welcome", "ended")
    cs.publish_snapshot()
    stripped = [{k: v for k, v in e.items() if k not in ("ts", "mono_ms")} for e in events]
    return stripped, cs


class CoreSignalFixtureTests(unittest.TestCase):
    def test_wire_script_matches_committed_fixture(self) -> None:
        events, _ = record()
        if os.environ.get("JARVIS_WRITE_FIXTURE") == "1":
            FIXTURE.parent.mkdir(parents=True, exist_ok=True)
            FIXTURE.write_text(json.dumps(events, indent=2) + "\n", encoding="utf-8")
        self.assertEqual(events, json.loads(FIXTURE.read_text(encoding="utf-8")))

    def test_script_shape(self) -> None:
        events, _ = record()
        kinds = [e.get("kind") or e["name"] for e in events]
        self.assertEqual(kinds[0], "core.snapshot")
        self.assertEqual(kinds[-1], "core.snapshot")
        self.assertEqual(kinds.count("output"), 5)
        self.assertEqual(kinds.count("playback-start"), 3)
        signals = [e["seq"] for e in events if e["name"] == "core.signal"]
        self.assertEqual(signals, list(range(1, len(signals) + 1)))


if __name__ == "__main__":
    unittest.main()
