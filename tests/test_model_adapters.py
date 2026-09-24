import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.models.ollama import OllamaProvider
from jarvis.adapters.models.openai_compat import OpenAICompatProvider
from jarvis.core.errors import ProviderConfigError, ProviderUnavailable
from jarvis.core.turn import TurnCancelled, TurnContext


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.server.requests[self.path] = json.loads(body or b"{}")
        if self.server.delay:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.flush()
            time.sleep(self.server.delay)
            return
        if self.path == "/chat/completions":
            if self.server.status == 401:
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"error":"bad key"}')
                return
            body = (
                b'data: not-json\n\n'
                b'data: {"choices":[{"delta":{"content":"hola"}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":" mundo"}}]}\n\n'
                b'data: {"choices":[],"usage":{"prompt_tokens":1000,"completion_tokens":500}}\n\n'
                b'data: [DONE]\n\n'
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/generate":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"done":true}')
        elif self.path == "/api/chat":
            body = (
                b'not-json\n'
                b'{"message":{"content":"hola"},"done":false}\n'
                b'{"message":{"content":" mundo"},"done":false}\n'
                b'{"message":{"content":""},"done":true}\n'
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


class ModelAdapterTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.server.status = 200
        cls.server.delay = 0
        cls.server.requests = {}
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    async def test_openai_compat_streams_tokens(self):
        provider = OpenAICompatProvider(
            base_url=self.base_url, model="test", api_key="sk-test"
        )
        tokens = [t async for t in provider.generate("hi", TurnContext.fresh("c"))]
        self.assertEqual(["hola", " mundo"], tokens)

    async def test_openai_compat_records_usage_and_enforces_daily_cap(self):
        import tempfile
        from jarvis.observability.cost import ProviderRate, SpendLedger

        path = Path(tempfile.mkdtemp()) / "spend.json"
        rate = ProviderRate(input_token_per_million=2.0, output_token_per_million=4.0)
        ledger = SpendLedger(path, max_daily_usd=0.005)
        provider = OpenAICompatProvider(
            base_url=self.base_url, model="test", api_key="sk-test", name="cloud", rate=rate, ledger=ledger
        )
        self.server.requests.pop("/chat/completions", None)
        [t async for t in provider.generate("hi", TurnContext.fresh("c"))]
        self.assertTrue(self.server.requests["/chat/completions"]["stream_options"]["include_usage"])
        # 1000 in * 2/M + 500 out * 4/M = 0.004 USD
        self.assertAlmostEqual(ledger.spent_today(), 0.004)
        self.assertEqual(provider.last_usage, {"input_tokens": 1000, "output_tokens": 500, "usd": 0.004})
        [t async for t in provider.generate("hi", TurnContext.fresh("c"))]  # 0.008 >= cap now
        self.server.requests.pop("/chat/completions", None)
        restarted = SpendLedger(path, max_daily_usd=0.005)  # the cap survives a restart
        capped = OpenAICompatProvider(
            base_url=self.base_url, model="test", api_key="sk-test", name="cloud", rate=rate, ledger=restarted
        )
        with self.assertRaises(ProviderConfigError):
            [t async for t in capped.generate("hi", TurnContext.fresh("c"))]
        self.assertNotIn("/chat/completions", self.server.requests)

    async def test_openai_compat_sends_provider_extra_body(self):
        provider = OpenAICompatProvider(
            base_url=self.base_url, model="test", api_key="sk-test",
            extra_body={"thinking": {"type": "disabled"}},
        )
        [t async for t in provider.generate("hi", TurnContext.fresh("c"))]
        self.assertEqual({"type": "disabled"}, self.server.requests["/chat/completions"]["thinking"])

    def test_config_parses_provider_extra_body(self):
        import tempfile
        from jarvis.config import load_config

        path = Path(tempfile.mkdtemp()) / "config.json"
        path.write_text(json.dumps({"runtime": {}, "models": {"providers": [
            {"name": "ds", "kind": "openai_compat", "model": "m", "extra_body": {"thinking": {"type": "disabled"}}}
        ]}}))
        self.assertEqual({"thinking": {"type": "disabled"}}, load_config(path).models.providers[0].extra_body)

    async def test_ollama_streams_tokens(self):
        provider = OllamaProvider(base_url=self.base_url, model="test")
        tokens = [t async for t in provider.generate("hi", TurnContext.fresh("c"))]
        self.assertEqual(["hola", " mundo"], tokens)

    async def test_voice_payloads_request_short_replies_and_keep_model_loaded(self):
        ollama = OllamaProvider(base_url=self.base_url, model="test")
        [t async for t in ollama.generate("hi", TurnContext.fresh("c"))]
        chat = self.server.requests["/api/chat"]
        self.assertEqual("system", chat["messages"][0]["role"])
        self.assertEqual({"role": "user", "content": "hi"}, chat["messages"][1])
        self.assertEqual("30m", chat["keep_alive"])
        self.assertEqual(150, chat["options"]["num_predict"])

        openai = OpenAICompatProvider(base_url=self.base_url, model="test", api_key="sk-test")
        [t async for t in openai.generate("hi", TurnContext.fresh("c"))]
        completion = self.server.requests["/chat/completions"]
        self.assertEqual("system", completion["messages"][0]["role"])
        self.assertEqual(150, completion["max_tokens"])

    def test_ollama_warm_up_loads_model_and_tolerates_down_server(self):
        self.assertTrue(OllamaProvider(base_url=self.base_url, model="test").warm_up())
        self.assertEqual(
            {"model": "test", "keep_alive": "30m"}, self.server.requests["/api/generate"]
        )
        self.assertFalse(
            OllamaProvider(base_url="http://127.0.0.1:9", model="test").warm_up(timeout_seconds=1)
        )

    async def test_cancelled_turn_does_not_open_a_provider_request(self):
        provider = OllamaProvider(base_url=self.base_url, model="test")
        context = TurnContext.fresh("c")
        context.cancellation.cancel()
        with self.assertRaises(TurnCancelled):
            await anext(provider.generate("hi", context))

    async def test_stream_timeout_is_classified_as_provider_unavailable(self):
        self.server.delay = 0.1
        provider = OpenAICompatProvider(
            base_url=self.base_url, model="test", api_key="sk-test", timeout_seconds=0.01
        )
        try:
            with self.assertRaises(ProviderUnavailable):
                async for _ in provider.generate("hi", TurnContext.fresh("c")):
                    pass
        finally:
            self.server.delay = 0

    async def test_openai_compat_requires_api_key(self):
        provider = OpenAICompatProvider(base_url=self.base_url, model="test", api_key=None)
        with self.assertRaises(ProviderConfigError):
            async for _ in provider.generate("hi", TurnContext.fresh("c")):
                pass

    async def test_http_401_classified_as_config_error(self):
        self.server.status = 401
        provider = OpenAICompatProvider(base_url=self.base_url, model="test", api_key="bad")
        with self.assertRaises(ProviderConfigError):
            async for _ in provider.generate("hi", TurnContext.fresh("c")):
                pass
        self.server.status = 200


if __name__ == "__main__":
    unittest.main()


class LocalFallbackPolicyTests(unittest.TestCase):
    def _chain(self, kinds):
        from jarvis.application.runtime import _build_model_chain
        from jarvis.config import ModelProviderConfig, ModelSettings
        from types import SimpleNamespace

        specs = tuple(ModelProviderConfig(name=f"p{i}", kind=k, base_url="http://127.0.0.1:9", model="m", api_key="k")
                      for i, k in enumerate(kinds))
        return _build_model_chain(SimpleNamespace(models=ModelSettings(providers=specs)))

    def test_ollama_as_cloud_fallback_releases_vram_and_is_not_prewarmed(self):
        from unittest import mock
        from jarvis.application.runtime import _warm_up

        chain = self._chain(["openai_compat", "ollama"])
        self.assertEqual(chain.providers[1].keep_alive, "2m")
        with mock.patch.object(OllamaProvider, "warm_up") as warm:
            _warm_up(chain, None)
        warm.assert_not_called()

    def test_ollama_as_primary_stays_resident_and_is_prewarmed(self):
        from unittest import mock
        from jarvis.application.runtime import _warm_up

        chain = self._chain(["ollama"])
        self.assertEqual(chain.providers[0].keep_alive, "30m")
        with mock.patch.object(OllamaProvider, "warm_up") as warm:
            _warm_up(chain, None)
        warm.assert_called_once()
