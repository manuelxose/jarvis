"""`jarvis claps test --file`: replay a recording through the exact CLI path, without sounddevice."""

import importlib.util
import io
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np

from jarvis.apps.cli import main

_spec = importlib.util.spec_from_file_location("clap_eval", ROOT / "scripts" / "clap_eval.py")
signals = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(signals)


def write_wav(path, audio, rate=16000):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())


def parse_output(text):
    """Split the CLI stdout into (event lines, final JSON summary)."""
    marker = text.index("{\n")  # _emit writes the summary indented; events are single-line
    events = [json.loads(line) for line in text[:marker].splitlines() if line.startswith("{")]
    return events, json.loads(text[marker:])


class ClapsTestFileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        # clap_tuning reads the saved calibration: point it at an empty temp dir.
        patcher = mock.patch("jarvis.apps.daemon._data_dir", return_value=self.dir / "data")
        patcher.start()
        self.addCleanup(patcher.stop)
        # No microphone stack on the host: importing sounddevice must fail, --file must still work.
        modules = mock.patch.dict(sys.modules, {"sounddevice": None})
        modules.start()
        self.addCleanup(modules.stop)
        self.config = self.dir / "config.json"
        self.config.write_text(json.dumps({"runtime": {}}), encoding="utf-8")
        rng = np.random.default_rng(11)
        self.two = self.dir / "two_claps.wav"
        write_wav(self.two, signals.claps(rng, [0.8, 1.2], 3.0))
        self.speech = self.dir / "speech.wav"
        write_wav(self.speech, signals.speech_like(rng, 8.0))

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = main(["claps", "test", *argv, "--config", str(self.config)], stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_two_clap_recording_prints_one_gesture_with_latency(self):
        code, out, err = self.run_cli("--file", str(self.two))
        self.assertEqual((code, err), (0, ""))
        events, summary = parse_output(out)
        gestures = [e for e in events if "GESTURE" in e]
        self.assertEqual(len(gestures), 1)
        self.assertLess(gestures[0]["latency_ms"], 300)
        self.assertEqual(summary["gestures"], 1)
        self.assertEqual(summary["claps"], 2)
        self.assertEqual(sum('"GESTURE"' in line for line in out.splitlines()), 1)

    def test_speech_like_recording_has_no_gesture(self):
        code, out, _ = self.run_cli("--file", str(self.speech))
        self.assertEqual(code, 0)
        events, summary = parse_output(out)
        self.assertEqual(summary["gestures"], 0)
        self.assertFalse([e for e in events if "GESTURE" in e])

    def test_config_claps_required_is_used_for_replay(self):
        self.config.write_text(json.dumps({"runtime": {}, "claps": {"claps_required": 3}}), encoding="utf-8")
        _, out, _ = self.run_cli("--file", str(self.two))
        self.assertEqual(parse_output(out)[1]["gestures"], 0)

    def test_missing_file_is_a_clean_error(self):
        code, out, err = self.run_cli("--file", str(self.dir / "absent.wav"))
        self.assertEqual(code, 1)
        self.assertIn("error: cannot read", err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn("gestures", out)

    def test_corrupt_file_is_a_clean_error(self):
        garbage = self.dir / "garbage.wav"
        garbage.write_bytes(b"not a wav")
        code, _, err = self.run_cli("--file", str(garbage))
        self.assertEqual(code, 1)
        self.assertIn("error: cannot read", err)

    def test_empty_recording_has_no_gesture(self):
        empty = self.dir / "empty.wav"
        write_wav(empty, np.zeros(0, dtype=np.float32))
        code, out, _ = self.run_cli("--file", str(empty))
        self.assertEqual(code, 0)
        self.assertEqual(parse_output(out)[1]["gestures"], 0)

    def test_live_mode_without_sounddevice_keeps_import_error(self):
        with self.assertRaises(ImportError):
            self.run_cli("--seconds", "1")


if __name__ == "__main__":
    unittest.main()
