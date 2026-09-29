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
from jarvis.adapters.models.fallback import ProviderChain, _sanitize_reason
from jarvis.adapters.models.ollama import OllamaProvider
from jarvis.adapters.models.openai_compat import OpenAICompatProvider
from jarvis.application.runtime import _build_model_chain, _model_diagnostics
from jarvis.config import load_config
from jarvis.core.errors import ProviderConfigError, ProviderError, ProviderUnavailable
from jarvis.core.turn import TurnContext
from jarvis.observability.cost import SpendLedger


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
        self.assertEqual("scripted", chain.last_selected_provider)
        self.assertEqual(2, len(chain.last_attempts))
        bad_config, selected = chain.last_attempts
        self.assertEqual("bad-config", bad_config.provider)
        self.assertFalse(bad_config.selected)
        self.assertFalse(bad_config.transient)
        self.assertIn("missing key", bad_config.reason)
        self.assertTrue(selected.selected)
        self.assertIsNone(selected.reason)

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
        self.assertIsNone(chain.last_selected_provider)
        self.assertEqual(1, len(chain.last_attempts))
        self.assertFalse(chain.last_attempts[0].selected)

    async def test_missing_key_and_secret_shaped_reason_is_sanitized_end_to_end(self):
        class LeakyConfigErrorModel:
            name = "leaky"

            async def generate(self, prompt, context):
                raise ProviderConfigError(
                    "rejected request: Authorization: Bearer sk-abcdef0123456789 is invalid",
                    provider=self.name,
                )
                yield  # pragma: no cover

        chain = ProviderChain(
            [LeakyConfigErrorModel(), ScriptedModel(default="ok")], retries=0, backoff_seconds=0
        )
        [t async for t in chain.generate("hi", TurnContext.fresh("c"))]
        reason = chain.last_attempts[0].reason
        self.assertNotIn("sk-abcdef0123456789", reason)
        self.assertNotIn("Bearer sk-abcdef0123456789", reason)

    async def test_no_providers_raises(self):
        chain = ProviderChain([])
        with self.assertRaises(ProviderUnavailable):
            async for _ in chain.generate("hi", TurnContext.fresh("c")):
                pass

    async def test_last_usage_record_comes_from_the_fallback_when_primary_fails(self):
        chain = ProviderChain(
            [FailingModel(failures=1), ScriptedModel(default="respuesta de reserva")],
            retries=0,
            backoff_seconds=0,
        )
        [t async for t in chain.generate("hi", TurnContext.fresh("c"))]
        self.assertIsNotNone(chain.last_usage_record)
        self.assertEqual("scripted", chain.last_usage_record.provider)

    async def test_last_usage_record_is_none_before_any_call(self):
        chain = ProviderChain([ScriptedModel()], retries=0, backoff_seconds=0)
        self.assertIsNone(chain.last_usage_record)

    async def test_last_usage_record_resets_at_the_start_of_each_call(self):
        model = ScriptedModel()
        chain = ProviderChain([model], retries=0, backoff_seconds=0)
        [t async for t in chain.generate("hi", TurnContext.fresh("c"))]
        self.assertIsNotNone(chain.last_usage_record)

        failing_chain = ProviderChain([FailingModel(failures=10)], retries=0, backoff_seconds=0)
        with self.assertRaises(ProviderUnavailable):
            async for _ in failing_chain.generate("hi", TurnContext.fresh("c")):
                pass
        self.assertIsNone(failing_chain.last_usage_record)


class SanitizeReasonTests(unittest.TestCase):
    def test_redacts_bearer_token(self):
        self.assertEqual("rejected: [redacted]", _sanitize_reason("rejected: Bearer sk-live-abc123"))

    def test_redacts_authorization_header(self):
        text = _sanitize_reason("blocked, Authorization: Bearer xyz789")
        self.assertNotIn("xyz789", text)

    def test_redacts_bare_api_key_pattern(self):
        self.assertNotIn("sk-abcdef123456", _sanitize_reason("key sk-abcdef123456 rejected"))

    def test_caps_length(self):
        self.assertLessEqual(len(_sanitize_reason("x" * 500)), 200)


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

    async def test_cloud_success_reports_actual_provider_even_without_usage_payload(self):
        """Regression: the stub cloud handler never emits a usage delta (many
        OpenAI-compatible upstreams do not honor stream_options.include_usage).
        Provenance must still say "openai" was selected -- it must not depend
        on billable usage having been recorded, and zero usage must not be
        mistaken for "cloud was not selected"."""
        chain = self._chain()
        tokens = [t async for t in chain.generate("hi", TurnContext.fresh("c"))]
        self.assertEqual(["nube", " ok"], tokens)
        self.assertIsNone(chain.last_usage_record)  # no usage payload from the stub
        self.assertEqual("openai", chain.last_selected_provider)
        self.assertEqual([("openai", True, None)], [
            (a.provider, a.selected, a.reason) for a in chain.last_attempts
        ])

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
        self.assertEqual("ollama", chain.last_selected_provider)
        self.assertEqual(2, len(chain.last_attempts))
        cloud_attempt, local_attempt = chain.last_attempts
        self.assertEqual("openai", cloud_attempt.provider)
        self.assertFalse(cloud_attempt.selected)
        self.assertIn("upstream down", cloud_attempt.reason)
        self.assertTrue(local_attempt.selected)

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
        self.assertEqual("ollama", chain.last_selected_provider)


