"""Deterministic regression tests for the bounded Ollama startup preflight.

Slice S03 goal: fail Ollama locally and early with bounded, actionable
diagnostics. ``OllamaClient.preflight`` must distinguish an unreachable service
(``service_unavailable``) from a reachable service whose configured model is
not pulled (``model_missing``), always return within the bounded probe timeout,
and carry an exact local remediation command plus a measured duration.

These tests never touch the network: ``brain.llm.requests.get`` is patched
per-test so the preflight is fully deterministic and independent of any local
Ollama install. They are written "tests first" to pin the preflight contract
before T02 wires it into ``main.py`` startup logging and routing.
"""

import sys
import types
import unittest
from unittest import mock

import requests

from brain.llm import (
    MODEL_MISSING,
    SERVICE_UNAVAILABLE,
    OllamaClient,
    OllamaPreflight,
)


# ---------------------------------------------------------------------------
# Stub heavy optional dependencies (colorlog, Coqui TTS, numpy, pyaudio,
# openwakeword, ...) so the legacy ``main`` module can be imported in this
# lightweight test environment (see AGENTS.md: "Use mocks/fakes around legacy
# services"). ``run_ollama_preflight`` lives in ``main``, so the wiring tests
# import it with the same stub set used by ``tests/test_runtime_performance.py``.
# ---------------------------------------------------------------------------

def _legacy_dependency_stubs() -> dict[str, types.ModuleType]:
    stubs: dict[str, types.ModuleType] = {}
    for name in (
        "colorlog",
        "TTS",
        "TTS.api",
        "numpy",
        "psutil",
        "pyautogui",
        "pyaudio",
        "sounddevice",
        "soundfile",
        "webrtcvad",
        "faster_whisper",
        "openwakeword",
        "openwakeword.model",
        "openwakeword.utils",
    ):
        stubs[name] = types.ModuleType(name)

    stubs["colorlog"].ColoredFormatter = mock.Mock
    stubs["colorlog"].StreamHandler = mock.Mock
    stubs["TTS.api"].TTS = lambda *args, **kwargs: mock.Mock()
    stubs["numpy"].int16 = object()
    stubs["faster_whisper"].WhisperModel = mock.Mock
    stubs["openwakeword.model"].Model = mock.Mock
    stubs["openwakeword.utils"].download_models = mock.Mock
    return stubs


with mock.patch.dict(sys.modules, _legacy_dependency_stubs()):
    import main  # noqa: E402


class OllamaPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = OllamaClient(
            base_url="http://127.0.0.1:11434",
            model="mistral:7b-instruct",
            preflight_timeout=2.0,
        )

    def _ok_tags(self, model_names):
        """Return a Mock ``/api/tags`` response listing ``model_names``."""
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"models": [{"name": n} for n in model_names]}
        return response

    def test_successful_preflight_reports_ready(self):
        with mock.patch(
            "brain.llm.requests.get",
            return_value=self._ok_tags(["mistral:7b-instruct"]),
        ) as get:
            result = self.client.preflight()

        self.assertIsInstance(result, OllamaPreflight)
        self.assertTrue(result.ok)
        self.assertIsNone(result.error)
        self.assertIsNone(result.remediation)
        self.assertIsInstance(result.duration_seconds, float)
        self.assertGreaterEqual(result.duration_seconds, 0.0)
        get.assert_called_once_with(
            "http://127.0.0.1:11434/api/tags", timeout=2.0
        )

    def test_service_unavailable_on_connection_refused(self):
        with mock.patch(
            "brain.llm.requests.get",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            result = self.client.preflight()

        self.assertFalse(result.ok)
        self.assertEqual(SERVICE_UNAVAILABLE, result.error)
        self.assertEqual("ollama serve", result.remediation)
        self.assertIn("Could not reach", result.detail)

    def test_service_unavailable_on_timeout_is_bounded(self):
        with mock.patch(
            "brain.llm.requests.get",
            side_effect=requests.exceptions.Timeout("timed out"),
        ) as get:
            result = self.client.preflight()

        self.assertFalse(result.ok)
        self.assertEqual(SERVICE_UNAVAILABLE, result.error)
        self.assertEqual("ollama serve", result.remediation)
        self.assertIn("Timed out after 2.0s", result.detail)
        get.assert_called_once_with(
            "http://127.0.0.1:11434/api/tags", timeout=2.0
        )

    def test_model_missing_reports_exact_pull_command(self):
        with mock.patch(
            "brain.llm.requests.get",
            return_value=self._ok_tags(["llama2:latest"]),
        ):
            result = self.client.preflight()

        self.assertFalse(result.ok)
        self.assertEqual(MODEL_MISSING, result.error)
        self.assertEqual("ollama pull mistral:7b-instruct", result.remediation)
        self.assertIn("mistral:7b-instruct", result.detail)

    def test_http_error_status_is_service_unavailable(self):
        response = mock.Mock()
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "500 Server Error"
        )
        with mock.patch("brain.llm.requests.get", return_value=response):
            result = self.client.preflight()

        self.assertFalse(result.ok)
        self.assertEqual(SERVICE_UNAVAILABLE, result.error)
        self.assertEqual("ollama serve", result.remediation)

    def test_malformed_response_is_service_unavailable(self):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.side_effect = ValueError("not json")
        with mock.patch("brain.llm.requests.get", return_value=response):
            result = self.client.preflight()

        self.assertFalse(result.ok)
        self.assertEqual(SERVICE_UNAVAILABLE, result.error)
        self.assertEqual("ollama serve", result.remediation)
        self.assertIn("Malformed response", result.detail)

    def test_preflight_timeout_override_is_passed_through(self):
        with mock.patch(
            "brain.llm.requests.get",
            return_value=self._ok_tags(["mistral:7b-instruct"]),
        ) as get:
            self.client.preflight(timeout=0.25)

        get.assert_called_once_with(
            "http://127.0.0.1:11434/api/tags", timeout=0.25
        )

    def test_default_preflight_timeout_is_bounded(self):
        self.assertEqual(5.0, OllamaClient().preflight_timeout)


class MainPreflightWiringTests(unittest.TestCase):
    """Pin that ``main.run_ollama_preflight`` drives startup via the bounded
    preflight and logs the measured duration plus the exact local remediation.
    """

    def test_failed_preflight_logs_duration_and_exact_remediation(self):
        result = OllamaPreflight(
            ok=False,
            error=MODEL_MISSING,
            duration_seconds=0.042,
            remediation="ollama pull mistral:7b-instruct",
            detail="Model mistral:7b-instruct not present among 1 local model(s)",
        )
        client = mock.Mock()
        client.preflight.return_value = result

        with self.assertLogs("jarvis.main", level="INFO") as logs:
            returned = main.run_ollama_preflight(client)

        client.preflight.assert_called_once_with()
        self.assertIs(returned, result)
        combined = "\n".join(logs.output)
        self.assertIn("duration=0.042s", combined)
        self.assertIn(MODEL_MISSING, combined)
        self.assertIn("ollama pull mistral:7b-instruct", combined)

    def test_successful_preflight_logs_duration_without_remediation(self):
        result = OllamaPreflight(
            ok=True,
            error=None,
            duration_seconds=0.031,
            remediation=None,
            detail="Ollama ready: mistral:7b-instruct present",
        )
        client = mock.Mock()
        client.preflight.return_value = result

        with self.assertLogs("jarvis.main", level="INFO") as logs:
            returned = main.run_ollama_preflight(client)

        client.preflight.assert_called_once_with()
        self.assertIs(returned, result)
        combined = "\n".join(logs.output)
        self.assertIn("ok=True", combined)
        self.assertIn("duration=0.031s", combined)
        self.assertTrue(all("Remediation:" not in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
