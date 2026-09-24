"""Pure evidence-gated TTS ordering; no adapter or network calls."""
import sys
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.tts.fallback import order_tts_candidates
from jarvis.config import TTSRoutingEvidence, RuntimeConfig, RuntimeSettings, MemorySettings, TTSSettings
from jarvis.core.errors import ProviderConfigError, ProviderUnavailable
from jarvis.application.runtime import _build_tts_chain, _model_pricing
from jarvis.application.turn_manager import TurnManager
from jarvis.application.routing import Router
from jarvis.adapters.fakes import RecordingAudioPlayer, ScriptedModel
from jarvis.core.circuit_breaker import CircuitBreaker
from jarvis.observability.event_hub import hub


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


class _FakeTTS:
    def __init__(self, name, *, fail=False):
        self.name, self.fail, self.calls = name, fail, 0

    async def synthesize(self, text, context):
        self.calls += 1
        async for _ in text:
            if self.fail:
                raise ProviderUnavailable("unavailable", provider=self.name)
            yield b"audio"


class RuntimeRoutingTests(unittest.IsolatedAsyncioTestCase):
    def config(self, profile="auto", rows=(), provider="alibaba_qwen"):
        return RuntimeConfig(runtime=RuntimeSettings(), memory=MemorySettings(),
                             tts=TTSSettings(provider=provider, profile=profile, routing_evidence=tuple(rows)))

    def chain(self, config, primary, fallback):
        with patch("jarvis.application.runtime.resolve_tts", return_value=primary), \
             patch("jarvis.application.runtime.Pyttsx3TTS", return_value=fallback):
            return _build_tts_chain(config)

    async def speak(self, chain, config):
        async def tools(name, arguments, context):
            return "ok"
        manager = TurnManager(router=Router(), tools=tools, model=ScriptedModel(),
                              tts=chain, audio=RecordingAudioPlayer(), pricing=_model_pricing(config))
        return await manager.handle("hola")

    async def test_composition_orders_measured_candidates_and_attributes_selected_price(self):
        config = self.config("cheap", (row("alibaba_qwen", cost=0.02), row("sapi", cost=0.001)))
        cloud, sapi = _FakeTTS("alibaba_qwen"), _FakeTTS("sapi")
        chain = self.chain(config, cloud, sapi)
        self.assertEqual((sapi, cloud), chain.providers)
        result = await self.speak(chain, config)
        entry = next(e for e in result.cost["entries"] if e["kind"] == "tts")
        self.assertEqual("sapi", entry["provider"])
        self.assertEqual(round(entry["characters"] * 0.001, 6), entry["usd"])
        self.assertEqual(0, cloud.calls)

    async def test_default_primary_failure_and_open_circuit_use_unpriced_fallback(self):
        config = self.config()
        cloud, sapi = _FakeTTS("alibaba_qwen", fail=True), _FakeTTS("sapi")
        chain = self.chain(config, cloud, sapi)
        chain._backoff_seconds = 0
        self.assertEqual((cloud, sapi), chain.providers)
        result = await self.speak(chain, config)
        entry = next(e for e in result.cost["entries"] if e["kind"] == "tts")
        self.assertEqual("sapi", entry["provider"])
        self.assertIsNone(entry["usd"])
        self.assertGreater(cloud.calls, 0)
        chain._breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=100)
        chain._breaker.record_failure("alibaba_qwen")
        before = cloud.calls
        result = await self.speak(chain, config)
        self.assertEqual(before, cloud.calls)
        self.assertEqual("sapi", next(e for e in result.cost["entries"] if e["kind"] == "tts")["provider"])

    async def test_priced_fallback_uses_serving_provider_rate_not_primary(self):
        config = self.config("fast", (row("alibaba_qwen", latency=10, cost=0.02),
                                      row("sapi", latency=100, cost=0.003)))
        cloud, sapi = _FakeTTS("alibaba_qwen", fail=True), _FakeTTS("sapi")
        chain = self.chain(config, cloud, sapi)
        chain._backoff_seconds = 0
        result = await self.speak(chain, config)
        entry = next(e for e in result.cost["entries"] if e["kind"] == "tts")
        self.assertEqual("sapi", entry["provider"])
        self.assertEqual(round(entry["characters"] * 0.003, 6), entry["usd"])

    async def test_all_providers_fail_without_tts_charge(self):
        config = self.config()
        chain = self.chain(config, _FakeTTS("alibaba_qwen", fail=True), _FakeTTS("sapi", fail=True))
        chain._backoff_seconds = 0
        events = []
        unsubscribe = hub.subscribe(lambda event: events.append(event) if event["name"] == "turn.cost" else None)
        try:
            with self.assertRaises(ProviderUnavailable):
                await self.speak(chain, config)
        finally:
            unsubscribe()
        self.assertEqual([], events[-1]["entries"])

    async def test_routing_log_excludes_evidence_payload(self):
        config = self.config("fast", (row("alibaba_qwen", latency=10), row("sapi", latency=100)))
        with self.assertLogs("jarvis.runtime", level="INFO") as logs:
            self.chain(config, _FakeTTS("alibaba_qwen"), _FakeTTS("sapi"))
        self.assertIn("profile=fast", logs.output[0])
        self.assertIn("providers=['alibaba_qwen', 'sapi']", logs.output[0])
        self.assertNotIn("same-run", logs.output[0])
        self.assertNotIn("operator", logs.output[0])
        self.assertNotIn("0.01", logs.output[0])

    async def test_singleton_explicit_profile_and_invalid_comparison(self):
        config = self.config("fast", provider="sapi")
        sapi = _FakeTTS("sapi")
        self.assertIs(sapi, self.chain(config, sapi, _FakeTTS("unused")))
        config = self.config("fast")
        with self.assertRaises(ProviderConfigError):
            self.chain(config, _FakeTTS("alibaba_qwen"), _FakeTTS("sapi"))


if __name__ == "__main__":
    unittest.main()