class _CountingCloudHandler(BaseHTTPRequestHandler):
    """Records that it was hit; a fail-closed cap must never reach this far."""

    def do_POST(self):
        self.server.request_count += 1
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b'data: {"choices":[{"delta":{"content":"nube"}}]}\n\ndata: [DONE]\n\n')

    def log_message(self, *args):
        pass


class SpendCapFailClosedTests(unittest.IsolatedAsyncioTestCase):
    async def test_exhausted_daily_cap_fails_closed_to_local(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _CountingCloudHandler)
        server.request_count = 0
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                ledger_path = Path(tmp) / "spend.json"
                ledger = SpendLedger(ledger_path, max_daily_usd=0.01)
                ledger.record("openai", 0.02)

                chain = ProviderChain(
                    [
                        OpenAICompatProvider(
                            base_url=f"http://127.0.0.1:{server.server_address[1]}",
                            model="cloud-model",
                            api_key="sk-test",
                            ledger=ledger,
                            name="openai",
                        ),
                        ScriptedModel(default="respuesta local"),
                    ],
                    retries=0,
                    backoff_seconds=0,
                )

                tokens = [t async for t in chain.generate("hi", TurnContext.fresh("c"))]

                self.assertIn("respuesta local", "".join(tokens))
                self.assertEqual(0, server.request_count)
                self.assertEqual("scripted", chain.last_selected_provider)
                cap_attempt = chain.last_attempts[0]
                self.assertEqual("openai", cap_attempt.provider)
                self.assertFalse(cap_attempt.selected)
                self.assertIn("budget", cap_attempt.reason)

                reloaded = SpendLedger(ledger_path, max_daily_usd=0.01)
                self.assertTrue(reloaded.exhausted())
        finally:
            server.shutdown()
            server.server_close()

    def test_zero_max_daily_usd_means_uncapped(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = SpendLedger(Path(tmp) / "spend.json", max_daily_usd=0)
            ledger.record("openai", 100.0)
            self.assertFalse(ledger.exhausted())


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

    def test_windows_base_config_merges_cloud_before_local_ollama(self):
        """Reproduces the committed real-laptop merge: config.win.json (secret-free,
        committed base) plus an in-memory, secret-free local override standing in
        for the gitignored config.local.json. Confirms D026 cloud-first ordering
        survives the merge -- so a 0/20 cloud run is not an ordering bug."""
        win_config_source = Path(__file__).resolve().parents[1] / "config.win.json"
        with tempfile.TemporaryDirectory() as tmp:
            win_config_path = Path(tmp) / "config.win.json"
            win_config_path.write_text(win_config_source.read_text(encoding="utf-8"), encoding="utf-8")
            local_override = {
                "models": {
                    "max_daily_usd": 1.0,
                    "providers": [
                        {
                            "name": "cloud",
                            "kind": "openai_compat",
                            "base_url": "https://cloud.example.invalid",
                            "api_key": "${TEST_IN_MEMORY_CLOUD_KEY}",
                            "model": "cloud-flash",
                        }
                    ],
                }
            }
            (Path(tmp) / "config.local.json").write_text(json.dumps(local_override), encoding="utf-8")
            config = load_config(
                win_config_path, environ={"TEST_IN_MEMORY_CLOUD_KEY": "sk-test-in-memory-only"}
            )
        self.assertEqual(["cloud", "ollama"], [p.name for p in config.models.providers])
        chain = _build_model_chain(config)
        self.assertEqual(["cloud", "ollama"], [p.name for p in chain.providers])
        self.assertIsInstance(chain.providers[0], OpenAICompatProvider)
        self.assertIsInstance(chain.providers[1], OllamaProvider)

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
