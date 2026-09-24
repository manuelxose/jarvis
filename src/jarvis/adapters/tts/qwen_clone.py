"""Local voice-cloning TTS backed by the supervised Faster Qwen3-TTS worker.

The model lives in a child process (``qwen_worker.py``) with its own venv:
faster-qwen3-tts needs transformers 5.x while Jarvis pins 4.x, and a separate
process also isolates CUDA crashes/OOM from the voice loop. The worker keeps
the model resident and warmed; this adapter supervises it (bounded restarts,
explicit degraded health) and streams its audio as WAV chunks.

Stale-audio guarantee: every segment is a request with a unique id; events
for any id other than the active request are dropped, and a cancelled turn
sends ``cancel`` so the worker stops generating within one chunk.
"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, AsyncIterator, Optional
import uuid
import wave

from jarvis.core.contracts import HealthReport, HealthStatus, TurnContext
from jarvis.core.errors import ProviderConfigError, ProviderError, ProviderUnavailable
from jarvis.core.turn import TurnCancelled

WORKER_SCRIPT = Path(__file__).with_name("qwen_worker.py")
_LANGUAGES = {"es": "Spanish", "en": "English", "fr": "French", "de": "German", "it": "Italian", "pt": "Portuguese"}


def default_worker_python() -> str:
    """Interpreter of the isolated TTS venv (``%LOCALAPPDATA%\\jarvis\\venv-tts``)."""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(Path.home(), ".local", "share")
    scripts = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    return str(Path(base) / "jarvis" / "venv-tts" / scripts)


def worker_command(
    python: str, *, profile_dir: str, language: str = "es", chunk_size: int = 4, model: str = ""
) -> list[str]:
    """Command line that starts ``qwen_worker.py serve`` for a voice profile."""
    command = [
        python, str(WORKER_SCRIPT), "serve",
        "--profile-dir", profile_dir,
        "--language", _LANGUAGES.get(language, language),
        "--chunk-size", str(chunk_size),
    ]
    return command + (["--model", model] if model else [])


def pcm_to_wav(pcm: bytes, rate: int) -> bytes:
    """Wrap mono 16-bit PCM in a WAV container."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


