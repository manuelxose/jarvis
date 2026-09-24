"""Offline STT bake-off evidence, timing, and failure-path contracts."""

import asyncio
import io
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

import stt_bakeoff as sb  # noqa: E402
from jarvis.core.contracts import Transcript  # noqa: E402
from jarvis.core.errors import ProviderConfigError, ProviderUnavailable  # noqa: E402


class FakeSTT:
    def __init__(self, text="cancion", *, failure=None, empty=False, partial_only=False, delay=0.01):
        self.text, self.failure, self.empty, self.partial_only, self.delay = text, failure, empty, partial_only, delay
        self.frames = []

    async def transcribe(self, audio, context):
        if self.failure:
            raise self.failure("private provider payload", provider="fake")
        if self.empty:
            return
        async for frame in audio:
            self.frames.append(frame)
        await asyncio.sleep(self.delay)
        yield Transcript(self.text if self.partial_only else "partial-private", is_final=False)
        if not self.partial_only:
            await asyncio.sleep(self.delay)
            yield Transcript(self.text, is_final=True)


class BakeoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = SimpleNamespace(audio=SimpleNamespace(sample_rate=16000),
                                      stt=SimpleNamespace(api_key="sk-sentinel-secret"),
                                      alibaba=SimpleNamespace(workspace_id="workspace-private"))
        self.wav("test.wav")
        self.manifest([{"id": "es-01", "audio": "test.wav", "language": "es", "reference": "canción"}])

    def wav(self, name, *, channels=1, width=2, rate=16000):
        with wave.open(str(self.base / name), "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(width)
            wav.setframerate(rate)
            wav.writeframes(b"\0" * (rate * width * channels // 10))

    def manifest(self, clips):
        (self.base / "manifest.json").write_text(json.dumps({"clips": clips}), encoding="utf-8")

    def run_cli(self, providers, *args):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sb, "load_config", return_value=self.config), \
             patch.object(sb, "_resolve_providers", side_effect=lambda cfg, names: {names[0]: providers[names[0]]}):
            status = sb.main(["--manifest", str(self.base / "manifest.json"), "--no-realtime", "--json", *args],
                             stdout=output, stderr=errors)
        return status, json.loads(output.getvalue()) if output.getvalue() else None, errors.getvalue()

    def test_schema_timing_accuracy_cost_and_nondisclosure(self):
        local, cloud = FakeSTT(), FakeSTT("cancion")
        status, record, errors = self.run_cli(
            {"whisper": local, "alibaba_qwen": cloud},
            "--usd-per-audio-minute", "alibaba_qwen=0.6", "--output", str(self.base / "evidence.json"))
        self.assertEqual(0, status)
        self.assertEqual({"schema", "platform", "python", "runs", "summary"}, set(record))
        self.assertEqual({"ok", "median_first_partial_ms", "median_final_ms", "mean_cer", "mean_wer", "total_estimated_usd"},
                         set(record["summary"]["whisper"]))
        for row in record["runs"]:
            self.assertEqual(set(sb.ROW_KEYS), set(row))
            self.assertEqual("ok", row["outcome"])
            self.assertEqual(0, row["cer"])
            self.assertEqual(0, row["wer"])
            self.assertLess(row["first_partial_ms"], row["final_ms"])
            self.assertGreaterEqual(row["final_ms"], 15)  # measured after feeder's last frame
            self.assertEqual([640] * 5, list(map(len, (local if row["provider"] == "whisper" else cloud).frames)))
        self.assertIsNone(record["runs"][0]["estimated_usd"])
        self.assertAlmostEqual(0.001, record["runs"][1]["estimated_usd"])
        self.assertEqual(1, record["summary"]["alibaba_qwen"]["ok"])
        self.assertEqual(0.001, record["summary"]["alibaba_qwen"]["total_estimated_usd"])
        serialized = json.dumps(record) + (self.base / "evidence.json").read_text()
        for secret in ("sk-sentinel-secret", "workspace-private", "canción", "cancion", "partial-private"):
            self.assertNotIn(secret, serialized)
        self.assertEqual("", errors)

    def test_accuracy_boundaries(self):
        self.assertEqual((0, 0), sb._error_rates("Canción!", "cancion"))
        self.assertEqual((0.6, 0.5), sb._error_rates("hola mundo", "hola"))
        self.assertEqual((None, None), sb._error_rates("", "algo"))
        self.assertEqual((0.25, 1.0), sb._error_rates("hola", "ola"))

    def test_empty_reference_and_partial_fallback(self):
        self.manifest([{"id": "es-01", "audio": "test.wav", "language": "es", "reference": ""}])
        status, record, _ = self.run_cli({"whisper": FakeSTT()}, "--providers", "whisper")
        self.assertEqual(0, status)
        self.assertIsNone(record["runs"][0]["cer"])
        self.assertIsNone(record["summary"]["whisper"]["mean_wer"])
        status, record, _ = self.run_cli({"whisper": FakeSTT(partial_only=True)}, "--providers", "whisper")
        self.assertEqual(0, status)
        self.assertIsNone(record["runs"][0]["final_ms"])

    def test_provider_failure_and_empty_stream_are_isolated(self):
        for exception, expected in ((ProviderUnavailable, "unavailable"), (ProviderConfigError, "config_error")):
            with self.subTest(expected=expected):
                status, record, errors = self.run_cli({"whisper": FakeSTT(), "alibaba_qwen": FakeSTT(failure=exception)})
                self.assertEqual(1, status)
                self.assertEqual(["ok", expected], [row["outcome"] for row in record["runs"]])
                self.assertNotIn("private provider payload", errors + json.dumps(record))
        status, record, _ = self.run_cli({"whisper": FakeSTT(empty=True)}, "--providers", "whisper")
        self.assertEqual(1, status)
        self.assertEqual("no_transcript", record["runs"][0]["outcome"])
        self.assertIsNone(record["summary"]["whisper"]["median_final_ms"])

    def test_wrong_format_and_missing_file(self):
        self.wav("bad.wav", channels=2)
        self.manifest([{"id": "bad", "audio": "bad.wav", "language": "es", "reference": "x"},
                       {"id": "gone", "audio": "gone.wav", "language": "es", "reference": "y"}])
        status, record, _ = self.run_cli({"whisper": FakeSTT()}, "--providers", "whisper")
        self.assertEqual(1, status)
        self.assertEqual(["missing_audio", "missing_audio"], [row["outcome"] for row in record["runs"]])

    def test_invalid_manifest_and_cli(self):
        for manifest in ("{}", '{"clips":[{"id":"x"}]}', "not json",
                         '{"clips":[{"id":"x","audio":"../outside.wav","language":"es","reference":"x"}]}'):
            with self.subTest(manifest=manifest):
                (self.base / "manifest.json").write_text(manifest)
                out, err = io.StringIO(), io.StringIO()
                self.assertEqual(2, sb.main(["--manifest", str(self.base / "manifest.json")], stdout=out, stderr=err))
                self.assertEqual("", out.getvalue())
                self.assertIn("error:", err.getvalue())
        for args in (("--timeout", "0"), ("--providers", "other"),
                     ("--usd-per-audio-minute", "whisper=-1"), ("--usd-per-audio-minute", "whisper")):
            with self.subTest(args=args):
                self.assertEqual(2, sb.main(["--manifest", "unused", *args], stdout=io.StringIO(), stderr=io.StringIO()))

    def test_provider_construction_matches_runtime_wiring(self):
        config = SimpleNamespace(
            audio=SimpleNamespace(sample_rate=16000),
            stt=SimpleNamespace(model="turbo", language="es", device="cuda"),
            activation=SimpleNamespace(wake_word="Jarvis"),
        )
        with patch.object(sb, "WhisperSTT") as whisper, patch.object(sb.AlibabaQwenSTT, "from_config") as cloud:
            result = sb._resolve_providers(config, sb.PROVIDERS)
        whisper.assert_called_once_with(model="turbo", language="es", device="cuda", sample_rate=16000,
                                        hotwords="Jarvis")
        cloud.assert_called_once_with(config)
        self.assertEqual(set(sb.PROVIDERS), set(result))

    def test_timeout_is_bounded(self):
        status, record, _ = self.run_cli({"whisper": FakeSTT(delay=0.1)}, "--providers", "whisper", "--timeout", "0.01")
        self.assertEqual(1, status)
        self.assertEqual("unavailable", record["runs"][0]["outcome"])


if __name__ == "__main__":
    unittest.main()
