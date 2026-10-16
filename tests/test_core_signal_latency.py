"""Loopback transport latency of core.signal output events (single process, one monotonic clock)."""

import asyncio
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.observability.core_signals import CoreSignals
from jarvis.observability.event_hub import EventHub
from jarvis.observability.event_stream import serve_events

TOKEN = "latency-token"
N = 200
P95_BOUND_MS = 50.0


class CoreSignalLatencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_to_receive_latency_over_loopback(self):
        hub = EventHub()
        signals = CoreSignals(hub)
        server = await serve_events(hub, TOKEN, port=0, snapshot=signals.snapshot)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(f"GET /events?token={TOKEN} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
            await writer.drain()
            await reader.readuntil(b"\r\n\r\n")
            await reader.readuntil(b"\n\n")  # : connected
            first = json.loads((await reader.readuntil(b"\n\n")).decode().strip()[6:])
            self.assertEqual(first["name"], "core.snapshot")
            sent: dict[int, float] = {}
            hub.subscribe(lambda e: sent.setdefault(e["seq"], time.monotonic()) if e["name"] == "core.signal" else None)
            latencies: list[float] = []

            async def receive() -> None:
                while len(latencies) < N:
                    frame = (await reader.readuntil(b"\n\n")).decode().strip()
                    if not frame.startswith("data: "):
                        continue
                    event = json.loads(frame[6:])
                    if event["name"] == "core.signal" and event["kind"] == "output":
                        latencies.append((time.monotonic() - sent[event["seq"]]) * 1000.0)

            task = asyncio.ensure_future(receive())
            for _ in range(N):
                self.assertTrue(signals.output("t1", 0.4, 0.5))
                await asyncio.sleep(0.005)
            await asyncio.wait_for(task, 5)
        finally:
            writer.close()
            server.close()
        latencies.sort()
        p50, p95, worst = latencies[len(latencies) // 2], latencies[int(len(latencies) * 0.95) - 1], latencies[-1]
        print(f"core.signal loopback latency n={len(latencies)} p50={p50:.2f}ms p95={p95:.2f}ms max={worst:.2f}ms")
        self.assertEqual(len(latencies), N)
        self.assertLess(p95, P95_BOUND_MS)


if __name__ == "__main__":
    unittest.main()
