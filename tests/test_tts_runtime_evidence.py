"""Locks the committed owner-profile speaker acceptance run (docs/bench/tts-clone-runtime.json)."""

import json
from pathlib import Path
import re
import unittest

EVIDENCE = Path(__file__).resolve().parents[1] / "docs" / "bench" / "tts-clone-runtime.json"


class TTSRuntimeEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.raw = EVIDENCE.read_text(encoding="utf-8")
        self.data = json.loads(self.raw)

    def test_owner_profile_run(self):
        meta = self.data["meta"]
        self.assertIn("%LOCALAPPDATA%", meta["command"])
        self.assertIn("--turns 20 --chunk-size 4", meta["command"])
        self.assertEqual(self.data["worker"]["profile"], "owner")
        self.assertGreaterEqual(self.data["turns"], 20)
        for key in ("gpu_temp_c_before", "gpu_temp_c_after"):
            self.assertIsInstance(meta[key], (int, float))

    def test_first_audio_timings(self):
        t = self.data["segment_to_first_audio_ms"]
        for key in ("p50", "p95", "max"):
            self.assertIsInstance(t[key], (int, float))
        self.assertLessEqual(t["p50"], t["p95"])
        self.assertLessEqual(t["p95"], t["max"])
        self.assertLess(t["p50"], 700)
        self.assertIsInstance(self.data["under_700ms"], int)
        self.assertIsInstance(self.data["underruns"], int)

    def test_cancel_and_warming(self):
        barge = self.data["barge_in"]
        self.assertIsInstance(barge["cancel_to_stop_ms"], (int, float))
        self.assertEqual(barge["stale_writes_after_cancel"], 0)
        self.assertEqual(self.data["warming_turn"]["served_by"], "sapi")

    def test_no_private_data_or_audio(self):
        self.assertNotRegex(self.raw, r"(?i)[a-z]:\\\\users|/home/|/mnt/|\\\\wsl")
        self.assertNotRegex(self.raw, r"(?i)pcm|base64|\.wav")
        self.assertIsNone(re.search(r"[A-Za-z0-9+/]{200,}", self.raw))


if __name__ == "__main__":
    unittest.main()
