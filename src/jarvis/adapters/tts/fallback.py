"""Structured TTS provider fallback with a per-provider circuit breaker.

Mirrors ``jarvis.adapters.models.fallback.ProviderChain``: providers are tried
in order, transient failures retry with bounded backoff, and once a provider
has yielded its first audio byte for the turn it is committed to (mid-stream
fallback would duplicate or overlap audio). The input text stream can only be
consumed once, so a ``_BufferedText`` wrapper replays chunks already read to
whichever provider is retried next, then continues reading the live stream.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Callable, Optional

from jarvis.core.circuit_breaker import CircuitBreaker
from jarvis.core.contracts import TextToSpeech, TurnContext
from jarvis.core.errors import ProviderConfigError, ProviderError, ProviderUnavailable
from jarvis.core.turn import TurnCancelled


class _BufferedText:
    """Replay chunks already read from *source*, then continue reading it."""

    def __init__(self, source: AsyncIterator[str]) -> None:
        self._source = source
        self._buffer: list[str] = []
        self._exhausted = False

    async def replay(self) -> AsyncIterator[str]:
        for chunk in list(self._buffer):
            yield chunk
        if self._exhausted:
            return
        async for chunk in self._source:
            self._buffer.append(chunk)
            yield chunk
        self._exhausted = True


class TTSChain:
    """Try TTS providers in order, skipping open circuits and retrying transiently."""

    def __init__(
        self,
        providers: list[TextToSpeech],
        *,
        retries: int = 1,
        backoff_seconds: float = 0.25,
        breaker: Optional[CircuitBreaker] = None,
        on_select: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._providers = providers
        self._retries = max(0, retries)
        self._backoff_seconds = max(0.0, backoff_seconds)
        self._breaker = breaker or CircuitBreaker()
        self._on_select = on_select

    @property
    def providers(self) -> tuple[TextToSpeech, ...]:
        return tuple(self._providers)

    def _name(self, provider: TextToSpeech) -> str:
        return getattr(provider, "name", type(provider).__name__)

    async def synthesize(self, text: AsyncIterator[str], context: TurnContext) -> AsyncIterator[bytes]:
        if not self._providers:
            raise ProviderUnavailable("no TTS providers configured")
        buffered = _BufferedText(text)
        last_error: Optional[Exception] = None
        for provider in self._providers:
            name = self._name(provider)
            if not self._breaker.allow(name):
                continue
            attempts = 0
            while True:
                context.cancellation.raise_if_cancelled()
                if self._on_select is not None:
                    self._on_select(name)
                got_first = False
                try:
                    async for audio in provider.synthesize(buffered.replay(), context):
                        got_first = True
                        yield audio
                    self._breaker.record_success(name)
                    return
                except ProviderConfigError as error:
                    last_error = error
                    self._breaker.record_failure(name)
                    break  # misconfigured: skip, do not retry
                except ProviderError as error:
                    last_error = error
                    if got_first:
                        # Mid-stream failure after partial audio cannot restart cleanly.
                        raise
                    self._breaker.record_failure(name)
                    if not error.transient:
                        break
                    attempts += 1
                    if attempts > self._retries:
                        break
                    delay = min(self._backoff_seconds * (2 ** (attempts - 1)), 1.0)
                    await asyncio.sleep(delay)
                except (TurnCancelled, asyncio.CancelledError):
                    raise
        raise ProviderUnavailable(f"all TTS providers failed: {last_error}") from last_error
