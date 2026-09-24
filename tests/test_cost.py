import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.observability.cost import (
    CostTracker,
    ProviderRate,
    TurnCost,
    UsageRecord,
    build_turn_cost,
    estimate_cost,
)


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


class TurnCostTests(unittest.TestCase):
    def test_total_usd_sums_only_priced_entries(self):
        cost = TurnCost(
            trace_id="t1",
            route="fast_model",
            entries=(
                (UsageRecord(provider="p", kind="llm", input_tokens=1), 0.5),
                (UsageRecord(provider="mystery", kind="tts", characters=1), None),
            ),
        )
        self.assertAlmostEqual(0.5, cost.total_usd)

    def test_unpriced_entry_gives_usd_none_in_to_dict(self):
        cost = TurnCost(
            trace_id="t1",
            route="fast_model",
            entries=((UsageRecord(provider="mystery", kind="llm", input_tokens=1), None),),
        )
        entry = cost.to_dict()["entries"][0]
        self.assertIsNone(entry["usd"])
        self.assertEqual(0.0, cost.to_dict()["total_usd"])

    def test_to_dict_has_only_plain_json_types_no_bytes_or_secrets(self):
        cost = TurnCost(
            trace_id="t1",
            route="fast_model",
            entries=(
                (
                    UsageRecord(
                        provider="p",
                        kind="llm",
                        model="m",
                        input_tokens=10,
                        output_tokens=5,
                        characters=0,
                        audio_seconds=0.0,
                    ),
                    0.001,
                ),
            ),
        )
        payload = cost.to_dict()
        import json

        encoded = json.dumps(payload)
        self.assertNotIn("api_key", encoded)
        self.assertNotIn("authorization", encoded.lower())

        def _walk(value):
            if isinstance(value, bytes):
                self.fail("to_dict() must never contain bytes")
            if isinstance(value, dict):
                for v in value.values():
                    _walk(v)
            elif isinstance(value, list):
                for v in value:
                    _walk(v)

        _walk(payload)
        self.assertEqual(
            {"provider", "kind", "model", "input_tokens", "output_tokens", "characters", "audio_seconds", "usd"},
            set(payload["entries"][0].keys()),
        )

    def test_build_turn_cost_estimates_each_usage_against_pricing(self):
        usages = [
            UsageRecord(provider="p", kind="llm", input_tokens=1_000_000, output_tokens=0),
            UsageRecord(provider="mystery", kind="tts", characters=100),
        ]
        cost = build_turn_cost("t1", "fast_model", usages, {"p": ProviderRate(input_token_per_million=2.0)})
        self.assertEqual(2, len(cost.entries))
        self.assertAlmostEqual(2.0, cost.entries[0][1])
        self.assertIsNone(cost.entries[1][1])
        self.assertAlmostEqual(2.0, cost.total_usd)

    def test_build_turn_cost_without_pricing_leaves_all_entries_unpriced(self):
        usages = [UsageRecord(provider="p", kind="llm", input_tokens=100)]
        cost = build_turn_cost("t1", "fast_model", usages, None)
        self.assertIsNone(cost.entries[0][1])
        self.assertEqual(0.0, cost.total_usd)


if __name__ == "__main__":
    unittest.main()
