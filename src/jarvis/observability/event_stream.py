"""Live EventHub stream for the command-center UI: stdlib asyncio HTTP + Server-Sent Events on loopback.

``GET /events?token=...`` upgrades to ``text/event-stream`` and every valid hub
event is sent as ``data: <json>``. The per-launch token is compared in constant
time before any stream byte is written and is never logged. Each client owns a
bounded queue; when a slow client falls behind the oldest events are dropped
(and counted), so :meth:`EventHub.publish` never blocks or raises, even when
called from an audio thread. No third-party dependencies.

Run ``python -m jarvis.observability.event_stream --port 0 --token T --demo [--static ui/dist]``
to get a standalone server that publishes scripted valid events (used by the UI
transport tests); it prints ``READY <port>`` once bound.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import mimetypes
import os
import re
import signal
import sys
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import parse_qs, unquote, urlsplit

from jarvis.observability.event_hub import EventHub, hub

logger = logging.getLogger("jarvis.events.stream")

MAX_REQUEST_BYTES = 8192
REQUEST_TIMEOUT_SECONDS = 5.0
MAX_CONTROL_BODY_BYTES = 128
MAX_CLIENTS = 64
_ORIGIN_ALLOWED = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d{1,5})?$")
_REASONS = {400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 405: "Method Not Allowed", 503: "Service Unavailable"}


def _origin_allowed(origin: str) -> bool:
    return origin == "null" or bool(_ORIGIN_ALLOWED.match(origin))


async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str]]:
    """Return (method, target, lower-cased headers); raise ValueError on a malformed/oversized/slow request."""
    try:
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), REQUEST_TIMEOUT_SECONDS)
    except (asyncio.LimitOverrunError, asyncio.IncompleteReadError, asyncio.TimeoutError) as exc:
        raise ValueError(type(exc).__name__) from exc
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("request too large")
    lines = raw.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/"):
        raise ValueError("bad request line")
    headers: dict[str, str] = {}
    for line in lines[1:-2]:
        name, sep, value = line.partition(":")
        key = name.strip().lower()
        if not sep or not key or key in headers or any(c.isspace() for c in key):
            raise ValueError("invalid or duplicate header")
        headers[key] = value.strip()
    return parts[0], parts[1], headers


async def _respond(writer: asyncio.StreamWriter, status: int, origin: str = "") -> None:
    head = [f"HTTP/1.1 {status} {_REASONS[status]}", "Content-Length: 0", "Connection: close"]
    if origin and _origin_allowed(origin):
        head.append(f"Access-Control-Allow-Origin: {origin}")
    try:
        writer.write(("\r\n".join(head) + "\r\n\r\n").encode("ascii"))
        await asyncio.wait_for(writer.drain(), REQUEST_TIMEOUT_SECONDS)
    except (ConnectionError, asyncio.TimeoutError):
        pass


async def _respond_control(writer: asyncio.StreamWriter, status: int, message: str) -> None:
    body = json.dumps({"status": message}).encode("ascii")
    reasons = {202: "Accepted", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
               408: "Request Timeout", 503: "Service Unavailable", 504: "Gateway Timeout"}
    head = (f"HTTP/1.1 {status} {reasons[status]}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n")
    try:
        writer.write(head.encode("ascii") + body)
        await asyncio.wait_for(writer.drain(), REQUEST_TIMEOUT_SECONDS)
    except (ConnectionError, asyncio.TimeoutError):
        pass


_FORCED_TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css"}  # Windows registries can be wrong


def _resolve_static(static_dir: Optional[Path], url_path: str) -> Optional[Path]:
    """Map a request path to a file under *static_dir*; None when missing or escaping the directory."""
    if static_dir is None:
        return None
    relative = unquote(url_path).lstrip("/") or "index.html"
    if "\x00" in relative:
        return None
    try:
        root = static_dir.resolve()
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            return None
    except (OSError, ValueError):
        return None
    return candidate


async def _respond_file(writer: asyncio.StreamWriter, path: Path, origin: str = "") -> bool:
    """Send *path* with safe headers; False (nothing written) when it cannot be read."""
    try:
        body = await asyncio.to_thread(path.read_bytes)
    except OSError:
        return False
    content_type = _FORCED_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if content_type.startswith("text/"):
        content_type += "; charset=utf-8"
    head = [
        "HTTP/1.1 200 OK",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body)}",
        "X-Content-Type-Options: nosniff",
        "Connection: close",
    ]
    if path.name == "index.html":
        head.append("Cache-Control: no-store")
    if origin and _origin_allowed(origin):
        head.append(f"Access-Control-Allow-Origin: {origin}")
    try:
        writer.write(("\r\n".join(head) + "\r\n\r\n").encode("ascii") + body)
        await writer.drain()
    except ConnectionError:
        pass
    return True


async def serve_events(
    event_hub: EventHub,
    token: str,
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    keepalive_seconds: float = 15.0,
    queue_size: int = 256,
    static_dir: Optional[Path] = None,
    on_action: Optional[Callable[[str], Awaitable[None]]] = None,
) -> asyncio.AbstractServer:
    """Start the SSE server. Read the bound port from ``server.sockets[0].getsockname()[1]``.

    With *static_dir* set, other GET paths serve the built UI from it (no token: it holds no secrets).
    """
    loop = asyncio.get_running_loop()
    expected = token.encode("utf-8")
    bound_port = port
    clients = asyncio.Semaphore(MAX_CLIENTS)

    async def control(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, target: str, headers: dict[str, str]) -> None:
        def deny(reason: str) -> None:
            logger.warning("ui control denied: %s", reason)

        origin = headers.get("origin")
        if origin is not None and origin != f"http://{host}:{bound_port}":
            deny("origin")
            await _respond_control(writer, 403, "denied")
            return
        supplied = headers.get("x-jarvis-token", "").encode("latin-1")
        if not supplied or not hmac.compare_digest(supplied, expected):
            deny("authentication")
            await _respond_control(writer, 401, "denied")
            return
        if on_action is None:
            deny("unavailable")
            await _respond_control(writer, 503, "unavailable")
            return
        length = headers.get("content-length", "")
        if (target != "/control" or headers.get("content-type") != "application/json"
                or "transfer-encoding" in headers or not length.isascii() or not length.isdecimal()
                or len(length) > 3 or not 0 < int(length) <= MAX_CONTROL_BODY_BYTES):
            deny("request shape")
            await _respond_control(writer, 400, "invalid request")
            return
        try:
            body = await asyncio.wait_for(reader.readexactly(int(length)), REQUEST_TIMEOUT_SECONDS)
            def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                result: dict[str, Any] = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate JSON key")
                    result[key] = value
                return result

            data = json.loads(body.decode("utf-8"), object_pairs_hook=unique_pairs)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            deny("body timeout or disconnect")
            await _respond_control(writer, 408, "request timeout")
            return
        except (UnicodeError, ValueError):
            deny("invalid json")
            await _respond_control(writer, 400, "invalid request")
            return
        if not isinstance(data, dict) or set(data) != {"action"} or data["action"] not in ("activate", "sleep"):
            deny("unsupported action")
            await _respond_control(writer, 400, "invalid request")
            return
        action = data["action"]
        try:
            await asyncio.wait_for(on_action(action), REQUEST_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            deny("dispatch timeout")
            await _respond_control(writer, 504, "dispatch timeout")
        except Exception:  # noqa: BLE001 - never disclose callback errors to the client
            deny("dispatch failed")
            await _respond_control(writer, 503, "unavailable")
        else:
            logger.info("ui control accepted: %s", action)
            await _respond_control(writer, 202, "accepted")

    async def stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, origin: str) -> None:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)
        dropped = 0

        def put(event: dict[str, Any]) -> None:
            nonlocal dropped
            if queue.full():
                queue.get_nowait()
                dropped += 1
            queue.put_nowait(event)

        def on_event(event: dict[str, Any]) -> None:
            try:
                loop.call_soon_threadsafe(put, event)
            except RuntimeError:  # loop already closed: daemon is shutting down
                pass

        head = ["HTTP/1.1 200 OK", "Content-Type: text/event-stream", "Cache-Control: no-cache", "Connection: keep-alive"]
        if origin and _origin_allowed(origin):
            head.append(f"Access-Control-Allow-Origin: {origin}")
        writer.write(("\r\n".join(head) + "\r\n\r\n: connected\n\n").encode("ascii"))
        await writer.drain()
        unsubscribe = event_hub.subscribe(on_event)
        eof = asyncio.ensure_future(reader.read(1024))  # completes (b"") when the client goes away
        getter: Optional[asyncio.Future[dict[str, Any]]] = None
        try:
            while True:
                if getter is None:
                    getter = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait({getter, eof}, timeout=keepalive_seconds, return_when=asyncio.FIRST_COMPLETED)
                if eof in done:
                    return
                if getter in done:
                    event = getter.result()
                    getter = None
                    frame = f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
                else:
                    frame = ": keepalive\n\n"
                writer.write(frame.encode("utf-8"))
                await writer.drain()
        finally:
            unsubscribe()
            for task in (eof, getter):
                if task is not None:
                    task.cancel()
            logger.debug("events client disconnected (dropped %d on overflow)", dropped)
            if dropped:
                logger.info("events client dropped %d events on overflow", dropped)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if clients.locked():
            await _respond(writer, 503)
            writer.close()
            return
        await clients.acquire()
        try:
            try:
                method, target, headers = await _read_request(reader)
            except ValueError as exc:
                logger.debug("events request rejected: %s", exc)
                await _respond(writer, 400)
                return
            if target.split("?", 1)[0] == "/control":
                if method == "POST":
                    await control(reader, writer, target, headers)
                else:
                    await _respond(writer, 405)
                return
            origin = headers.get("origin", "")
            if method != "GET":
                await _respond(writer, 405)
                return
            url = urlsplit(target)
            if url.path != "/events":
                file = _resolve_static(static_dir, url.path)
                if file is None or not await _respond_file(writer, file, origin):
                    logger.debug("events static 404")
                    await _respond(writer, 404, origin)
                return
            supplied = parse_qs(url.query).get("token", [""])[0].encode("utf-8")
            if not supplied or not hmac.compare_digest(supplied, expected):
                logger.warning("events client rejected: bad or missing token")
                await _respond(writer, 401, origin)
                return
            logger.debug("events client connected")
            await stream(reader, writer, origin)
        except (ConnectionError, asyncio.CancelledError):
            pass
        except Exception:  # noqa: BLE001 - one bad client never takes the daemon down
            logger.debug("events connection failed", exc_info=True)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            clients.release()

    server = await asyncio.start_server(handle, host, port, limit=MAX_REQUEST_BYTES)
    bound_port = server.sockets[0].getsockname()[1]
    logger.info("events stream listening on %s:%d", host, server.sockets[0].getsockname()[1])
    return server


_DEMO_EVENTS: list[tuple[str, dict[str, Any]]] = [
    ("activation.started", {"source": "demo"}),
    ("voice.state", {"state": "listening", "reason": "demo"}),
    ("speech.started", {"trace_id": "demo", "source": "live"}),
    ("speech.completed", {"trace_id": "demo", "cancelled": False}),
    ("agent.started", {"trace_id": "demo", "route": "demo"}),
    ("agent.completed", {"trace_id": "demo", "route": "demo", "ok": True, "elapsed_ms": 12}),
    ("system.metrics", {"cpu_percent": 12.5, "ram_percent": 40.0, "gpu": None}),
]


async def _run(args: argparse.Namespace) -> None:
    server = await serve_events(hub, args.token, port=args.port, static_dir=Path(args.static) if args.static else None)
    print(f"READY {server.sockets[0].getsockname()[1]}", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, ValueError):  # Windows / non-main thread
            pass
    if not sys.stdin.isatty():  # a parent closing our stdin pipe is also a stop signal

        def watch_stdin() -> None:
            try:
                sys.stdin.buffer.read()
            except (OSError, ValueError):
                pass
            loop.call_soon_threadsafe(stop.set)

        threading.Thread(target=watch_stdin, daemon=True).start()

    async def demo() -> None:
        while True:
            for name, payload in _DEMO_EVENTS:
                hub.publish(name, **payload)
                await asyncio.sleep(0.2)

    task = asyncio.ensure_future(demo()) if args.demo else None
    try:
        await stop.wait()
    finally:
        if task is not None:
            task.cancel()
        server.close()  # no wait_closed: it would block on still-connected SSE clients


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Serve EventHub events over SSE on 127.0.0.1.")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token", required=True)
    parser.add_argument("--static", help="directory with the built UI to serve at /")
    parser.add_argument("--demo", action="store_true", help="publish a scripted loop of valid events")
    asyncio.run(_run(parser.parse_args(argv)))
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)  # the stdin watcher thread blocks in read(); normal shutdown would abort noisily
