"""Structured provider fallback with bounded retry and config-error short-circuit."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Callable, Optional

from jarvis.core.contracts import ModelProvider, TurnContext
from jarvis.core.errors import ProviderConfigError, ProviderError, ProviderUnavailable
from jarvis.core.turn import TurnCancelled


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

    @property
    def providers(self) -> tuple[ModelProvider, ...]:
        return tuple(self._providers)

    async def generate(self, prompt: str, context: TurnContext) -> AsyncIterator[str]:
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
                    return
                except ProviderConfigError as error:
                    last_error = error
                    break  # misconfigured: skip, do not retry
                except ProviderError as error:
                    last_error = error
                    if got_first:
                        # Mid-stream failure after partial output cannot restart cleanly.
                        raise
                    if not error.transient:
                        break
                    attempts += 1
                    if attempts > self._retries:
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
