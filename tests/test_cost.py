import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.observability.cost import CostTracker, ProviderRate, UsageRecord, estimate_cost


class EstimateCostTests(unittest.TestCase):
    def test_returns_none_without_a_rate(self):
        usage = UsageRecord(provider="unknown", kind="llm", input_tokens=1000)
        self.assertIsNone(estimate_cost(usage, None))

    def test_computes_llm_token_cost(self):
        rate = ProviderRate(input_token_per_million=1.0, output_token_per_million=2.0)
        usage = UsageRecord(
            provider="p", kind="llm", input_tokens=500_000, output_tokens=250_000
        )
        self.assertAlmostEqual(0.5 * 1.0 + 0.25 * 2.0, estimate_cost(usage, rate))

    def test_computes_tts_character_cost(self):
        rate = ProviderRate(character=0.00003)
        usage = UsageRecord(provider="p", kind="tts", characters=1000)
        self.assertAlmostEqual(0.03, estimate_cost(usage, rate))

    def test_computes_stt_audio_second_cost(self):
        rate = ProviderRate(audio_second=0.0001)
        usage = UsageRecord(provider="p", kind="stt", audio_seconds=60)
        self.assertAlmostEqual(0.006, estimate_cost(usage, rate))


class CostTrackerTests(unittest.TestCase):
    def test_record_returns_none_for_unpriced_provider(self):
        tracker = CostTracker(pricing={})
        cost = tracker.record(UsageRecord(provider="mystery", kind="llm", input_tokens=100))
        self.assertIsNone(cost)

    def test_record_returns_estimated_cost_for_priced_provider(self):
        tracker = CostTracker(pricing={"p": ProviderRate(character=0.0001)})
        cost = tracker.record(UsageRecord(provider="p", kind="tts", characters=100))
        self.assertAlmostEqual(0.01, cost)

    def test_summary_counts_interactions_and_priced_interactions_separately(self):
        tracker = CostTracker(pricing={"p": ProviderRate(character=0.0001)})
        tracker.record(UsageRecord(provider="p", kind="tts", characters=100))
        tracker.record(UsageRecord(provider="mystery", kind="tts", characters=100))
        summary = tracker.summary()
        self.assertEqual(2, summary["interactions"])
        self.assertEqual(1, summary["priced_interactions"])

    def test_summary_provider_distribution_sums_per_provider(self):
        tracker = CostTracker(
            pricing={
                "a": ProviderRate(character=0.0001),
                "b": ProviderRate(character=0.0002),
            }
        )
        tracker.record(UsageRecord(provider="a", kind="tts", characters=100))
        tracker.record(UsageRecord(provider="b", kind="tts", characters=100))
        summary = tracker.summary()
        self.assertAlmostEqual(0.01, summary["provider_distribution"]["a"])
        self.assertAlmostEqual(0.02, summary["provider_distribution"]["b"])

    def test_summary_of_empty_tracker_has_zeroed_fields(self):
        summary = CostTracker().summary()
        self.assertEqual(0, summary["interactions"])
        self.assertEqual(0.0, summary["total_cost_usd"])
        self.assertEqual(0.0, summary["avg_cost_per_interaction_usd"])
        self.assertEqual({}, summary["provider_distribution"])

    def test_avg_cost_per_minute_uses_recorded_timestamp_span(self):
        tracker = CostTracker(pricing={"p": ProviderRate(character=1.0)})
        tracker.record(UsageRecord(provider="p", kind="tts", characters=1, timestamp=0.0))
        tracker.record(UsageRecord(provider="p", kind="tts", characters=1, timestamp=60.0))
        summary = tracker.summary()
        # 2 USD total over exactly one minute span.
        self.assertAlmostEqual(2.0, summary["avg_cost_per_conversational_minute_usd"])

    def test_no_time_span_gives_zero_per_minute_rate(self):
        tracker = CostTracker(pricing={"p": ProviderRate(character=1.0)})
        tracker.record(UsageRecord(provider="p", kind="tts", characters=1, timestamp=0.0))
        summary = tracker.summary()
        self.assertEqual(0.0, summary["avg_cost_per_conversational_minute_usd"])


if __name__ == "__main__":
    unittest.main()
