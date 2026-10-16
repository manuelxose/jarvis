"""Test-only serve_events boundary fed by a real CoreSignals producer.

Playwright drives the producer through a localhost-only step listener
(POST /turn-start, /playback, /cancel, /end, /activate, /clap, /startup/<phase>[?partial=1],
/apps, /cancel-startup, /cleanup; GET /status, whose `triggers` counts step calls only). No PCM, no audio
device and no production token logging. Never deploy this fixture.
"""
import asyncio
import json
import os
import signal
from pathlib import Path

from jarvis.observability.core_signals import CoreSignals
from jarvis.observability.event_hub import EventHub
from jarvis.observability.event_stream import serve_events


async def main():
    hub = EventHub()
    signals = CoreSignals(hub)
    turns = 0
    triggers = 0
    pump_task = None

    async def pump(turn: str) -> None:
        # Real playback writes a slice every ~40 ms; a single output sample goes stale (250 ms) on a slow host.
        while True:
            await asyncio.sleep(0.1)
            signals.output(turn, 0.5, 0.6)

    def stop_pump() -> None:
        nonlocal pump_task
        if pump_task is not None:
            pump_task.cancel()
            pump_task = None

    def startup_step(route: str, query: str) -> bool:
        if route == "/activate":
            return signals.begin_activation() > 0
        if route == "/clap":
            return signals.candidate("clap-1", True, 2000)
        if route.startswith("/startup/"):
            return signals.startup(route[len("/startup/"):], partial="partial=1" in query.split("&"))
        if route == "/apps":
            return signals.apps([("spotify", "launched"), ("browser", "failed")])
        if route == "/cancel-startup":
            return signals.startup("cancelled")
        if route == "/cleanup":
            return signals.cleaned_up()
        return False

    def step(route: str) -> bool:
        nonlocal turns, pump_task
        if route in ("/turn-start", "/cancel", "/end"):
            stop_pump()
        if route == "/turn-start":
            turns += 1
            return signals.turn_start(f"turn-{turns}")
        turn = f"turn-{turns}"
        if route == "/playback":
            ok = signals.output(turn, 0.5, 0.6)
            if ok and pump_task is None:
                pump_task = asyncio.get_running_loop().create_task(pump(turn))
            return ok
        if route == "/cancel":
            return signals.cancel_turn(turn)
        if route == "/end":
            ended = signals.playback_end(turn, "ended")
            return signals.turn_end(turn, True) or ended
        return False

    async def listener(reader, writer):
        nonlocal triggers
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            method, target, _ = head.split(b"\r\n", 1)[0].decode().split(" ", 2)
            route, _, query = target.partition("?")
            if method == "GET" and route == "/status":
                body = json.dumps({**signals.snapshot()["facts"], "triggers": triggers}).encode()
            elif method == "POST" and route in ("/turn-start", "/playback", "/cancel", "/end"):
                triggers += 1
                body = json.dumps({"ok": step(route)}).encode()
            elif method == "POST" and (route in ("/activate", "/clap", "/apps", "/cancel-startup", "/cleanup") or route.startswith("/startup/")):
                triggers += 1
                body = json.dumps({"ok": startup_step(route, query)}).encode()
            else:
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            await writer.drain()
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await serve_events(hub, os.environ["CORE_TEST_TOKEN"], static_dir=Path("ui/dist"), snapshot=signals.snapshot)
    probe = await asyncio.start_server(listener, "127.0.0.1", 0)
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
