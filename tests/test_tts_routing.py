"""Pure evidence-gated TTS ordering; no adapter or network calls."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.tts.fallback import order_tts_candidates
from jarvis.config import TTSRoutingEvidence
from jarvis.core.errors import ProviderConfigError


def row(provider, *, run_id="same-run", latency=100, cost=0.01,
        quality=0.5, reliability=0.5, integration=0.5):
    return TTSRoutingEvidence(provider=provider, run_id=run_id, source="operator",
                              first_audio_ms=latency, usd_per_character=cost,
                              quality=quality, reliability=reliability,
                              integration=integration)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.cloud = SimpleNamespace(name="alibaba_qwen")
        self.sapi = SimpleNamespace(name="sapi")
        self.candidates = [self.cloud, self.sapi]

    def test_profiles_use_only_measured_fields(self):
        rows = [row("alibaba_qwen", latency=50, cost=0.04, quality=0.9,
                    reliability=0.9, integration=0.9),
                row("sapi", latency=150, cost=0, quality=0.2,
                    reliability=0.2, integration=0.2)]
        for profile, expected in (("fast", self.cloud), ("cheap", self.sapi),
                                  ("quality", self.cloud), ("auto", self.cloud)):
            with self.subTest(profile=profile):
                ordered, reason = order_tts_candidates(self.candidates, profile, rows)
                self.assertIs(ordered[0], expected)
                self.assertEqual("measured evidence", reason)
                self.assertEqual(self.candidates, [self.cloud, self.sapi])

    def test_auto_weighted_cost_can_win(self):
        rows = [row("alibaba_qwen", latency=100, cost=1, quality=0, reliability=0, integration=0),
                row("sapi", latency=100, cost=0, quality=1, reliability=1, integration=1)]
        self.assertIs(self.sapi, order_tts_candidates(self.candidates, "auto", rows)[0][0])

    def test_stable_ties_and_singleton(self):
        rows = [row("alibaba_qwen"), row("sapi")]
        for profile in ("auto", "fast", "cheap", "quality"):
            with self.subTest(profile=profile):
                self.assertEqual(self.candidates, order_tts_candidates(self.candidates, profile, rows)[0])
                self.assertEqual([self.sapi], order_tts_candidates([self.sapi], profile)[0])

    def test_absent_incomplete_and_mismatched_evidence(self):
        for rows, reason in (((), "missing"),
                             ((row("alibaba_qwen"),), "missing"),
                             ((row("alibaba_qwen", quality=None), row("sapi")), "incomplete"),
                             ((row("alibaba_qwen"), row("sapi", run_id="other")), "mismatched")):
            with self.subTest(reason=reason):
                ordered, explanation = order_tts_candidates(self.candidates, "auto", rows)
                self.assertEqual(self.candidates, ordered)
                self.assertIn(reason, explanation)
                for profile in ("fast", "cheap", "quality"):
                    with self.assertRaises(ProviderConfigError):
                        order_tts_candidates(self.candidates, profile, rows)

    def test_unknown_unavailable_duplicate_and_invalid_profile(self):
        for profile, rows, message in (
            ("fast", (row("qwen_clone"),), "unavailable"),
            ("fast", (row("sapi"), row("sapi")), "duplicate"),
            ("turbo", (), "profile"),
        ):
            with self.subTest(profile=profile, message=message):
                with self.assertRaisesRegex(ProviderConfigError, message):
                    order_tts_candidates(self.candidates, profile, rows)
        ordered, reason = order_tts_candidates(self.candidates, "auto", (row("qwen_clone"),))
        self.assertEqual(self.candidates, ordered)
        self.assertIn("unavailable", reason)


if __name__ == "__main__":
    unittest.main()
