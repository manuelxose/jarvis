"""Supervised Hermes child process adapter.

Provides both a ManagedComponent lifecycle (start/stop/health with bounded
restart) and the AgentRuntime ``respond`` stream. The child speaks the
structured JSON-lines protocol over stdio.
"""

from __future__ import annotations

import asyncio
import sys
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

    def __init__(
        self,
        command: Optional[list[str]] = None,
        *,
        timeout_seconds: float = 300.0,
        restart_max: int = 3,
    ) -> None:
        self._command = list(command) if command else default_command()
        self._timeout = timeout_seconds
        self._restart_max = restart_max
        self._process: Optional[asyncio.subprocess.Process] = None
        self._restarts = 0
        self._last_error = ""

    @property
    def configured(self) -> bool:
        return bool(self._command)

    async def start(self) -> None:
        await self._spawn()

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
                "Hermes",
                HealthStatus.DEGRADED,
                "Hermes command not configured; agent workflows unavailable",
                required=False,
            )
        if self._process is not None and self._process.returncode is None:
            return HealthReport("Hermes", HealthStatus.HEALTHY)
        return HealthReport(
            "Hermes",
            HealthStatus.DEGRADED,
            self._last_error or "Hermes child not running",
            required=False,
            retryable=True,
        )

    async def _spawn(self) -> None:
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, FileNotFoundError) as error:
            self._last_error = f"failed to start Hermes child: {error}"
            raise HermesError(self._last_error) from error

    async def _recover(self) -> None:
        await self.stop()
        if self._restarts < self._restart_max:
            self._restarts += 1
            await self._spawn()

    async def respond(self, text: str, context: TurnContext) -> AsyncIterator:
        if self._process is None or self._process.returncode is not None:
            if not self.configured:
                yield AgentStatus("Hermes unavailable: not configured")
                return
            await self._recover()
        process = self._process
        assert process is not None
        assert process.stdin is not None
        assert process.stdout is not None

        request_id = protocol.new_request_id()
        process.stdin.write(
            (
                protocol.encode_message(
                    request_id, context.trace_id, protocol.REQUEST, {"text": text}
                )
                + "\n"
            ).encode("utf-8")
        )
        await process.stdin.drain()

        try:
            while True:
                if context.cancellation.cancelled:
                    await self._send_cancel(process, request_id, context.trace_id)
                    raise TurnCancelled()
                line = await asyncio.wait_for(
                    process.stdout.readline(), timeout=self._timeout
                )
                if not line:
                    raise HermesError("Hermes child exited mid-response")
                message = protocol.parse_message(line.decode("utf-8", "replace"))
                if message is None or message.request_id != request_id:
                    continue
                event = _to_agent_event(message)
                if event is None:
                    continue
                yield event
                if message.event_type in (protocol.COMPLETED, protocol.FAILED, protocol.CANCELLED):
                    return
        except asyncio.TimeoutError as error:
            self._last_error = "Hermes child timed out"
            raise HermesError(self._last_error) from error
        except (BrokenPipeError, ConnectionError) as error:
            self._last_error = f"Hermes child crashed: {error}"
            raise HermesError(self._last_error) from error

    async def _send_cancel(self, process, request_id: str, turn_id: str) -> None:
        if process.stdin is None:
            return
        try:
            process.stdin.write(
                (protocol.encode_message(request_id, turn_id, protocol.CANCEL, {}) + "\n").encode("utf-8")
            )
            await process.stdin.drain()
        except Exception:
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
