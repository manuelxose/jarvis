"""Tests for the v2 real-hardware voice acceptance diagnostic.

These cover the pure/logic layers (percentile math, environment capture,
backend probing, STT model caching, and CLI dispatch) that run without audio
hardware. The real WASAPI/G435/STT timing itself is exercised only by the
``jarvis diagnose voice --acceptance`` command on the Windows target machine.
"""

import sys
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.application import voice_acceptance as va  # noqa: E402
from jarvis.adapters.stt.whisper import WhisperSTT  # noqa: E402


def make_config(provider: str = "whisper"):
    return SimpleNamespace(
        audio=SimpleNamespace(
            sample_rate=16000, channels=1, chunk_size=1024, input_device=None, output_device=None
        ),
        stt=SimpleNamespace(provider=provider, model="tiny", language="es", device="cpu"),
    )


class PercentileTests(unittest.TestCase):
    def test_percentiles_known_values(self):
        self.assertEqual(
            {"p50": 3.0, "p95": 4.8, "max": 5.0},
            va._percentiles([1.0, 2.0, 3.0, 4.0, 5.0]),
        )

    def test_percentiles_empty(self):
        self.assertEqual(
            {"p50": 0.0, "p95": 0.0, "max": 0.0}, va._percentiles([])
        )

    def test_percentiles_single_sample(self):
        self.assertEqual(
            {"p50": 2.0, "p95": 2.0, "max": 2.0}, va._percentiles([2.0])
        )


class EnvironmentCaptureTests(unittest.TestCase):
    def test_capture_environment_reports_config(self):
        env = va.capture_environment(make_config())
        self.assertIn("system", env["os"])
        self.assertEqual(16000, env["configured_audio"]["sample_rate"])
        self.assertEqual(1, env["configured_audio"]["channels"])
        self.assertEqual("whisper", env["configured_stt"]["provider"])
        self.assertEqual("tiny", env["configured_stt"]["model"])

    @mock.patch.object(va, "_sounddevice", return_value=None)
    @mock.patch.object(va, "list_devices", return_value=[])
    def test_audio_backend_info_without_sounddevice(self, _list, _sd):
        info = va.audio_backend_info(make_config())
        self.assertFalse(info["sounddevice_available"])
        self.assertFalse(info["wasapi_hostapi"]["present"])
        self.assertEqual([], info["discovered_input_devices"])
        self.assertEqual("int16 mono PCM", info["capture_format"])

    def test_wasapi_hostapi_detection(self):
        self.assertIsNone(va._wasapi_hostapi(None))
        self.assertIsNone(va._wasapi_hostapi([]))
        found = va._wasapi_hostapi(
            [{"name": "MME", "default_input_device": 0}, {"name": "Windows WASAPI", "default_input_device": 6}]
        )
        self.assertEqual(6, found["default_input_device"])


class WhisperModelCachingTests(unittest.TestCase):
    def test_load_model_reuses_cached_instance(self):
        adapter = WhisperSTT()
        sentinel = object()
        adapter._model = sentinel
        self.assertIs(sentinel, adapter._load_model())

    def test_load_model_instantiates_once(self):
        adapter = WhisperSTT()
        fake_model = object()
        whisper_model = mock.Mock(return_value=fake_model)
        fake_module = SimpleNamespace(WhisperModel=whisper_model)
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}):
            first = adapter._load_model()
            second = adapter._load_model()
        self.assertIs(fake_model, first)
        self.assertIs(first, second)
        whisper_model.assert_called_once()


class CliDispatchTests(unittest.TestCase):
    def test_diagnose_voice_acceptance_runs_acceptance(self):
        from jarvis.apps.cli import _run_diagnose_command

        args = SimpleNamespace(target="voice", acceptance=True, use_fakes=False, json=False)
        stdout = StringIO()
        stderr = StringIO()
        with mock.patch(
            "jarvis.application.voice_acceptance.run_voice_acceptance",
            return_value={"environment": {}, "runs": []},
        ) as runner, mock.patch(
            "jarvis.application.voice_acceptance.format_report", return_value="{}"
        ):
            code = _run_diagnose_command(make_config(), args, stdout, stderr)
        self.assertEqual(0, code)
        runner.assert_called_once()
        self.assertEqual("{}\n", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
