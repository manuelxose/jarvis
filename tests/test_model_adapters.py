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
        self.rfile.read(length)
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
                b'data: [DONE]\n\n'
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(body)
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

    async def test_ollama_streams_tokens(self):
        provider = OllamaProvider(base_url=self.base_url, model="test")
        tokens = [t async for t in provider.generate("hi", TurnContext.fresh("c"))]
        self.assertEqual(["hola", " mundo"], tokens)

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
