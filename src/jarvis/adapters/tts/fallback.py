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
import logging
from typing import AsyncIterator, Callable, Optional, Sequence

from jarvis.config import TTSRoutingEvidence

from jarvis.core.circuit_breaker import CircuitBreaker
from jarvis.core.contracts import TextToSpeech, TurnContext
from jarvis.core.errors import ProviderConfigError, ProviderError, ProviderUnavailable
from jarvis.core.turn import TurnCancelled

logger = logging.getLogger(__name__)


def order_tts_candidates(
    providers: Sequence[TextToSpeech], profile: str,
    evidence: Sequence[TTSRoutingEvidence] = (),
) -> tuple[list[TextToSpeech], str]:
    """Order only composed adapters; return a safe, non-measurement diagnostic reason.

    Called once at composition, never during a turn. Equal scores retain input order.
    """
    candidates = list(providers)
    if profile not in {"auto", "fast", "cheap", "quality"}:
        raise ProviderConfigError("invalid TTS routing profile", provider="tts")
    names = [provider.name for provider in candidates]
    if len(names) != len(set(names)):
        raise ProviderConfigError("duplicate resolved TTS provider", provider="tts")
    rows = {row.provider: row for row in evidence}
    if len(rows) != len(evidence):
        raise ProviderConfigError("duplicate TTS routing evidence provider", provider="tts")
    if set(rows) - set(names):
        if profile != "auto":
            raise ProviderConfigError("TTS routing evidence names an unavailable provider", provider="tts")
        return candidates, "evidence names unavailable provider; configured order retained"
    if len(candidates) < 2:
        return candidates, "single candidate; configured order retained"
    selected = [rows.get(name) for name in names]
    if any(row is None or not row.complete for row in selected):
        if profile != "auto":
            raise ProviderConfigError("TTS routing requires complete evidence for every resolved provider", provider="tts")
        return candidates, "missing or incomplete comparable evidence; configured order retained"
    if len({row.run_id for row in selected}) != 1:
        if profile != "auto":
            raise ProviderConfigError("TTS routing evidence must share one operator run_id", provider="tts")
        return candidates, "mismatched operator runs; configured order retained"

    if profile in {"fast", "cheap", "quality"}:
        metric = {"fast": "first_audio_ms", "cheap": "usd_per_character", "quality": "quality"}[profile]
        reverse = profile == "quality"
        return sorted(candidates, key=lambda p: (-1 if reverse else 1) * getattr(rows[p.name], metric)), "measured evidence"

    def benefit(metric: str, value: float) -> float:
        values = [getattr(row, metric) for row in selected]
        low, high = min(values), max(values)
        return 1.0 if high == low else (high - value) / (high - low)

    def score(provider: TextToSpeech) -> float:
        row = rows[provider.name]
        return (0.35 * benefit("first_audio_ms", row.first_audio_ms)
                + 0.25 * benefit("usd_per_character", row.usd_per_character)
                + 0.20 * row.quality + 0.10 * row.reliability
                + 0.10 * row.integration)

    return sorted(candidates, key=lambda p: -score(p)), "measured evidence"


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
        self.last_provider: Optional[TextToSpeech] = None

    @property
    def providers(self) -> tuple[TextToSpeech, ...]:
        return tuple(self._providers)

    @property
    def last_usage_record(self):
        return getattr(self.last_provider, "last_usage_record", None)

    def _name(self, provider: TextToSpeech) -> str:
        return getattr(provider, "name", type(provider).__name__)

    async def synthesize(self, text: AsyncIterator[str], context: TurnContext) -> AsyncIterator[bytes]:
        self.last_provider = None
        if not self._providers:
            raise ProviderUnavailable("no TTS providers configured")
        buffered = _BufferedText(text)
        last_error: Optional[Exception] = None
        primary = self._providers[0]
        primary_name = self._name(primary)
        primary_failure: Optional[str] = None
        for provider in self._providers:
            name = self._name(provider)
            if not self._breaker.allow(name):
                if name == primary_name and primary_failure is None:
                    primary_failure = "circuit open"
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
                    self.last_provider = provider
                    if provider is not primary:
                        logger.warning(
                            "TTS fallback: served by %s after %s",
                            name, primary_failure or "unknown",
                        )
                    return
                except ProviderConfigError as error:
                    last_error = error
                    if name == primary_name and primary_failure is None:
                        primary_failure = f"{type(error).__name__}: {error}"
                    self._breaker.record_failure(name)
                    break  # misconfigured: skip, do not retry
                except ProviderError as error:
                    last_error = error
                    if got_first:
                        # Mid-stream failure after partial audio cannot restart cleanly.
                        raise
                    if name == primary_name and primary_failure is None:
                        primary_failure = f"{type(error).__name__}: {error}"
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
