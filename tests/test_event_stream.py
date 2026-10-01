"""Token-protected SSE stream of EventHub events on loopback."""

import asyncio
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.observability.event_hub import EventHub
from jarvis.observability.event_stream import MAX_REQUEST_BYTES, MAX_CONTROL_BODY_BYTES, serve_events

TOKEN = "s3cret-token"


async def http_get(port, target, *, method="GET", origin=None, extra=b""):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    head = f"{method} {target} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
    if origin:
        head += f"Origin: {origin}\r\n"
    writer.write(head.encode() + extra + b"\r\n")
    await writer.drain()
    return reader, writer


async def read_head(reader):
    raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
    lines = raw.decode().split("\r\n")
    headers = {k.lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:] if line)}
    return int(lines[0].split(" ")[1]), headers


async def read_events(reader, count, timeout=3):
    """Collect *count* ``data:`` frames, skipping comment frames."""
    events = []

    async def collect():
        while len(events) < count:
            frame = (await reader.readuntil(b"\n\n")).decode().strip()
            if frame.startswith("data: "):
                events.append(json.loads(frame[6:]))

    await asyncio.wait_for(collect(), timeout)
    return events


class EventStreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = EventHub()
        self.server = None
        self.writers = []

    async def asyncTearDown(self):
        for writer in self.writers:
            writer.close()
        if self.server:
            self.server.close()

    async def start(self, **kwargs):
        self.server = await serve_events(self.hub, TOKEN, **kwargs)
        return self.server.sockets[0].getsockname()[1]

    async def get(self, port, target, **kwargs):
        reader, writer = await http_get(port, target, **kwargs)
        self.writers.append(writer)
        return reader, writer

    async def wait_hub(self, active, timeout=2.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while self.hub.active != active and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.02)
        return self.hub.active

    async def test_valid_token_streams_published_event(self):
        port = await self.start()
        reader, _ = await self.get(port, f"/events?token={TOKEN}")
        status, headers = await read_head(reader)
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "text/event-stream")
        self.assertEqual(await reader.readuntil(b"\n\n"), b": connected\n\n")
        self.assertTrue(await self.wait_hub(True))
        self.hub.publish("voice.state", state="listening", reason="test")
        (event,) = await read_events(reader, 1)
        self.assertEqual((event["name"], event["v"]), ("voice.state", 1))
        self.assertEqual(event["state"], "listening")

    async def test_missing_or_wrong_token_is_401_and_never_subscribes(self):
        port = await self.start()
        for target in ("/events", "/events?token=", "/events?token=wrong", f"/events?token={TOKEN}x"):
            reader, _ = await self.get(port, target)
            status, _ = await read_head(reader)
            self.assertEqual(status, 401, target)
            self.assertEqual(await reader.read(), b"")
            self.assertFalse(self.hub.active)

    async def test_wrong_path_404_and_post_405(self):
        port = await self.start()
        reader, _ = await self.get(port, f"/other?token={TOKEN}")
        self.assertEqual((await read_head(reader))[0], 404)
        reader, _ = await self.get(port, f"/events?token={TOKEN}", method="POST")
        self.assertEqual((await read_head(reader))[0], 405)
        self.assertFalse(self.hub.active)

    def static_dir(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "dist" / "assets").mkdir(parents=True)
        (root / "dist" / "index.html").write_text("<h1>jarvis</h1>")
        (root / "dist" / "assets" / "app.js").write_text("console.log(1)")
        (root / "secret").write_text("top secret")
        return root / "dist"

    async def test_static_serves_index_and_assets_without_token(self):
        port = await self.start(static_dir=self.static_dir())
        reader, _ = await self.get(port, "/")
        status, headers = await read_head(reader)
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/html"))
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(await reader.read(), b"<h1>jarvis</h1>")
        self.assertEqual(int(headers["content-length"]), len(b"<h1>jarvis</h1>"))
        reader, _ = await self.get(port, "/assets/app.js")
        status, headers = await read_head(reader)
        self.assertEqual((status, headers["content-type"].split(";")[0]), (200, "text/javascript"))
        self.assertEqual(await reader.read(), b"console.log(1)")
        self.assertFalse(self.hub.active)

    async def test_static_rejects_traversal_missing_and_directories(self):
        port = await self.start(static_dir=self.static_dir())
        for target in ("/../secret", "/%2e%2e/secret", "/assets/../../secret", "/assets/%2e%2e/%2e%2e/secret", "/missing.js", "/assets", "/%00"):
            reader, _ = await self.get(port, target)
            self.assertEqual((await read_head(reader))[0], 404, target)
            self.assertNotIn(b"top secret", await reader.read())

    async def test_static_missing_dir_and_no_static_dir_are_404(self):
        for kwargs in ({}, {"static_dir": Path(tempfile.gettempdir()) / "jarvis-no-such-dist"}):
            port = await self.start(**kwargs)
            reader, _ = await self.get(port, "/")
            self.assertEqual((await read_head(reader))[0], 404)
            self.server.close()

    async def test_static_does_not_weaken_events_token_or_methods(self):
        port = await self.start(static_dir=self.static_dir())
        reader, _ = await self.get(port, "/events")
        self.assertEqual((await read_head(reader))[0], 401)
        reader, _ = await self.get(port, "/", method="POST")
        self.assertEqual((await read_head(reader))[0], 405)

    async def test_oversized_header_and_garbage_get_400(self):
        port = await self.start()
        reader, _ = await self.get(port, f"/events?token={TOKEN}", extra=b"X-Pad: " + b"a" * (MAX_REQUEST_BYTES + 100) + b"\r\n")
        self.assertEqual((await read_head(reader))[0], 400)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        self.writers.append(writer)
        writer.write(b"not http\r\n\r\n")
        self.assertEqual((await read_head(reader))[0], 400)
        self.assertFalse(self.hub.active)

    async def test_cors_origin_allowlist(self):
        port = await self.start()
        for origin in ("http://localhost:4173", "http://127.0.0.1:5173", "http://localhost", "null"):
            reader, _ = await self.get(port, f"/events?token={TOKEN}", origin=origin)
            _, headers = await read_head(reader)
            self.assertEqual(headers.get("access-control-allow-origin"), origin)
        for origin in ("http://evil.example", "http://localhost.evil.example", "https://localhost:4173"):
            reader, _ = await self.get(port, f"/events?token={TOKEN}", origin=origin)
            _, headers = await read_head(reader)
            self.assertNotIn("access-control-allow-origin", headers, origin)
        reader, _ = await self.get(port, "/events?token=bad", origin="http://evil.example")
        _, headers = await read_head(reader)
        self.assertNotIn("access-control-allow-origin", headers)

    async def test_publish_from_another_thread_is_delivered(self):
        port = await self.start()
        reader, _ = await self.get(port, f"/events?token={TOKEN}")
        await read_head(reader)
        self.assertTrue(await self.wait_hub(True))
        thread = threading.Thread(target=lambda: self.hub.publish("voice.loading", reason="thread"))
        thread.start()
        thread.join()
        (event,) = await read_events(reader, 1)
        self.assertEqual((event["name"], event["reason"]), ("voice.loading", "thread"))

    async def test_client_close_unsubscribes(self):
        port = await self.start()
        reader, writer = await self.get(port, f"/events?token={TOKEN}")
        await read_head(reader)
        self.assertTrue(await self.wait_hub(True))
        writer.close()
        self.assertFalse(await self.wait_hub(False, 2.0))

    async def test_slow_client_drops_oldest_and_keeps_newest(self):
        port = await self.start(queue_size=2)
        reader, _ = await self.get(port, f"/events?token={TOKEN}")
        await read_head(reader)
        self.assertTrue(await self.wait_hub(True))
        for i in range(50):
            self.hub.publish("voice.loading", reason=str(i))  # never raises or blocks
        await asyncio.sleep(0.2)
        events = []
        while True:
            try:
                events += await read_events(reader, 1, timeout=0.3)
            except asyncio.TimeoutError:
                break
        self.assertTrue(0 < len(events) <= 2 + 1, len(events))
        self.assertEqual(events[-1]["reason"], "49")

    async def test_invalid_event_is_never_streamed(self):
        port = await self.start()
        reader, _ = await self.get(port, f"/events?token={TOKEN}")
        await read_head(reader)
        self.assertTrue(await self.wait_hub(True))
        self.assertIsNone(self.hub.publish("not.an.event", x=1))
        self.assertIsNone(self.hub.publish("voice.state", state=3, reason="bad"))
        self.hub.publish("voice.loading", reason="ok")
        (event,) = await read_events(reader, 1)
        self.assertEqual(event["name"], "voice.loading")

    async def test_keepalive_comment_when_idle(self):
        port = await self.start(keepalive_seconds=0.1)
        reader, _ = await self.get(port, f"/events?token={TOKEN}")
        await read_head(reader)
        await reader.readuntil(b"\n\n")
        self.assertEqual(await asyncio.wait_for(reader.readuntil(b"\n\n"), 2), b": keepalive\n\n")


class ControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.actions = []

        async def dispatch(action):
            self.actions.append(action)

        self.dispatch = dispatch
        self.server = await serve_events(EventHub(), TOKEN, on_action=dispatch)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def request(self, body=b'{"action":"activate"}', *, token=TOKEN, origin="same", method="POST", path="/control", headers=b"", length=None):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        origin = f"http://127.0.0.1:{self.port}" if origin == "same" else origin
        head = (f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                + (f"Origin: {origin}\r\n" if origin is not None else "")
                + (f"X-Jarvis-Token: {token}\r\n" if token is not None else "")
                + "Content-Type: application/json\r\n"
                + f"Content-Length: {len(body) if length is None else length}\r\n").encode() + headers + b"\r\n"
        writer.write(head + body)
        await writer.drain()
        if length is not None and isinstance(length, int) and length > len(body) and length <= MAX_CONTROL_BODY_BYTES:
            writer.write_eof()  # truncated upload: server must not wait for its full timeout
        status, response_headers = await read_head(reader)
        response = await reader.read()
        writer.close()
        await writer.wait_closed()
        self.assertNotIn("access-control-allow-origin", response_headers)
        self.assertNotIn(TOKEN.encode(), response)
        return status, response

    async def test_valid_actions_dispatch_exactly_once_and_return_accepted(self):
        for action in ("activate", "sleep"):
            status, body = await self.request(json.dumps({"action": action}).encode())
            self.assertEqual(status, 202)
            self.assertEqual(json.loads(body), {"status": "accepted"})
        self.assertEqual(self.actions, ["activate", "sleep"])
        self.assertEqual((await self.request(origin=None))[0], 202)  # authenticated non-browser client

    async def test_invalid_requests_never_dispatch(self):
        cases = [
            {"token": None}, {"token": "wrong"}, {"token": TOKEN, "headers": b"X-Jarvis-Token: duplicate\r\n"},
            {"origin": "null"}, {"origin": "http://evil.example"}, {"origin": "http://localhost:9999"},
            {"body": b'{"action":"restart"}'}, {"body": b'{"action":"activate","extra":1}'},
            {"body": b'{"action":"activate","action":"restart"}'},
            {"body": b'{"action":"activate","action":"activate"}'},
            {"body": b'{"action":true}'}, {"body": b'{"action":null}'},
            {"headers": b"Origin: null\r\n"},
            {"body": b'{"action":'}, {"body": b'[]'},
            {"body": b'x' * (MAX_CONTROL_BODY_BYTES + 1)},
            {"length": "9" * 5000}, {"length": 0}, {"length": MAX_CONTROL_BODY_BYTES + 1},
            {"headers": b"Transfer-Encoding: chunked\r\n"},
            {"headers": b"Content-Type: application/json\r\n"},
            {"headers": b"X-Extra: " + b"x" * MAX_REQUEST_BYTES + b"\r\n"},
            {"path": "/control?anything=1"}, {"path": "/control/"}, {"method": "PUT"},
        ]
        for case in cases:
            with self.subTest(case=case):
                status, _ = await self.request(**case)
                self.assertNotEqual(status, 202)
        self.assertEqual(self.actions, [])

    async def test_short_body_and_failure_do_not_claim_acceptance(self):
        self.assertEqual((await self.request(body=b"{}", length=20))[0], 408)
        self.assertEqual(self.actions, [])
        self.server.close()
        await self.server.wait_closed()

        async def failing(action):
            raise RuntimeError("secret diagnostics")

        self.server = await serve_events(EventHub(), TOKEN, on_action=failing)
        self.port = self.server.sockets[0].getsockname()[1]
        status, body = await self.request()
        self.assertEqual(status, 503)
        self.assertNotIn(b"secret diagnostics", body)

    async def test_dispatch_timeout_and_disabled_route(self):
        self.server.close()
        await self.server.wait_closed()

        async def stuck(action):
            await asyncio.sleep(1)

        from unittest import mock
        with mock.patch("jarvis.observability.event_stream.REQUEST_TIMEOUT_SECONDS", 0.05):
            self.server = await serve_events(EventHub(), TOKEN, on_action=stuck)
            self.port = self.server.sockets[0].getsockname()[1]
            self.assertEqual((await self.request())[0], 504)
        self.server.close()
        await self.server.wait_closed()
        self.server = await serve_events(EventHub(), TOKEN)
        self.port = self.server.sockets[0].getsockname()[1]
        self.assertEqual((await self.request())[0], 503)
        self.assertEqual(self.actions, [])


if __name__ == "__main__":
    unittest.main()
