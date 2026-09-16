"""Turn orchestration: routing, execution, cancellation, and trace timing.

A single voice interaction is one :class:`TurnManager.handle` call. The manager
owns the active turn's cancellation token and propagates cancellation through
every stage (model stream, Hermes events, tool execution, TTS, playback).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional

from jarvis.core.contracts import (
    AgentEvent,
    AgentRuntime,
    AgentStatus,
    AgentToken,
    AgentToolRequest,
    AudioPlayer,
    ModelProvider,
    TextToSpeech,
    TurnContext,
)
from jarvis.core.errors import ToolExecutionError
from jarvis.core.state import RuntimeState
from jarvis.core.turn import TurnCancelled
from jarvis.observability.tracing import InteractionTrace

from .routing import RouteDecision, Router

# Sentence boundary chunks keep speech natural without waiting for the whole
# answer, and without sounding fragmented.
_CHUNK_END = ".!?\n"
_MAX_CHUNK_CHARS = 220
_MIN_CHUNK_CHARS = 40


class SentenceChunker:
    """Convert a raw token stream into natural phrase chunks."""

    def __init__(self, tokens: AsyncIterator[str], context: TurnContext) -> None:
        self._tokens = tokens
        self._context = context

    async def __aiter__(self) -> AsyncIterator[str]:
        buffer: list[str] = []
        size = 0
        async for token in self._tokens:
            self._context.cancellation.raise_if_cancelled()
            if not token:
                continue
            buffer.append(token)
            size += len(token)
            last = buffer[-1]
            if last and last[-1] in _CHUNK_END and size >= _MIN_CHUNK_CHARS:
                yield "".join(buffer).strip()
                buffer.clear()
                size = 0
            elif size >= _MAX_CHUNK_CHARS:
                yield "".join(buffer).strip()
                buffer.clear()
                size = 0
        if buffer:
            yield "".join(buffer).strip()


@dataclass(frozen=True)
class TurnResult:
    """Outcome of one completed or cancelled turn."""

    transcript: str
    response: str
    route: str
    elapsed_ms: float
    trace: dict[str, Any]
    cancelled: bool = False


class TurnManager:
    """Own the active turn and its cooperative cancellation boundary."""

    def __init__(
        self,
        *,
        router: Router,
        tools: Callable[[str, dict[str, Any], TurnContext], Any],
        model: ModelProvider,
        tts: TextToSpeech,
        audio: AudioPlayer,
        hermes: Optional[AgentRuntime] = None,
        memory: Any = None,
        state_setter: Optional[Callable[[RuntimeState], None]] = None,
    ) -> None:
        self._router = router
        self._tools = tools
        self._model = model
        self._tts = tts
        self._audio = audio
        self._hermes = hermes
        self._memory = memory
        self._state_setter = state_setter
        self._current: Optional[TurnContext] = None
        self._lock = asyncio.Lock()

    @property
    def active(self) -> bool:
        return self._current is not None

    async def interrupt(self) -> None:
        """Barge-in: cancel the active turn and flush playback immediately."""
        async with self._lock:
            if self._current is not None:
                self._current.cancellation.cancel()

    async def handle(self, text: str, *, conversation_id: str = "default") -> TurnResult:
        """Run one complete turn from transcript to response playback."""
        async with self._lock:
            self._current = TurnContext.fresh(conversation_id)
            context = self._current
        trace = InteractionTrace(context.trace_id)
        started = time.monotonic()
        trace.mark("speech_end")
        response = ""
        route = "fast_model"
        try:
            self._set_state(RuntimeState.THINKING)
            decision = await self._router.route(text, context)
            route = decision.route
            trace.mark("routing_ms")

            if route == "fast_command":
                response = await self._handle_fast_command(decision, text, context)
            elif route == "hermes" and self._hermes is not None:
                response = await self._handle_hermes(text, context, trace)
            else:
                response = await self._handle_model(text, context, trace)
            trace.mark("total_request_ms")
            return TurnResult(
                transcript=text,
                response=response,
                route=route,
                elapsed_ms=(time.monotonic() - started) * 1000,
                trace=trace.as_dict(),
            )
        except (TurnCancelled, asyncio.CancelledError):
            return TurnResult(
                transcript=text,
                response=response,
                route=route,
                elapsed_ms=(time.monotonic() - started) * 1000,
                trace=trace.as_dict(),
                cancelled=True,
            )
        finally:
            async with self._lock:
                if self._current is context:
                    self._current = None

    async def _handle_fast_command(
        self, decision: RouteDecision, text: str, context: TurnContext
    ) -> str:
        assert decision.command is not None
        try:
            result = await self._tools(
                decision.command.name, dict(decision.command.arguments), context
            )
        except ToolExecutionError as error:
            return str(error)
        return self._command_response(decision.command.name, result)

    def _command_response(self, name: str, result: Any) -> str:
        if isinstance(result, str):
            return result
        if result is None or result is True:
            return _COMMAND_ACKS.get(name, "Hecho.")
        if result is False:
            return "No he podido completar la accion."
        return str(result)

    async def _handle_model(self, text: str, context: TurnContext, trace: InteractionTrace) -> str:
        prompt = await self._build_prompt(text, context)
        tokens = self._model.generate(prompt, context)
        chunker = SentenceChunker(tokens, context)
        collected: list[str] = []
        try:
            audio_stream = self._tts.synthesize(chunker, context)
            trace.mark("playback_start_ms")
            await self._audio.play(audio_stream, context)
        except (TurnCancelled, asyncio.CancelledError):
            raise
        # Re-stream to collect the text for the result record without replaying
        # audio twice: fakes are deterministic, real providers are not replayed.
        tokens = self._model.generate(prompt, context)
        async for chunk in SentenceChunker(tokens, context):
            collected.append(chunk)
        return " ".join(collected)

    async def _handle_hermes(
        self, text: str, context: TurnContext, trace: InteractionTrace
    ) -> str:
        assert self._hermes is not None
        collected: list[str] = []
        chunks: list[str] = []
        async for event in self._hermes.respond(text, context):
            context.cancellation.raise_if_cancelled()
            if isinstance(event, AgentToken):
                if not collected:
                    trace.mark("agent_first_token_ms")
                collected.append(event.text)
                chunks.append(event.text)
            elif isinstance(event, AgentToolRequest):
                await self._tools(event.name, dict(event.arguments), context)
            elif isinstance(event, AgentStatus):
                pass
        if not collected:
            return "No he podido completar la tarea."
        response = " ".join(collected)
        trace.mark("agent_total_ms")
        trace.mark("playback_start_ms")
        await self._speak_text(response, context)
        return response

    async def _speak_text(self, text: str, context: TurnContext) -> None:
        async def _gen() -> AsyncIterator[str]:
            yield text

        await self._audio.play(self._tts.synthesize(_gen(), context), context)

    async def _build_prompt(self, text: str, context: TurnContext) -> str:
        if self._memory is None:
            return text
        try:
            memories = await self._memory.recall(text, context)
        except Exception:
            memories = []
        if not memories:
            return text
        return text + "\n\nRelevant context:\n" + "\n".join(f"- {item}" for item in memories)

    def _set_state(self, state: RuntimeState) -> None:
        if self._state_setter is not None:
            try:
                self._state_setter(state)
            except Exception:
                pass


_COMMAND_ACKS = {
    "volume_up": "He subido el volumen.",
    "volume_down": "He bajado el volumen.",
    "volume_set": "Volumen ajustado.",
    "mute": "Sonido silenciado.",
    "unmute": "Sonido restaurado.",
    "media_play_pause": "Hecho.",
    "media_next": "Siguiente.",
    "media_previous": "Anterior.",
    "open_application": "Abriendo.",
    "open_url": "Abriendo el enlace.",
    "stop": "Hecho.",
    "repeat": "Hecho.",
    "time": "",
    "date": "",
    "system_info": "",
}
