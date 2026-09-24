"""Per-turn cost telemetry: TurnManager.handle() attaches a TurnResult.cost
record and publishes exactly one 'turn.cost' hub event per turn.
"""

import io
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.fakes import EchoTTS, RecordingAudioPlayer, ScriptedModel
from jarvis.adapters.tools.gateway import Risk, Tool, ToolGateway, ToolResult
from jarvis.adapters.tts.voice_cache import VoiceCache
from jarvis.application.routing import Router
from jarvis.application.turn_manager import TurnManager
from jarvis.observability.cost import ProviderRate, UsageRecord
from jarvis.observability.event_hub import hub


async def _noop_tools(name, arguments, context):
    return "ok"


def _wav(n: int = 50) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(24000)
        writer.writeframes(b"\x01\x00" * n)
    return buffer.getvalue()


_PRICING = {
    "scripted": ProviderRate(input_token_per_million=1.0, output_token_per_million=2.0),
    "echo_tts": ProviderRate(character=0.001),
}


class TurnCostFastModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_model_turn_produces_priced_entries_for_model_and_tts(self):
        manager = TurnManager(
            router=Router(),
            tools=_noop_tools,
            model=ScriptedModel(),
            tts=EchoTTS(),
            audio=RecordingAudioPlayer(),
            pricing=_PRICING,
        )
        result = await manager.handle("hola que tal estas")

        self.assertIsNotNone(result.cost)
        self.assertEqual(2, len(result.cost["entries"]))
        by_provider = {entry["provider"]: entry for entry in result.cost["entries"]}
        self.assertIn("scripted", by_provider)
        self.assertIn("echo_tts", by_provider)

        model_entry = by_provider["scripted"]
        self.assertGreater(model_entry["input_tokens"], 0)
        self.assertGreater(model_entry["output_tokens"], 0)
        expected_model_usd = (
            model_entry["input_tokens"] / 1_000_000 * 1.0
            + model_entry["output_tokens"] / 1_000_000 * 2.0
        )
        self.assertAlmostEqual(round(expected_model_usd, 6), model_entry["usd"])

        tts_entry = by_provider["echo_tts"]
        self.assertGreater(tts_entry["characters"], 0)
        expected_tts_usd = tts_entry["characters"] * 0.001
        self.assertAlmostEqual(round(expected_tts_usd, 6), tts_entry["usd"])

        self.assertAlmostEqual(
            round(model_entry["usd"] + tts_entry["usd"], 6), result.cost["total_usd"]
        )

    async def test_without_pricing_usd_is_none_and_total_is_zero(self):
        manager = TurnManager(
            router=Router(),
            tools=_noop_tools,
            model=ScriptedModel(),
            tts=EchoTTS(),
            audio=RecordingAudioPlayer(),
        )
        result = await manager.handle("hola")

        self.assertIsNotNone(result.cost)
        self.assertTrue(result.cost["entries"])
        for entry in result.cost["entries"]:
            self.assertIsNone(entry["usd"])
        self.assertEqual(0.0, result.cost["total_usd"])


class _ModelWithSecret:
    """A model double carrying credential-shaped attributes that must never
    leak into the cost record."""

    name = "scripted"
    model = "scripted-model"
    api_key = "sk-secret-XYZ"

    def __init__(self) -> None:
        self.last_usage_record: UsageRecord | None = None

    async def generate(self, prompt, context):
        self.last_usage_record = None
        response = "respuesta"
        words = response.split(" ")
        for word in words:
            yield word + " "
        self.last_usage_record = UsageRecord(
            provider=self.name,
            kind="llm",
            model=self.model,
            input_tokens=len(prompt.split()),
            output_tokens=len(words),
        )


class TurnCostSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_cost_record_never_leaks_credentials_or_bytes(self):
        manager = TurnManager(
            router=Router(),
            tools=_noop_tools,
            model=_ModelWithSecret(),
            tts=EchoTTS(),
            audio=RecordingAudioPlayer(),
            pricing=_PRICING,
        )
        result = await manager.handle("hola")

        encoded = json.dumps(result.cost)
        self.assertNotIn("sk-secret", encoded)
        self.assertNotIn("api_key", encoded)
        self.assertNotIn("authorization", encoded.lower())

        def _walk(value):
            if isinstance(value, bytes):
                self.fail("cost record must never contain bytes")
            if isinstance(value, dict):
                for v in value.values():
                    _walk(v)
            elif isinstance(value, list):
                for v in value:
                    _walk(v)

        _walk(result.cost)


class TurnCostHubEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_turn_cost_event_published_exactly_once(self):
        events: list[dict] = []
        unsubscribe = hub.subscribe(lambda e: events.append(e) if e["name"] == "turn.cost" else None)
        try:
            manager = TurnManager(
                router=Router(),
                tools=_noop_tools,
                model=ScriptedModel(),
                tts=EchoTTS(),
                audio=RecordingAudioPlayer(),
                pricing=_PRICING,
            )
            result = await manager.handle("hola")
        finally:
            unsubscribe()

        self.assertEqual(1, len(events))
        event = events[0]
        self.assertEqual(result.cost["trace_id"], event["trace_id"])
        self.assertEqual(result.route, event["route"])
        self.assertEqual(result.cost["total_usd"], event["total_usd"])
        self.assertEqual(result.cost["entries"], event["entries"])


class _RepeatTool(Tool):
    name, risk, input_schema = "repeat", Risk.READ_ONLY, {}

    async def execute(self, arguments, context):
        return ToolResult("Hecho.", ok=True)


class CachedAckCostTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_ack_fast_command_yields_zero_tts_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = VoiceCache(
                Path(tmp),
                {"provider": "echo", "profile": "x", "profile_version": "v1", "model": "m", "format": "wav"},
            )
            cache.put("Hecho.", _wav())  # pre-populated: the turn never touches live TTS

            played: list[bytes] = []

            async def render(turn_id, chunk):
                played.append(chunk)

            from jarvis.adapters.audio.output import AudioOutputQueue

            manager = TurnManager(
                router=Router(),
                tools=ToolGateway([_RepeatTool()]).execute,
                model=ScriptedModel(),
                tts=EchoTTS(),
                audio=AudioOutputQueue(render=render),
                ack_cache=cache,
                pricing=_PRICING,
            )
            result = await manager.handle("repite")

            self.assertEqual("fast_command", result.route)
            self.assertEqual([], result.cost["entries"])
            self.assertEqual(0.0, result.cost["total_usd"])


if __name__ == "__main__":
    unittest.main()
