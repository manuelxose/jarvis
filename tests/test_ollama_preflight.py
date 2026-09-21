import unittest
from unittest.mock import Mock, patch

from legacy.brain.llm import OllamaClient


class OllamaPreflightTests(unittest.TestCase):
    def test_chat_payload_bounds_local_response_and_keeps_model_warm(self):
        payload = OllamaClient()._build_payload(
            messages=[{"role": "user", "content": "hola"}],
            system_prompt="responde breve",
            stream=False,
        )

        self.assertEqual("10m", payload["keep_alive"])
        self.assertEqual(128, payload["options"]["num_predict"])
        self.assertEqual(2048, payload["options"]["num_ctx"])

    def test_preflight_reports_service_remediation_with_bounded_timeout(self):
        client = OllamaClient(timeout=30)
        with patch("legacy.brain.llm.requests.get", side_effect=ConnectionError("offline")) as get:
            result = client.preflight(timeout=1)
        self.assertFalse(result.available)
        self.assertIn("ollama serve", result.message)
        self.assertEqual(1, get.call_args.kwargs["timeout"])

    def test_preflight_reports_missing_model(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"models": [{"name": "other:latest"}]}
        with patch("legacy.brain.llm.requests.get", return_value=response):
            result = OllamaClient(model="mistral:7b-instruct").preflight(timeout=1)
        self.assertFalse(result.available)
        self.assertIn("ollama pull mistral:7b-instruct", result.message)

    def test_preflight_accepts_configured_model(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"models": [{"name": "mistral:7b-instruct"}]}
        with patch("legacy.brain.llm.requests.get", return_value=response):
            result = OllamaClient(model="mistral:7b-instruct").preflight(timeout=1)
        self.assertTrue(result.available)
