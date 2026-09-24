"""Deterministic fake adapters for fast, reliable orchestration tests.

These satisfy the same ports as the real adapters and let the turn pipeline,
routing, cancellation, barge-in, and memory behaviour be exercised without
audio hardware, provider credentials, or paid API calls.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable, Mapping, Optional

from jarvis.core.contracts import (
    AgentStatus,
    AgentToken,
    Transcript,
    TurnContext,
    WakeDetector,
)


class ScriptedModel:
    """Yield scripted tokens from a prompt-keyed (or default) response map."""

    def __init__(
        self,
        responses: Optional[Mapping[str, str] | Callable[[str], str]] = None,
        *,
        default: str = "Respuesta del modelo.",
    ) -> None:
        self._responses = responses
        self._default = default
        self.calls: list[str] = []

    def _response(self, prompt: str) -> str:
        if callable(self._responses):
            return self._responses(prompt)
        if isinstance(self._responses, Mapping):
            for key, value in self._responses.items():
                if key in prompt:
                    return value
        return self._default

    async def generate(self, prompt: str, context: TurnContext) -> AsyncIterator[str]:
        self.calls.append(prompt)
        response = self._response(prompt)
        for word in response.split(" "):
            context.cancellation.raise_if_cancelled()
            yield word + " "
            await asyncio.sleep(0)


class FailingModel:
    """A model provider that fails transiently a set number of times."""

    def __init__(self, failures: int = 1, *, error_factory=None) -> None:
        self.failures = failures
        self.attempts = 0
        self._error_factory = error_factory

    async def generate(self, prompt: str, context: TurnContext) -> AsyncIterator[str]:
        self.attempts += 1
        if self.attempts <= self.failures:
            from jarvis.core.errors import ProviderUnavailable

            raise ProviderUnavailable("simulated transient failure", provider="fake")
        yield "respuesta de reserva"


class EchoTTS:
    """Synthesize each text chunk into deterministic bytes."""

    def __init__(self) -> None:
        self.chunks: list[str] = []

    async def synthesize(self, text: AsyncIterator[str], context: TurnContext) -> AsyncIterator[bytes]:
        async for chunk in text:
            context.cancellation.raise_if_cancelled()
            self.chunks.append(chunk)
            yield chunk.encode("utf-8")


class ScriptedSTT:
    """Yield scripted transcripts from the input stream."""

    def __init__(self, transcripts: list[Transcript] | None = None) -> None:
        self.transcripts = transcripts or [Transcript("hola jarvis", is_final=True)]

    async def transcribe(self, audio: AsyncIterator[bytes], context: TurnContext) -> AsyncIterator[Transcript]:
        # Drain the audio stream so the pipeline behaves like real STT.
        async for _ in audio:
            context.cancellation.raise_if_cancelled()
        for transcript in self.transcripts:
            context.cancellation.raise_if_cancelled()
            yield transcript


class ScriptedAudioInput:
    """Yield scripted audio frames."""

    def __init__(self, frames: list[bytes] | None = None) -> None:
        self.frames = frames or [b"\x00\x00" * 8]

    async def capture(self, context: TurnContext) -> AsyncIterator[bytes]:
        for frame in self.frames:
            context.cancellation.raise_if_cancelled()
            yield frame


class ScriptedWakeDetector(WakeDetector):
    """Return scripted wake-word decisions without interpreting audio."""

    def __init__(self, triggers: list[bool] | None = None) -> None:
        self._triggers = list(triggers) if triggers is not None else []

    def detected(self, audio: bytes) -> bool:
        if not self._triggers:
            return False
        return self._triggers.pop(0)


class RecordingAudioPlayer:
    """Record played chunks per turn instead of rendering audio."""

    def __init__(self) -> None:
        self.played: list[tuple[str, bytes]] = []

    async def play(self, audio: AsyncIterator[bytes], context: TurnContext) -> None:
        async for chunk in audio:
            context.cancellation.raise_if_cancelled()
            self.played.append((context.trace_id, chunk))


class ScriptedHermes:
    """A deterministic in-process AgentRuntime for tests."""

    def __init__(self, events: list[Any] | None = None) -> None:
        self.events = events or [
            AgentStatus("started"),
            AgentToken("resultado del agente"),
        ]

    async def respond(self, text: str, context: TurnContext) -> AsyncIterator[Any]:
        for event in self.events:
            context.cancellation.raise_if_cancelled()
            yield event


class ScriptedMemory:
    """Return scripted memories for any query."""

    def __init__(self, memories: list[str] | None = None) -> None:
        self.memories = memories or []

    async def recall(self, query: str, context: TurnContext) -> list[str]:
        return list(self.memories)
