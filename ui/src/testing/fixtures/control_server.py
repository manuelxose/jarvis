"""Test-only real serve_events boundary with a recording action callback.

The status listener is localhost-only and exists solely for Playwright assertions.
No production daemon or session token is logged. Never deploy this fixture.
"""
import asyncio
import json
import os
import signal
from pathlib import Path

from jarvis.observability.event_hub import EventHub
from jarvis.observability.event_stream import serve_events


async def main():
    actions = []
    failing = False
    hub = EventHub()

    async def dispatch(action):
        if failing:
            raise RuntimeError("fixture unavailable")
        actions.append(action)

    async def status(reader, writer):
        nonlocal failing
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            first = head.split(b"\r\n", 1)[0]
            if first == b"POST /fail HTTP/1.1":
                failing = True
            elif first == b"POST /recover HTTP/1.1":
                failing = False
            elif first != b"GET /status HTTP/1.1":
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            body = json.dumps({"actions": actions, "failing": failing}).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            await writer.drain()
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await serve_events(hub, os.environ["CONTROL_TEST_TOKEN"], static_dir=Path("ui/dist"), on_action=dispatch)
    probe = await asyncio.start_server(status, "127.0.0.1", 0)
    print(f"READY {server.sockets[0].getsockname()[1]} {probe.sockets[0].getsockname()[1]}", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await stop.wait()
    server.close()
    probe.close()
    await probe.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