class QwenCloneTTS:
    """TextToSpeech + ManagedComponent for the cloned owner voice."""

    name = "qwen_clone"
    required = False

    def __init__(
        self,
        command: list[str],
        *,
        event_timeout_seconds: float = 15.0,
        restart_max: int = 3,
        restart_backoff_seconds: float = 2.0,
        stderr_path: Optional[str] = None,
        warmup_wait_seconds: float = 0.0,
    ) -> None:
        self._command = list(command)
        # >0: a turn arriving while the model is still loading waits for the
        # owner's voice instead of falling back to SAPI at once.
        self._warmup_wait = max(0.0, warmup_wait_seconds)
        self._event_timeout = event_timeout_seconds
        self._restart_max = max(0, restart_max)
        self._backoff = restart_backoff_seconds
        self._stderr_path = stderr_path
        self._process: Optional[asyncio.subprocess.Process] = None
        self._reader: Optional[asyncio.Task] = None
        self._restart_task: Optional[asyncio.Task] = None
        self._ready: Optional[asyncio.Future] = None
        self._queues: dict[str, asyncio.Queue] = {}
        self._info: dict[str, Any] = {}
        self._restarts = 0
        self._stopping = False
        self._last_error = ""
        self._timings: deque[dict] = deque(maxlen=50)

    # -- lifecycle -----------------------------------------------------------------

    async def start(self) -> None:
        """Spawn the worker without waiting for the model to load."""
        if self._process is not None and self._process.returncode is None:
            return
        self._stopping = False
        await self._spawn()

    async def wait_ready(self) -> dict:
        while True:
            future = self._ready
            if future is None:
                raise ProviderUnavailable("voice clone worker not started", provider=self.name)
            if await asyncio.shield(future):
                return dict(self._info)
            if self._restart_task is not None and not self._restart_task.done():
                await self._restart_task
                continue
            raise ProviderUnavailable(f"voice clone worker failed: {self._last_error}", provider=self.name)

    async def stop(self) -> None:
        self._stopping = True
        if self._restart_task is not None:
            self._restart_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._restart_task
            self._restart_task = None
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            with suppress(Exception):
                process.stdin.write(b'{"op":"exit"}\n')
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                _kill_tree(process)
                with suppress(Exception):
                    await asyncio.wait_for(process.wait(), timeout=5.0)
        if self._reader is not None:
            self._reader.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None
        self._fail_pending()
        if self._ready is not None and not self._ready.done():
            self._ready.set_result(False)

    async def health(self) -> HealthReport:
        ready = self._ready is not None and self._ready.done() and self._ready.result()
        alive = self._process is not None and self._process.returncode is None
        if ready and alive and self._info.get("profile"):
            detail = "cloned voice {profile} ({mode}) ready, {vram_free_mb} MiB VRAM free".format(
                **{"vram_free_mb": "?", **self._info}
            )
            return HealthReport("TTS voice clone", HealthStatus.HEALTHY, detail, required=False)
        if ready and alive:
            detail = "no voice profile enrolled; using fallback voice (run: python -m jarvis.voice_profile record)"
        elif alive:
            detail = "voice clone worker warming up; using fallback voice"
        else:
            detail = f"voice clone unavailable ({self._last_error or 'not started'}); using fallback voice"
        # Only a dead worker is worth restarting: the supervisor retrying a worker
        # that is merely loading its model kills it mid-load (seconds lost per retry).
        return HealthReport("TTS voice clone", HealthStatus.DEGRADED, detail, required=False, retryable=not alive)

    @property
    def ready(self) -> bool:
        """Model loaded with an enrolled profile: audio will be the owner's voice."""
        alive = self._process is not None and self._process.returncode is None
        return bool(alive and self._ready is not None and self._ready.done() and self._ready.result() and self._info.get("profile"))

    @property
    def busy(self) -> bool:
        """A synthesis request is in flight (never unload the model now)."""
        return bool(self._queues)

    def state(self) -> dict[str, Any]:
        ttfa = sorted(t["ttfa_ms"] for t in self._timings if t.get("ttfa_ms"))
        return {
            "pid": self._process.pid if self._process is not None and self._process.returncode is None else None,
            "ready": bool(self._ready is not None and self._ready.done() and self._ready.result()),
            "restarts": self._restarts,
            "last_error": self._last_error,
            "info": dict(self._info),
            "segments": len(self._timings),
            "ttfa_p50_ms": ttfa[len(ttfa) // 2] if ttfa else None,
            "ttfa_p95_ms": ttfa[min(len(ttfa) - 1, int(len(ttfa) * 0.95))] if ttfa else None,
        }

    async def _spawn(self) -> None:
        if self._ready is None or self._ready.done():
            self._ready = asyncio.get_running_loop().create_future()
        if self._stderr_path:
            # logs/ is not versioned, so a fresh clone does not have it yet.
            Path(self._stderr_path).parent.mkdir(parents=True, exist_ok=True)
        stderr = open(self._stderr_path, "ab") if self._stderr_path else subprocess.DEVNULL
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                # Windows: no console window. Launched from pythonw, the worker would
                # otherwise get its own console, and closing it kills the model.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr,
                limit=8 * 2**20,  # one audio event is a single base64 line
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        except OSError as error:
            self._last_error = f"cannot start worker: {error}"
            self._ready.set_result(False)
            return
        finally:
            if stderr is not subprocess.DEVNULL:
                stderr.close()
        self._reader = asyncio.create_task(self._read(self._process, self._ready))

    async def _read(self, process: asyncio.subprocess.Process, ready: asyncio.Future) -> None:
        assert process.stdout is not None
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "ready":
                self._info = {k: v for k, v in event.items() if k != "type"}
                if not ready.done():
                    ready.set_result(True)
            elif kind == "fatal":
                self._last_error = f"{event.get('code')}: {event.get('detail')}"
            elif kind == "pong":
                self._info.update({k: v for k, v in event.items() if k != "type"})
            else:
                queue = self._queues.get(str(event.get("id")))
                if queue is not None:  # otherwise stale: a finished or cancelled request
                    queue.put_nowait(event)
        await process.wait()
        if self._stopping:
            return
        self._last_error = self._last_error or f"worker exited with code {process.returncode}"
        self._fail_pending()
        if self._restarts < self._restart_max:
            self._restarts += 1
            self._ready = asyncio.get_running_loop().create_future()
            if not ready.done():
                ready.set_result(False)
            self._restart_task = asyncio.create_task(self._restart())
        elif not ready.done():
            ready.set_result(False)
        else:
            self._ready = asyncio.get_running_loop().create_future()
            self._ready.set_result(False)

    async def _restart(self) -> None:
        await asyncio.sleep(self._backoff * self._restarts)
        if not self._stopping:
            self._last_error = ""
            await self._spawn()

    def _fail_pending(self) -> None:
        for queue in self._queues.values():
            queue.put_nowait(None)

    # -- synthesis -------------------------------------------------------------------

    async def synthesize(self, text: AsyncIterator[str], context: TurnContext) -> AsyncIterator[bytes]:
        warming = self._ready is not None and not self._ready.done() and self._process is not None and self._process.returncode is None
        if warming and self._warmup_wait:
            with suppress(asyncio.TimeoutError, ProviderUnavailable):
                await asyncio.wait_for(self.wait_ready(), self._warmup_wait)
        if not (self._ready is not None and self._ready.done() and self._ready.result()):
            # Not transient within this turn: fail over to SAPI at once, no retry.
            reason = self._last_error or "warming up"
            raise ProviderError(f"voice clone worker not ready ({reason})", transient=False, provider=self.name)
        if not self._info.get("profile"):
            raise ProviderConfigError("no voice profile enrolled", provider=self.name)
        async for segment in text:
            context.cancellation.raise_if_cancelled()
            segment = segment.strip()
            if segment:
                async for chunk in self._synthesize_segment(segment, context):
                    yield chunk

    async def _synthesize_segment(self, segment: str, context: TurnContext) -> AsyncIterator[bytes]:
        process = self._process
        if process is None or process.returncode is not None or process.stdin is None:
            raise ProviderUnavailable("voice clone worker not running", provider=self.name)
        request_id = uuid.uuid4().hex
        queue: asyncio.Queue = asyncio.Queue()
        self._queues[request_id] = queue
        finished = False
        try:
            await self._send(process, {"op": "synth", "id": request_id, "text": segment})
            while True:
                event = await self._next_event(queue, context, process)
                kind = event["type"]
                if kind == "audio":
                    yield pcm_to_wav(base64.b64decode(event["pcm"]), int(event.get("rate", 24000)))
                elif kind == "done":
                    finished = True
                    self._timings.append(event)
                    return
                elif kind == "error":
                    finished = True
                    if event.get("code") == "no_profile":
                        raise ProviderConfigError("no voice profile enrolled", provider=self.name)
                    raise ProviderUnavailable(
                        f"voice clone synthesis failed: {event.get('code')} {event.get('detail', '')}",
                        provider=self.name,
                    )
        finally:
            self._queues.pop(request_id, None)
            if not finished and process.returncode is None:
                with suppress(Exception):
                    await self._send(process, {"op": "cancel", "id": request_id})

    async def _next_event(self, queue: asyncio.Queue, context: TurnContext, process) -> dict:
        getter = asyncio.ensure_future(queue.get())
        deadline = asyncio.get_running_loop().time() + self._event_timeout
        try:
            while True:
                if context.cancellation.cancelled:
                    raise TurnCancelled()
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    # A silent worker is hung (e.g. a wedged CUDA call): recycle it.
                    self._last_error = "worker stopped responding"
                    _kill_tree(process)
                    raise ProviderUnavailable("voice clone worker timed out", provider=self.name)
                done, _ = await asyncio.wait({getter}, timeout=min(remaining, 0.05))
                if done:
                    event = getter.result()
                    if event is None:
                        raise ProviderUnavailable(
                            f"voice clone worker exited ({self._last_error})", provider=self.name
                        )
                    return event
        finally:
            if not getter.done():
                getter.cancel()

    @staticmethod
    async def _send(process, message: dict) -> None:
        process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        await process.stdin.drain()


def _kill_tree(process) -> None:
    """Kill the worker and its children (a Windows venv python.exe is a launcher)."""
    if process.returncode is not None:
        return
    if sys.platform == "win32":
        # CREATE_NO_WINDOW: the daemon runs under pythonw, so a console program
        # would otherwise flash a terminal window on the owner's screen.
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        with suppress(ProcessLookupError):
            process.kill()
