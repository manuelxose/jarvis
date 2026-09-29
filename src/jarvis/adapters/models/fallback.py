"""Structured provider fallback with bounded retry and config-error short-circuit."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Optional

from jarvis.core.contracts import ModelProvider, TurnContext
from jarvis.core.errors import ProviderConfigError, ProviderError, ProviderUnavailable
from jarvis.core.turn import TurnCancelled

# Transports already keep HTTP error bodies short and never echo the request
# Authorization header, but this is defense in depth: a reason string that
# reaches per-turn evidence must never carry a bearer token or key, even if a
# future transport or upstream error body echoes one back.
_SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)authorization\s*[:=]\s*\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}"),
)
_MAX_REASON_CHARS = 200


def _sanitize_reason(message: object) -> str:
    """Redact secret-shaped substrings and cap length for per-turn evidence."""
    text = str(message)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text[:_MAX_REASON_CHARS]


@dataclass(frozen=True)
class ProviderAttempt:
    """One provider's outcome within a single ``ProviderChain.generate()`` call.

    ``reason`` is always sanitized text, never the raw exception, so it is
    safe to surface directly in per-turn evidence.
    """

    provider: str
    selected: bool
    reason: Optional[str] = None
    transient: Optional[bool] = None

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "selected": self.selected,
            "reason": self.reason,
            "transient": self.transient,
        }


class ProviderChain:
    """Try providers in order, retrying transient failures with bounded backoff.

    Configuration errors are never retried; they short-circuit to the next
    provider. Once a provider has streamed its first token it is committed to
    (mid-stream fallback would duplicate speech).
    """

    def __init__(
        self,
        providers: list[ModelProvider],
        *,
        retries: int = 2,
        backoff_seconds: float = 0.25,
        on_select: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._providers = providers
        self._retries = max(0, retries)
        self._backoff_seconds = max(0.0, backoff_seconds)
        self._on_select = on_select
        self.last_provider: Optional[ModelProvider] = None
        # Actual provider name that answered the last turn, and the sanitized
        # per-provider outcomes attempted to get there. These are the source
        # of truth for turn provenance: unlike last_usage_record, they are set
        # on every successful stream regardless of whether the provider ever
        # emitted a billable usage payload (some OpenAI-compatible upstreams
        # never send one), so "no spend" is never mistaken for "not selected".
        self.last_selected_provider: Optional[str] = None
        self.last_attempts: list[ProviderAttempt] = []

    @property
    def providers(self) -> tuple[ModelProvider, ...]:
        return tuple(self._providers)

    @property
    def last_usage_record(self):
        return getattr(self.last_provider, "last_usage_record", None)

    async def generate(self, prompt: str, context: TurnContext) -> AsyncIterator[str]:
        self.last_provider = None
        self.last_selected_provider = None
        self.last_attempts = []
        if not self._providers:
            raise ProviderUnavailable("no model providers configured")
        last_error: Optional[Exception] = None
        for provider in self._providers:
            name = getattr(provider, "name", type(provider).__name__)
            got_first = False
            attempts = 0
            while True:
                context.cancellation.raise_if_cancelled()
                if self._on_select is not None:
                    self._on_select(name)
                try:
                    async for token in provider.generate(prompt, context):
                        got_first = True
                        yield token
                    self.last_provider = provider
                    self.last_selected_provider = name
                    self.last_attempts.append(ProviderAttempt(provider=name, selected=True))
                    return
                except ProviderConfigError as error:
                    last_error = error
                    self.last_attempts.append(
                        ProviderAttempt(
                            provider=name, selected=False,
                            reason=_sanitize_reason(error), transient=False,
                        )
                    )
                    break  # misconfigured: skip, do not retry
                except ProviderError as error:
                    last_error = error
                    if got_first:
                        # Mid-stream failure after partial output cannot restart cleanly.
                        self.last_attempts.append(
                            ProviderAttempt(
                                provider=name, selected=False,
                                reason=_sanitize_reason(error), transient=error.transient,
                            )
                        )
                        raise
                    if not error.transient:
                        self.last_attempts.append(
                            ProviderAttempt(
                                provider=name, selected=False,
                                reason=_sanitize_reason(error), transient=False,
                            )
                        )
                        break
                    attempts += 1
                    if attempts > self._retries:
                        self.last_attempts.append(
                            ProviderAttempt(
                                provider=name, selected=False,
                                reason=_sanitize_reason(error), transient=True,
                            )
                        )
                        break
                    # ponytail: Cap retries at one second; add per-provider policy only
                    # when provider-specific rate-limit guidance requires it.
                    delay = min(self._backoff_seconds * (2 ** (attempts - 1)), 1.0)
                    await asyncio.sleep(delay)
                except (TurnCancelled, asyncio.CancelledError):
                    raise
        raise ProviderUnavailable(
            f"all model providers failed: {last_error}"
        ) from last_error
