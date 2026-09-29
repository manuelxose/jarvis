import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH_PATH = ROOT / "docs" / "bench" / "tts-clone-spike.json"


class TtsBenchEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.raw = BENCH_PATH.read_text(encoding="utf-8")
        self.data = json.loads(self.raw)

    def test_model_and_gpu(self):
        self.assertEqual("Qwen/Qwen3-TTS-12Hz-0.6B-Base", self.data["model"])
        self.assertIn("3070", self.data["gpu"])

    def test_at_least_twenty_trials(self):
        self.assertGreaterEqual(len(self.data["trials"]), 20)

    def test_ttfa_percentiles_are_numeric_and_ordered(self):
        ttfa = self.data["ttfa_ms"]
        for key in ("p50", "p95", "min", "max"):
            self.assertIsInstance(ttfa[key], (int, float))
        self.assertLessEqual(ttfa["p50"], ttfa["p95"])

    def test_rtf_p50_is_positive(self):
        self.assertGreater(self.data["rtf"]["p50"], 0)

    def test_vram_key_present(self):
        vram_keys = {"vram_free_mb", "vram_total_mb", "vram_peak_alloc_mb", "vram_reserved_mb"}
        self.assertTrue(vram_keys & self.data.keys())

    def test_no_audio_or_personal_path_leakage(self):
        self.assertNotIn("pcm", self.data)
        self.assertNotIn("base64", self.raw)
        self.assertNotIn("C:\\Users", self.raw)
        self.assertNotIn(".wav", self.raw)


if __name__ == "__main__":
    unittest.main()
