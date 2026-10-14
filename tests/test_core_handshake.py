import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.apps import daemon
from jarvis.observability.event_hub import VERSION, EventHub
from jarvis.observability.event_stream import serve_events

TOKEN = "handshake-token"


class _Stub:
    def __init__(self, endpoint):
        self.ui_endpoint = endpoint


async def _line(port, command):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(command + b"\n")
    await writer.drain()
    data = await asyncio.wait_for(reader.readline(), 3)
    writer.close()
    return json.loads(data)


async def _head(port, target):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
    await writer.drain()
    head = b""
    while not head.endswith(b"\r\n\r\n"):
        chunk = await asyncio.wait_for(reader.read(1), 3)
        if not chunk:
            break
        head += chunk
    writer.close()
    return head.decode("ascii")


class CoreHandshakeTests(unittest.IsolatedAsyncioTestCase):
    async def _serve(self, endpoint):
        server = await daemon.serve(_Stub(endpoint), 0)
        self.addCleanup(self._close, server)
        return server.sockets[0].getsockname()[1]

    async def _close(self, server):
        server.close()
        await server.wait_closed()

    async def test_ui_reply_advertises_protocol(self):
        endpoint = {"url": "http://127.0.0.1:1/events", "token": TOKEN, "app_url": "http://127.0.0.1:1/"}
        port = await self._serve(endpoint)
        reply = await _line(port, b"ui")
        self.assertIs(reply["ok"], True)
        self.assertEqual(reply["protocol"], VERSION)
        self.assertEqual(reply["token"], TOKEN)

    async def test_ui_reply_disabled_has_no_protocol_or_token(self):
        port = await self._serve(None)
        reply = await _line(port, b"ui")
        self.assertIs(reply["ok"], False)
        self.assertNotIn("protocol", reply)
        self.assertNotIn("token", reply)

    async def test_events_head_has_protocol_header_only_when_authorized(self):
        server = await serve_events(EventHub(), TOKEN, port=0)
        self.addAsyncCleanup(self._close, server)
        port = server.sockets[0].getsockname()[1]
        ok = await _head(port, f"/events?token={TOKEN}")
        self.assertTrue(ok.startswith("HTTP/1.1 200"))
        self.assertIn(f"X-Jarvis-Protocol: {VERSION}", ok)
        bad = await _head(port, "/events?token=wrong")
        self.assertTrue(bad.startswith("HTTP/1.1 401"))
        self.assertNotIn("X-Jarvis-Protocol", bad)


if __name__ == "__main__":
    unittest.main()
