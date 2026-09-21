import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.fakes import FailingModel, ScriptedModel
from jarvis.adapters.models.fallback import ProviderChain
from jarvis.adapters.models.ollama import OllamaProvider
from jarvis.adapters.models.openai_compat import OpenAICompatProvider
from jarvis.application.runtime import _build_model_chain, _model_diagnostics
from jarvis.config import load_config
from jarvis.core.errors import ProviderConfigError, ProviderError, ProviderUnavailable
from jarvis.core.turn import TurnContext


class ConfigErrorModel:
    name = "bad-config"

    async def generate(self, prompt, context):
        raise ProviderConfigError("missing key", provider=self.name)
        yield  # pragma: no cover


class NonRetryableModel:
    name = "invalid-request"

    def __init__(self):
        self.attempts = 0

    async def generate(self, prompt, context):
        self.attempts += 1
        raise ProviderError("invalid request", transient=False, provider=self.name)
        yield  # pragma: no cover


class ImmediateModel:
    name = "immediate"

    def __init__(self):
        self.calls = 0

    async def generate(self, prompt, context):
        self.calls += 1
        yield "ok "


class PartialFailureModel:
    name = "partial-failure"

    async def generate(self, prompt, context):
        yield "partial "
        raise ProviderUnavailable("connection lost", provider=self.name)


class ProviderFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_falls_back_after_transient_failure(self):
        chain = ProviderChain(
            [FailingModel(failures=1), ScriptedModel(default="respuesta de reserva")],
            retries=0,
            backoff_seconds=0,
        )
        tokens = [t async for t in chain.generate("hi", TurnContext.fresh("c"))]
        self.assertIn("reserva", " ".join(tokens))

    async def test_config_error_skips_to_next_provider_without_retry(self):
        chain = ProviderChain(
            [ConfigErrorModel(), ScriptedModel(default="ok")],
            retries=2,
            backoff_seconds=0,
        )
        tokens = [t async for t in chain.generate("hi", TurnContext.fresh("c"))]
        self.assertEqual(["ok "], tokens)

    async def test_retries_transient_errors_with_bounded_exponential_backoff(self):
        model = FailingModel(failures=4)
        chain = ProviderChain([model], retries=4, backoff_seconds=0.25)
        with patch("jarvis.adapters.models.fallback.asyncio.sleep", new_callable=AsyncMock) as sleep:
            tokens = [t async for t in chain.generate("hi", TurnContext.fresh("c"))]
        self.assertEqual(["respuesta de reserva"], tokens)
        self.assertEqual(5, model.attempts)
        sleep.assert_has_awaits([call(0.25), call(0.5), call(1.0), call(1.0)])

    async def test_non_retryable_provider_error_falls_back_without_delay(self):
        model = NonRetryableModel()
        chain = ProviderChain([model, ImmediateModel()], retries=5)
        with patch("jarvis.adapters.models.fallback.asyncio.sleep", new_callable=AsyncMock) as sleep:
            tokens = [t async for t in chain.generate("hi", TurnContext.fresh("c"))]
        self.assertEqual(["ok "], tokens)
        self.assertEqual(1, model.attempts)
        sleep.assert_not_awaited()

    async def test_partial_stream_failure_does_not_replay_via_fallback(self):
        fallback = ImmediateModel()
        chain = ProviderChain([PartialFailureModel(), fallback], retries=2)
        with self.assertRaises(ProviderUnavailable):
            async for _ in chain.generate("hi", TurnContext.fresh("c")):
                pass
        self.assertEqual(0, fallback.calls)

    async def test_all_failures_raise(self):
        chain = ProviderChain([FailingModel(failures=10)], retries=0, backoff_seconds=0)
        with self.assertRaises(ProviderUnavailable):
            async for _ in chain.generate("hi", TurnContext.fresh("c")):
                pass

    async def test_no_providers_raises(self):
        chain = ProviderChain([])
        with self.assertRaises(ProviderUnavailable):
            async for _ in chain.generate("hi", TurnContext.fresh("c")):
                pass


