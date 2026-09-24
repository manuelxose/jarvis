"""Turn orchestration: routing, execution, cancellation, and trace timing.

A single voice interaction is one :class:`TurnManager.handle` call. The manager
owns the active turn's cancellation token and propagates cancellation through
every stage (model stream, Hermes events, tool execution, TTS, playback).
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional

from jarvis.core.contracts import (
    AgentRuntime,
    AgentToken,
    AgentToolRequest,
    AudioPlayer,
    ModelProvider,
    TextToSpeech,
    TurnContext,
)
from jarvis.adapters.tts.ack_cache import AckAudioCache, bytes_to_stream, join_wavs
from jarvis.observability.event_hub import hub
from jarvis.core.errors import ToolError
from jarvis.core.state import RuntimeState
from jarvis.core.turn import TurnCancelled
from jarvis.observability.tracing import InteractionTrace

from .routing import RouteDecision, Router

# Sentence-sized chunks keep speech natural without waiting for the whole
# answer. The first chunk is released as early as possible (first sentence end,
# or a comma once it is long enough) because it gates time-to-first-audio.
_MAX_CHUNK_CHARS = 220
_MIN_CHUNK_CHARS = 40
_FIRST_PHRASE_MIN_CHARS = 24
_TIMEOUT_MIN_CHARS = 12
_SENTENCE_END = re.compile(r"[.!?…]+[\"»”')\]]*\s")
_PHRASE_END = re.compile(r"[,;:]\s")
_ABBREVIATION = re.compile(
    r"(?:\b(?:sr|sra|srta|dr|dra|d|dña|ud|uds|vd|etc|ej|aprox|núm|num|pág|pag|av|avda|tel|art|cap|vol|mr|mrs|st|uu|ee)"
    r"|\b[A-ZÁÉÍÓÚÑ])\.$",
    re.IGNORECASE,
)


def _sentence_cut(text: str, min_chars: int) -> int:
    for match in _SENTENCE_END.finditer(text):
        end = match.end()
        if end < min_chars:
            continue
        head = text[: match.start() + 1]
        if head.endswith(".") and _ABBREVIATION.search(head[-8:]):
            continue
        return end
    return 0


def _phrase_cut(text: str, min_chars: int) -> int:
    for match in _PHRASE_END.finditer(text):
        if match.end() >= min_chars:
            return match.end()
    return 0


def _word_cut(text: str, limit: int) -> int:
    """Last phrase boundary, else last space, at or before *limit*."""
    window = text[:limit]
    ends = [m.end() for m in _PHRASE_END.finditer(window)]
    if ends:
        return ends[-1]
    space = window.rfind(" ")
    return space + 1 if space > 0 else limit


class SentenceChunker:
    """Convert a raw token stream into natural Spanish phrase chunks.

    A boundary needs whitespace after the punctuation, so decimals ("3.5")
    never split, and a known abbreviation ("Sr.", "p. ej.") is not a sentence
    end. If the stream stalls for *max_wait_seconds* with text buffered, the
    buffer is flushed at a word boundary so audio is not held hostage by a
    slow provider.
    """

    def __init__(
        self, tokens: AsyncIterator[str], context: TurnContext, *, max_wait_seconds: float = 1.2
    ) -> None:
        self._tokens = tokens
        self._context = context
        self._max_wait = max_wait_seconds

    def _cut(self, buffer: str, first: bool) -> int:
        cut = _sentence_cut(buffer, 1 if first else _MIN_CHUNK_CHARS)
        if not cut and first:
            cut = _phrase_cut(buffer, _FIRST_PHRASE_MIN_CHARS)
        if not cut and len(buffer) > _MAX_CHUNK_CHARS:
            cut = _word_cut(buffer, _MAX_CHUNK_CHARS)
        return cut

    async def __aiter__(self) -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        iterator = self._tokens.__aiter__()
        buffer = ""
        first = True
        since: Optional[float] = None
        pending: Optional[asyncio.Future] = None
        try:
            while True:
                self._context.cancellation.raise_if_cancelled()
                if pending is None:
                    pending = asyncio.ensure_future(anext(iterator))
                timeout = None if since is None else max(0.0, since + self._max_wait - loop.time())
                done, _ = await asyncio.wait({pending}, timeout=timeout)
                self._context.cancellation.raise_if_cancelled()
                if not done:
                    # Provider stalled: speak what we have, up to the last word.
                    stripped = buffer.rstrip()
                    if len(stripped) >= _TIMEOUT_MIN_CHARS:
                        cut = stripped.rfind(" ") + 1 if buffer == stripped else len(buffer)
                        cut = cut or len(buffer)
                        yield buffer[:cut].strip()
                        buffer, first = buffer[cut:], False
                    since = loop.time() if buffer.strip() else None
                    continue
                try:
                    token = pending.result()
                except StopAsyncIteration:
                    pending = None
                    break
                pending = None
                if not token:
                    continue
                buffer += token
                if since is None:
                    since = loop.time()
                while (cut := self._cut(buffer, first)) > 0:
                    yield buffer[:cut].strip()
                    buffer, first = buffer[cut:], False
                    since = loop.time() if buffer.strip() else None
            if buffer.strip():
                yield buffer.strip()
        finally:
            if pending is not None:
                if pending.done():
                    if not pending.cancelled():
                        pending.exception()  # consumed: the turn already ended
                else:
                    pending.cancel()


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
        ack_cache: Optional[AckAudioCache] = None,
        planner: Any = None,
        agent_tools: Optional[Callable[[str, dict[str, Any], TurnContext], Any]] = None,
    ) -> None:
        self._router = router
        self._tools = tools
        self._model = model
        self._tts = tts
        self._audio = audio
        self._hermes = hermes
        self._memory = memory
        self._state_setter = state_setter
        self._ack_cache = ack_cache
        # Desktop planner and the tool entry used for agent-originated
        # requests, which the gateway treats as untrusted.
        self.planner = planner
        self._agent_tools = agent_tools or tools
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
        ok = False
        try:
            self._set_state(RuntimeState.THINKING)
            decision = await self._router.route(text, context)
            route = decision.route
            trace.mark("routing_ms")
            hub.publish("agent.started", trace_id=context.trace_id, route=route)

            if route == "desktop" and self.planner is None:
                route = "fast_model"
            if route == "fast_command":
                response = await self._handle_fast_command(decision, text, context)
            elif route == "desktop":
                response = await self.planner.handle(text, context)
                await self._speak_text(response, context)
            elif route == "hermes" and self._hermes is not None:
                response = await self._handle_hermes(text, context, trace)
            else:
                response = await self._handle_model(text, context, trace)
            trace.mark("total_request_ms")
            ok = True
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
            hub.publish("agent.completed", trace_id=context.trace_id, route=route, ok=ok, elapsed_ms=round((time.monotonic() - started) * 1000, 1))
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
        except ToolError as error:
            response = str(error)
            succeeded = False
        else:
            succeeded = getattr(result, "ok", True) is not False
            if self.planner is not None and _unresolved(decision.command.name, result):
                # "abre el proyecto X" is not an app name: let the planner resolve it.
                response = await self.planner.handle(text, context)
                await self._speak_text(response, context)
                return response
            response = self._command_response(decision.command.name, result)
        # A command result (or its error message) is spoken so the user hears the
        # outcome rather than a silent execution; this is the milestone's spoken
        # response guarantee for the fast-command path. A cached acknowledgement
        # skips the TTS provider round trip entirely.
        # Only stable success phrases are cached: a failure or a value (time,
        # percentages) is always spoken live, so a cached "done" can never lie.
        cacheable = self._ack_cache is not None and succeeded and _stable_ack(response)
        cached = self._ack_cache.get(response) if cacheable else None
        if cached is not None:
            hub.publish("speech.started", trace_id=context.trace_id, source="cache")
            await self._audio.play(bytes_to_stream(cached), context)
            hub.publish("speech.completed", trace_id=context.trace_id, cancelled=False)
        elif cacheable and hasattr(self._ack_cache, "put") and self._ready_clone() is not None:
            await self._speak_and_cache(response, context)
        else:
            await self._speak_text(response, context)
        return response

    def _ready_clone(self) -> Any:
        """The cloned-voice provider if it is loaded (only its audio may be cached)."""
        for provider in getattr(self._tts, "providers", (self._tts,)):
            if getattr(provider, "ready", False):
                return provider
        return None

    async def _speak_and_cache(self, text: str, context: TurnContext) -> None:
        clone = self._ready_clone()
        chunks: list[bytes] = []

        async def _gen() -> AsyncIterator[str]:
            yield text

        async def _tee() -> AsyncIterator[bytes]:
            async for chunk in clone.synthesize(_gen(), context):
                if not chunks:
                    hub.publish("speech.started", trace_id=context.trace_id, source="live")
                chunks.append(chunk)
                yield chunk

        await self._audio.play(_tee(), context)
        hub.publish("speech.completed", trace_id=context.trace_id, cancelled=context.cancellation.cancelled)
        if chunks and not context.cancellation.cancelled:
            try:
                self._ack_cache.put(text, join_wavs(chunks))
            except (OSError, ValueError):
                pass  # the cache is an optimisation, never a failure

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
        return await self._speak_stream(self._model.generate(prompt, context), context, trace)

    async def _speak_stream(
        self, tokens: AsyncIterator[str], context: TurnContext, trace: InteractionTrace
    ) -> str:
        """Chunk a token stream and speak it while it is still being generated."""
        collected: list[str] = []

        async def _timed_tokens() -> AsyncIterator[str]:
            async for token in tokens:
                if token and "llm_first_token_ms" not in marked:
                    marked.add("llm_first_token_ms")
                    trace.mark("llm_first_token_ms")
                yield token

        async def _tee() -> AsyncIterator[str]:
            # Record each chunk as it is sent to TTS instead of re-generating the
            # model response afterwards: a second generate() call would double
            # provider cost and, for a non-deterministic model, could return text
            # that does not match what was actually spoken.
            async for chunk in SentenceChunker(_timed_tokens(), context):
                if not collected:
                    trace.mark("first_segment_ms")
                collected.append(chunk)
                yield chunk

        async def _timed_audio() -> AsyncIterator[bytes]:
            async for audio in self._tts.synthesize(_tee(), context):
                if "tts_first_audio_ms" not in marked:
                    marked.add("tts_first_audio_ms")
                    trace.mark("tts_first_audio_ms")
                    hub.publish("speech.started", trace_id=context.trace_id, source="live")
                    # The queue is idle between turns, so the first chunk starts
                    # on the device as soon as it is enqueued.
                    trace.mark("playback_start_ms")
                yield audio

        marked: set[str] = set()
        try:
            await self._audio.play(_timed_audio(), context)
        finally:
            if marked:
                hub.publish("speech.completed", trace_id=context.trace_id, cancelled=context.cancellation.cancelled)
        return " ".join(collected)

    async def _handle_hermes(
        self, text: str, context: TurnContext, trace: InteractionTrace
    ) -> str:
        assert self._hermes is not None
        tokens: list[str] = []
        if self.planner is not None:
            # "this error", "that project": give the agent what the owner sees.
            text = f"{text}\n\nContexto del escritorio: {self.planner.context_json()}"

        async def _agent_tokens() -> AsyncIterator[str]:
            # Tokens are spoken as they stream; tool requests run inline.
            async for event in self._hermes.respond(text, context):
                context.cancellation.raise_if_cancelled()
                if isinstance(event, AgentToken):
                    if not tokens:
                        trace.mark("agent_first_token_ms")
                    tokens.append(event.text)
                    yield event.text
                elif isinstance(event, AgentToolRequest):
                    await self._agent_tools(event.name, dict(event.arguments), context)

        await self._speak_stream(_agent_tokens(), context, trace)
        trace.mark("agent_total_ms")
        if not tokens:
            response = "No he podido completar la tarea."
            await self._speak_text(response, context)
            return response
        return "".join(tokens).strip()

    async def speak_text(self, text: str, context: Optional[TurnContext] = None) -> None:
        """Speak *text* outside a routed turn (startup welcome, confirmations)."""
        await self._speak_text(text, context or TurnContext.fresh("system"))

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


def _stable_ack(text: str) -> bool:
    """A fixed success phrase worth caching (no numbers, no failure wording)."""
    text = (text or "").strip()
    return 0 < len(text) <= 80 and not any(c.isdigit() for c in text) and not text.lower().startswith(("no ", "tool ", "error"))


def _unresolved(name: str, result: Any) -> bool:
    return name == "open_application" and str(result).startswith("No se como abrir")


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
