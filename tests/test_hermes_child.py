import asyncio
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jarvis.adapters.hermes import protocol
from jarvis.adapters.hermes.child import HermesChildAdapter, default_command
from jarvis.adapters.models.ollama import FALLBACK_KEEP_ALIVE, KEEP_ALIVE
from jarvis.application.runtime import _hermes_env
from jarvis.config import load_config
from jarvis.core.contracts import AgentStatus, AgentToken, HealthStatus
from jarvis.core.turn import TurnCancelled, TurnContext


async def collect(adapter: HermesChildAdapter, text: str, context: TurnContext):
    return [event async for event in adapter.respond(text, context)]


class _FakeOllamaHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if self.path == "/api/chat":
            body = (
                b"not-json\n"
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


class _FakeOllamaServerTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllamaHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()


class HermesChildAdapterTests(_FakeOllamaServerTests):
    async def test_real_child_streams_tokens_tools_and_healthy_lifecycle(self):
        adapter = HermesChildAdapter(
            timeout_seconds=1,
            env={"JARVIS_OLLAMA_BASE_URL": self.base_url, "JARVIS_OLLAMA_MODEL": "test"},
        )
        self.addAsyncCleanup(adapter.stop)

        events = await collect(adapter, "estado del sistema", TurnContext.fresh("conversation"))

        self.assertTrue(any(isinstance(event, AgentToken) for event in events))
        self.assertTrue(any(isinstance(event, AgentStatus) and event.detail == "done" for event in events))
        self.assertEqual(HealthStatus.HEALTHY, (await adapter.health()).status)
        await adapter.stop()
        self.assertEqual(HealthStatus.DEGRADED, (await adapter.health()).status)

    async def test_crashed_child_restarts_once_then_returns_degraded_fallback(self):
        adapter = HermesChildAdapter(
            timeout_seconds=1,
            restart_max=1,
            env={"JARVIS_OLLAMA_BASE_URL": self.base_url, "JARVIS_OLLAMA_MODEL": "test"},
        )
        self.addAsyncCleanup(adapter.stop)

        first_failure = await collect(adapter, "__CRASH__", TurnContext.fresh("conversation"))
        recovered = await collect(adapter, "continua", TurnContext.fresh("conversation"))
        second_failure = await collect(adapter, "__CRASH__", TurnContext.fresh("conversation"))
        exhausted = await collect(adapter, "continua", TurnContext.fresh("conversation"))

        self.assertTrue(_unavailable(first_failure))
        self.assertTrue(any(isinstance(event, AgentToken) for event in recovered))
        self.assertTrue(_unavailable(second_failure))
        self.assertEqual("Hermes unavailable: Hermes restart budget exhausted", exhausted[0].detail)
        self.assertEqual(HealthStatus.DEGRADED, (await adapter.health()).status)

    async def test_timeout_degrades_and_terminates_unresponsive_child(self):
        adapter = HermesChildAdapter(
            [sys.executable, "-c", "import sys, time; sys.stdin.readline(); time.sleep(5)"],
            timeout_seconds=0.02,
        )
        self.addAsyncCleanup(adapter.stop)

        events = await collect(adapter, "wait", TurnContext.fresh("conversation", timeout_seconds=1))

        self.assertTrue(_unavailable(events))
        self.assertEqual(HealthStatus.DEGRADED, (await adapter.health()).status)

    async def test_cancellation_interrupts_an_in_flight_child_read(self):
        adapter = HermesChildAdapter(
            [sys.executable, "-c", "import sys, time; sys.stdin.readline(); time.sleep(5)"],
            timeout_seconds=1,
        )
        self.addAsyncCleanup(adapter.stop)
        context = TurnContext.fresh("conversation", timeout_seconds=1)

        consumer = asyncio.create_task(collect(adapter, "wait", context))
        await asyncio.sleep(0.1)
        context.cancellation.cancel()

        with self.assertRaises(TurnCancelled):
            await consumer
        self.assertEqual(HealthStatus.DEGRADED, (await adapter.health()).status)

    async def test_missing_command_reports_degraded_fallback_without_spawning(self):
        adapter = HermesChildAdapter([])

        events = await collect(adapter, "hello", TurnContext.fresh("conversation"))

        self.assertEqual("Hermes unavailable: Hermes command not configured", events[0].detail)
        self.assertEqual(HealthStatus.DEGRADED, (await adapter.health()).status)

    async def test_real_child_ignores_malformed_inbound_record(self):
        process = await asyncio.create_subprocess_exec(
            *default_command(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        self.addAsyncCleanup(_stop_process, process)
        process.stdin.write(b"[]\n")
        process.stdin.write(
            (
                protocol.encode_message("request", "turn", protocol.REQUEST, {"text": "hello"}) + "\n"
            ).encode()
        )
        await process.stdin.drain()

        message = protocol.parse_message(
            (await asyncio.wait_for(process.stdout.readline(), timeout=1)).decode()
        )

        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(protocol.STARTED, message.event_type)


class HermesChildAdapterContractTests(_FakeOllamaServerTests):
    async def test_contract_streams_canned_ndjson_turn(self):
        adapter = HermesChildAdapter(
            timeout_seconds=5,
            env={"JARVIS_OLLAMA_BASE_URL": self.base_url, "JARVIS_OLLAMA_MODEL": "test"},
        )
        self.addAsyncCleanup(adapter.stop)

        events = await collect(adapter, "hola", TurnContext.fresh("conversation"))

        tokens = [event.text for event in events if isinstance(event, AgentToken)]
        self.assertEqual(["hola", " mundo"], tokens)
        self.assertTrue(
            any(isinstance(event, AgentStatus) and event.detail == "done" for event in events)
        )


class _BlockingOllamaHandler(BaseHTTPRequestHandler):
    """Streams one token, then blocks on ``server.release_event`` before ``done``."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if self.path != "/api/chat":
            self.send_response(404)
            self.end_headers()
            return
        self.server.recorded_body = json.loads(body)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        self.wfile.write(b'{"message":{"content":"hola"},"done":false}\n')
        self.wfile.flush()
        self.server.release_event.wait(timeout=5)
        self.wfile.write(b'{"message":{"content":""},"done":true}\n')

    def log_message(self, *args):
        pass


class HermesChildKeepAliveStreamingTests(unittest.IsolatedAsyncioTestCase):
    def _start_server(self):
        release_event = threading.Event()
        server = ThreadingHTTPServer(("127.0.0.1", 0), _BlockingOllamaHandler)
        server.release_event = release_event
        server.recorded_body = None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return server, release_event

    async def test_first_token_streams_before_server_unblocks_and_keep_alive_is_forwarded(self):
        server, release_event = self._start_server()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        adapter = HermesChildAdapter(
            timeout_seconds=5,
            env={
                "JARVIS_OLLAMA_BASE_URL": base_url,
                "JARVIS_OLLAMA_MODEL": "test",
                "JARVIS_OLLAMA_KEEP_ALIVE": "7m",
            },
        )
        self.addAsyncCleanup(adapter.stop)

        saw_token_while_server_blocked = False
        completed = False
        async for event in adapter.respond("hola", TurnContext.fresh("conversation", timeout_seconds=5)):
            if isinstance(event, AgentToken) and not release_event.is_set():
                saw_token_while_server_blocked = True
                release_event.set()
            if isinstance(event, AgentStatus) and event.detail == "done":
                completed = True

        self.assertTrue(saw_token_while_server_blocked, "first token must arrive before the server's done frame")
        self.assertTrue(completed)
        self.assertEqual("7m", server.recorded_body["keep_alive"])

    async def test_default_keep_alive_is_0_when_env_unset(self):
        """D043: the child's built-in default matches FALLBACK_KEEP_ALIVE ('0'),
        not the resident-primary 30m, so a fallback burst never leaves the
        model resident even when the runtime env is missing."""
        server, release_event = self._start_server()
        release_event.set()  # do not block completion; this test only checks the payload
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        adapter = HermesChildAdapter(
            timeout_seconds=5,
            env={"JARVIS_OLLAMA_BASE_URL": base_url, "JARVIS_OLLAMA_MODEL": "test"},
        )
        self.addAsyncCleanup(adapter.stop)

        events = await collect(adapter, "hola", TurnContext.fresh("conversation"))

        self.assertTrue(any(isinstance(event, AgentToken) for event in events))
        self.assertEqual("0", server.recorded_body["keep_alive"])

    async def test_options_from_env_are_merged_into_the_request(self):
        server, release_event = self._start_server()
        release_event.set()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        adapter = HermesChildAdapter(
            timeout_seconds=5,
            env={
                "JARVIS_OLLAMA_BASE_URL": base_url,
                "JARVIS_OLLAMA_MODEL": "test",
                "JARVIS_OLLAMA_OPTIONS": json.dumps({"num_gpu": 0}),
            },
        )
        self.addAsyncCleanup(adapter.stop)

        events = await collect(adapter, "hola", TurnContext.fresh("conversation"))

        self.assertTrue(any(isinstance(event, AgentToken) for event in events))
        self.assertEqual({"temperature": 0.7, "num_gpu": 0}, server.recorded_body["options"])

    async def test_malformed_or_non_object_options_env_is_ignored(self):
        for bad in ("{not json", "[1, 2]", '"text"'):
            with self.subTest(bad=bad):
                server, release_event = self._start_server()
                release_event.set()
                base_url = f"http://127.0.0.1:{server.server_address[1]}"
                adapter = HermesChildAdapter(
                    timeout_seconds=5,
                    env={
                        "JARVIS_OLLAMA_BASE_URL": base_url,
                        "JARVIS_OLLAMA_MODEL": "test",
                        "JARVIS_OLLAMA_OPTIONS": bad,
                    },
                )
                self.addAsyncCleanup(adapter.stop)

                events = await collect(adapter, "hola", TurnContext.fresh("conversation"))

                self.assertTrue(any(isinstance(event, AgentToken) for event in events))
                self.assertEqual({"temperature": 0.7}, server.recorded_body["options"])


class HermesEnvTests(unittest.TestCase):
    """Unit tests for runtime._hermes_env: config-driven, non-secret Ollama env."""

    def _config(self, tmp, providers):
        config_path = Path(tmp) / "config.json"
        config_path.write_text(
            json.dumps({"runtime": {}, "models": {"providers": providers}}), encoding="utf-8"
        )
        return load_config(config_path, environ={"OPENAI_API_KEY": "sk-test"})

    def test_ollama_primary_uses_the_resident_keep_alive(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(
                tmp,
                [{"name": "ollama", "kind": "ollama", "base_url": "http://localhost:11434", "model": "mistral:7b-instruct"}],
            )
            env = _hermes_env(config)
        self.assertEqual(
            {
                "JARVIS_OLLAMA_BASE_URL": "http://localhost:11434",
                "JARVIS_OLLAMA_MODEL": "mistral:7b-instruct",
                "JARVIS_OLLAMA_KEEP_ALIVE": KEEP_ALIVE,
            },
            env,
        )

    def test_cloud_first_ollama_second_uses_the_short_fallback_keep_alive(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(
                tmp,
                [
                    {
                        "name": "openai",
                        "kind": "openai_compat",
                        "base_url": "https://api.example.com/v1",
                        "api_key": "${OPENAI_API_KEY}",
                        "model": "gpt-4o-mini",
                    },
                    {"name": "ollama", "kind": "ollama", "base_url": "http://localhost:11434", "model": "mistral:7b-instruct"},
                ],
            )
            env = _hermes_env(config)
        self.assertEqual(
            {
                "JARVIS_OLLAMA_BASE_URL": "http://localhost:11434",
                "JARVIS_OLLAMA_MODEL": "mistral:7b-instruct",
                "JARVIS_OLLAMA_KEEP_ALIVE": FALLBACK_KEEP_ALIVE,
            },
            env,
        )

    def test_cpu_only_fallback_env_carries_options_and_resident_keep_alive(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(
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
                        "model": "qwen2.5:3b-instruct",
                        "extra_body": {"options": {"num_gpu": 0}},
                    },
                ],
            )
            env = _hermes_env(config)
        self.assertEqual(
            {
                "JARVIS_OLLAMA_BASE_URL": "http://localhost:11434",
                "JARVIS_OLLAMA_MODEL": "qwen2.5:3b-instruct",
                "JARVIS_OLLAMA_KEEP_ALIVE": KEEP_ALIVE,
                "JARVIS_OLLAMA_OPTIONS": json.dumps({"num_gpu": 0}),
            },
            env,
        )
        self.assertNotIn("sk-test", json.dumps(env))

    def test_no_ollama_provider_yields_empty_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(
                tmp,
                [
                    {
                        "name": "openai",
                        "kind": "openai_compat",
                        "base_url": "https://api.example.com/v1",
                        "api_key": "${OPENAI_API_KEY}",
                        "model": "gpt-4o-mini",
                    }
                ],
            )
            env = _hermes_env(config)
        self.assertEqual({}, env)

    def test_env_contains_no_key_material_when_a_cloud_provider_precedes_ollama(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(
                tmp,
                [
                    {
                        "name": "openai",
                        "kind": "openai_compat",
                        "base_url": "https://api.example.com/v1",
                        "api_key": "${OPENAI_API_KEY}",
                        "model": "gpt-4o-mini",
                    },
                    {"name": "ollama", "kind": "ollama", "base_url": "http://localhost:11434", "model": "mistral:7b-instruct"},
                ],
            )
            env = _hermes_env(config)
        self.assertNotIn("api_key", env)
        self.assertNotIn("sk-test", json.dumps(env))


def _unavailable(events) -> bool:
    return any(isinstance(event, AgentStatus) and event.detail.startswith("Hermes unavailable:") for event in events)


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.terminate()
        await process.wait()


if __name__ == "__main__":
    unittest.main()
