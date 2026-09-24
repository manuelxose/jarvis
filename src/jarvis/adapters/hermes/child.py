"""Supervised Hermes child process adapter.

Provides both a ManagedComponent lifecycle (start/stop/health with bounded
restart) and the AgentRuntime ``respond`` stream. The child speaks the
structured JSON-lines protocol over stdio.
"""

from __future__ import annotations

import asyncio
import subprocess
from contextlib import suppress
import os
import sys
import time
from typing import AsyncIterator, Optional

from jarvis.core.contracts import (
    AgentRuntime,
    AgentStatus,
    AgentToken,
    AgentToolRequest,
    HealthReport,
    HealthStatus,
    TurnContext,
)
from jarvis.core.errors import HermesError
from jarvis.core.turn import TurnCancelled

from . import protocol


def default_command() -> list[str]:
    return [sys.executable, "-m", "jarvis.adapters.hermes.agent_child"]


class HermesChildAdapter:
    """Manage and stream from a single supervised Hermes child process."""

    name = "Hermes"
    required = False

    def __init__(
        self,
        command: Optional[list[str]] = None,
        *,
        timeout_seconds: float = 300.0,
        restart_max: int = 3,
        env: Optional[dict] = None,
    ) -> None:
        self._command = default_command() if command is None else list(command)
        self._timeout = timeout_seconds
        self._restart_max = max(0, restart_max)
        self._env = env
        self._process: Optional[asyncio.subprocess.Process] = None
        self._restarts = 0
        self._last_error = ""
        self._restart_needed = False
        self._request_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._command)

    async def start(self) -> None:
        if self._process is not None and self._process.returncode is None:
            return
        await self._spawn()
        self._restart_needed = False

    async def stop(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

    async def health(self) -> HealthReport:
        if not self.configured:
            return HealthReport(
                self.name,
                HealthStatus.DEGRADED,
                "Hermes command not configured; agent workflows unavailable",
                required=self.required,
            )
        if self._process is not None and self._process.returncode is None:
            return HealthReport(self.name, HealthStatus.HEALTHY, required=self.required)
        return HealthReport(
            self.name,
            HealthStatus.DEGRADED,
            self._last_error or "Hermes child not running",
            required=self.required,
            retryable=True,
        )

    async def _spawn(self) -> None:
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # no console window on Windows
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                # Avoid an unconsumed stderr pipe blocking a noisy child.
                stderr=asyncio.subprocess.DEVNULL,
                env={**os.environ, **(self._env or {})},
            )
        except (OSError, FileNotFoundError) as error:
            self._last_error = f"failed to start Hermes child: {error}"
            raise HermesError(self._last_error) from error

    async def _ensure_process(self) -> bool:
        process = self._process
        if process is not None and process.returncode is None:
            return True
        if not self.configured:
            self._last_error = "Hermes command not configured"
            return False
        if process is not None or self._restart_needed:
            return await self._recover()
        try:
            await self._spawn()
        except HermesError:
            return False
        return True

    async def _recover(self) -> bool:
        await self.stop()
        if self._restarts >= self._restart_max:
            self._last_error = "Hermes restart budget exhausted"
            return False
        self._restarts += 1
        try:
            await self._spawn()
        except HermesError:
            return False
        self._restart_needed = False
        return True

    async def respond(self, text: str, context: TurnContext) -> AsyncIterator:
        """Stream one serialized child request, degrading on child failure."""
        async with self._request_lock:
            if not await self._ensure_process():
                yield AgentStatus(f"Hermes unavailable: {self._last_error}")
                return

            process = self._process
            assert process is not None
            assert process.stdin is not None
            assert process.stdout is not None
            request_id = protocol.new_request_id()
            try:
                process.stdin.write(
                    (
                        protocol.encode_message(
                            request_id, context.trace_id, protocol.REQUEST, {"text": text}
                        )
                        + "\n"
                    ).encode("utf-8")
                )
                await process.stdin.drain()

                while True:
                    line = await self._read_line(process, request_id, context)
                    if not line:
                        raise HermesError("Hermes child exited mid-response")
                    message = protocol.parse_message(line.decode("utf-8", "replace"))
                    if message is None or message.request_id != request_id:
                        continue
                    event = _to_agent_event(message)
                    if event is not None:
                        yield event
                    if message.event_type in (
                        protocol.COMPLETED,
                        protocol.FAILED,
                        protocol.CANCELLED,
                    ):
                        return
            except TurnCancelled:
                self._last_error = "Hermes turn cancelled"
                self._restart_needed = False
                await self.stop()
                raise
            except (asyncio.TimeoutError, BrokenPipeError, ConnectionError, OSError, HermesError) as error:
                self._last_error = str(error) or "Hermes child unavailable"
                self._restart_needed = True
                await self.stop()
                yield AgentStatus(f"Hermes unavailable: {self._last_error}")

    async def _read_line(
        self,
        process: asyncio.subprocess.Process,
        request_id: str,
        context: TurnContext,
    ) -> bytes:
        """Wait for one line while honoring cancellation and the turn deadline."""
        assert process.stdout is not None
        reader_task = asyncio.create_task(process.stdout.readline())
        deadline = min(time.monotonic() + self._timeout, context.deadline_monotonic)
        try:
            while True:
                if context.cancellation.cancelled:
                    await self._send_cancel(process, request_id, context.trace_id)
                    raise TurnCancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                done, _ = await asyncio.wait(
                    {reader_task}, timeout=min(remaining, 0.05)
                )
                if done:
                    return reader_task.result()
        finally:
            if not reader_task.done():
                reader_task.cancel()
                with suppress(asyncio.CancelledError):
                    await reader_task

    async def _send_cancel(self, process, request_id: str, turn_id: str) -> None:
        if process.stdin is None:
            return
        try:
            process.stdin.write(
                (protocol.encode_message(request_id, turn_id, protocol.CANCEL, {}) + "\n").encode("utf-8")
            )
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionError, OSError):
            pass


def _to_agent_event(message) -> Optional[object]:
    event_type = message.event_type
    payload = message.payload
    if event_type == protocol.PARTIAL_RESPONSE:
        text = payload.get("text", "")
        return AgentToken(text) if text else None
    if event_type in (protocol.TOOL_STARTED, protocol.TOOL_COMPLETED):
        name = payload.get("name", "")
        if name:
            return AgentToolRequest(name, payload.get("arguments") or {})
    if event_type in (protocol.STARTED, protocol.THINKING, protocol.COMPLETED, protocol.FAILED):
        return AgentStatus(payload.get("detail", event_type))
    return None