class _CloudFallbackHandler(BaseHTTPRequestHandler):
    """Deterministic in-process server: cloud SSE + local Ollama NDJSON."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if self.path == "/chat/completions":
            self._serve_cloud()
        elif self.path == "/api/chat":
            self._serve_ollama()
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_cloud(self):
        mode = self.server.cloud_mode
        if mode == "error":
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"upstream down"}}')
            return
        if mode == "slow":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.flush()
            time.sleep(self.server.delay)
            return
        body = (
            b'data: {"choices":[{"delta":{"content":"nube"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":" ok"}}]}\n\n'
            b'data: [DONE]\n\n'
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(body)

    def _serve_ollama(self):
        body = (
            b'{"message":{"content":"local"},"done":false}\n'
            b'{"message":{"content":" ok"},"done":false}\n'
            b'{"message":{"content":""},"done":true}\n'
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class CloudFirstFallbackTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _CloudFallbackHandler)
        cls.server.cloud_mode = "ok"
        cls.server.delay = 0.0
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _chain(self, retries=0, on_select=None, cloud_timeout=5.0):
        return ProviderChain(
            [
                OpenAICompatProvider(
                    base_url=self.base_url,
                    model="cloud-model",
                    api_key="sk-test",
                    timeout_seconds=cloud_timeout,
                    name="openai",
                ),
                OllamaProvider(
                    base_url=self.base_url,
                    model="local-model",
                    timeout_seconds=5.0,
                    name="ollama",
                ),
            ],
            retries=retries,
            backoff_seconds=0,
            on_select=on_select,
        )

    async def test_cloud_success_streams_without_touching_fallback(self):
        selected = []
        chain = self._chain(on_select=selected.append)
        tokens = [t async for t in chain.generate("hi", TurnContext.fresh("c"))]
        self.assertEqual(["nube", " ok"], tokens)
        self.assertEqual(["openai"], selected)

    async def test_transient_http_error_advances_once_to_ollama(self):
        self.server.cloud_mode = "error"
        try:
            selected = []
            chain = self._chain(on_select=selected.append)
            tokens = [t async for t in chain.generate("hi", TurnContext.fresh("c"))]
        finally:
            self.server.cloud_mode = "ok"
        self.assertEqual(["local", " ok"], tokens)
        self.assertEqual(["openai", "ollama"], selected)

    async def test_first_token_timeout_advances_once_to_ollama(self):
        self.server.cloud_mode = "slow"
        self.server.delay = 0.2
        try:
            selected = []
            chain = self._chain(on_select=selected.append, cloud_timeout=0.05)
            tokens = [t async for t in chain.generate("hi", TurnContext.fresh("c"))]
        finally:
            self.server.cloud_mode = "ok"
            self.server.delay = 0.0
        self.assertEqual(["local", " ok"], tokens)
        self.assertEqual(["openai", "ollama"], selected)


class RuntimeWiringTests(unittest.TestCase):
    def _config_path(self, directory, providers):
        config_path = Path(directory) / "config.json"
        config_path.write_text(
            json.dumps({"runtime": {}, "models": {"providers": providers}}),
            encoding="utf-8",
        )
        return config_path

    def test_build_model_chain_orders_cloud_before_ollama(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(
                self._config_path(
                    tmp,
                    [
                        {
                            "name": "openai",
                            "kind": "openai_compat",
                            "base_url": "https://api.example.com/v1",
                            "api_key": "${OPENAI_API_KEY}",
                            "model": "gpt-4o-mini",
                        },
                        {
                            "name": "ollama",
                            "kind": "ollama",
                            "base_url": "http://localhost:11434",
                            "model": "mistral:7b-instruct",
                        },
                    ],
                ),
                environ={"OPENAI_API_KEY": "sk-test"},
            )
            chain = _build_model_chain(config)
        providers = chain.providers
        self.assertIsInstance(providers[0], OpenAICompatProvider)
        self.assertIsInstance(providers[1], OllamaProvider)
        self.assertEqual(["openai", "ollama"], [p.name for p in providers])

    def test_diagnostics_expose_order_and_key_presence_without_secret(self):
        chain = ProviderChain(
            [
                OpenAICompatProvider(
                    base_url="https://api.example.com/v1",
                    model="gpt",
                    api_key="sk-secret",
                    name="openai",
                ),
                OllamaProvider(base_url="http://localhost:11434", model="mistral", name="ollama"),
            ]
        )
        report = _model_diagnostics(chain, probe=lambda _url: False)
        self.assertEqual(["openai", "ollama"], report["provider_order"])
        self.assertTrue(report["providers"][0]["has_api_key"])
        self.assertFalse(report["providers"][1]["has_api_key"])
        self.assertEqual("openai", report["primary"]["name"])
        self.assertEqual("openai_compat", report["primary"]["kind"])
        self.assertEqual("gpt", report["primary"]["model"])
        self.assertEqual("https://api.example.com/v1", report["primary"]["base_url"])
        self.assertTrue(report["primary"]["has_api_key"])
        self.assertEqual(["ollama"], [f["name"] for f in report["fallbacks"]])
        self.assertFalse(report["local_fallback_ready"])
        self.assertNotIn("sk-secret", json.dumps(report))

    def test_diagnostics_empty_for_non_chain_model(self):
        report = _model_diagnostics(ScriptedModel())
        self.assertEqual(
            {
                "provider_order": [],
                "providers": [],
                "primary": None,
                "fallbacks": [],
                "local_fallback_ready": False,
            },
            report,
        )

    def test_diagnostics_reports_local_fallback_ready_from_probe(self):
        def _chain():
            return ProviderChain(
                [
                    OpenAICompatProvider(
                        base_url="https://api.example.com/v1",
                        model="gpt",
                        api_key="sk-secret",
                        name="openai",
                    ),
                    OllamaProvider(
                        base_url="http://localhost:11434", model="mistral", name="ollama"
                    ),
                ]
            )

        ready = _model_diagnostics(_chain(), probe=lambda _url: True)
        self.assertTrue(ready["local_fallback_ready"])

        unavailable = _model_diagnostics(_chain(), probe=lambda _url: False)
        self.assertFalse(unavailable["local_fallback_ready"])

    def test_diagnostics_never_probes_cloud_only_chain(self):
        probe = Mock(return_value=True)
        chain = ProviderChain(
            [
                OpenAICompatProvider(
                    base_url="https://api.example.com/v1",
                    model="gpt",
                    api_key="sk-secret",
                    name="openai",
                )
            ]
        )
        report = _model_diagnostics(chain, probe=probe)
        self.assertFalse(report["local_fallback_ready"])
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
