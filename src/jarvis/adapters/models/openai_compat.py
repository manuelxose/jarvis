"""Streaming adapter for OpenAI-compatible chat-completions endpoints."""

from __future__ import annotations

import json
from typing import AsyncIterator

from jarvis.core.contracts import ModelProvider, TurnContext
from jarvis.core.errors import ProviderConfigError, ProviderUnavailable
from jarvis.observability.cost import ProviderRate, SpendLedger, UsageRecord, estimate_cost

from ._transport import MAX_RESPONSE_TOKENS, stream_lines, voice_messages


class OpenAICompatProvider:
    """Stream tokens from any OpenAI-compatible ``/v1/chat/completions`` API."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        timeout_seconds: float = 30.0,
        temperature: float = 0.7,
        name: str = "openai_compat",
        rate: ProviderRate | None = None,
        ledger: SpendLedger | None = None,
        extra_body: dict | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.name = name
        self.rate = rate
        self.ledger = ledger
        self.last_usage: dict | None = None
        # Provider-specific request fields, e.g. DeepSeek {"thinking": {"type": "disabled"}}
        self.extra_body = dict(extra_body or {})

    async def generate(self, prompt: str, context: TurnContext) -> AsyncIterator[str]:
        if not self.api_key:
            raise ProviderConfigError(
                f"{self.name} has no API key configured", provider=self.name
            )
        if not self.base_url:
            raise ProviderConfigError(
                f"{self.name} has no base URL configured", provider=self.name
            )
        if self.ledger is not None and self.ledger.exhausted():
            raise ProviderConfigError(
                f"{self.name} daily budget of ${self.ledger.max_daily_usd:.2f} reached", provider=self.name
            )
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": voice_messages(prompt),
            "stream": True,
            "temperature": self.temperature,
            "max_tokens": MAX_RESPONSE_TOKENS,
            "stream_options": {"include_usage": True},
            **self.extra_body,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        async for line in stream_lines(
            url, payload, headers, context, self.timeout_seconds, self.name
        ):
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if obj.get("usage"):
                self._record_usage(obj["usage"])
            token = _extract_token(obj)
            if token:
                yield token

    def _record_usage(self, usage: dict) -> None:
        record = UsageRecord(
            provider=self.name,
            kind="llm",
            model=self.model,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )
        usd = estimate_cost(record, self.rate)
        self.last_usage = {
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "usd": round(usd, 6) if usd is not None else None,
        }
        if usd is not None and self.ledger is not None:
            self.ledger.record(self.name, usd)


def _extract_token(obj: dict) -> str:
    choices = obj.get("choices") or []
    if not choices:
        return ""
    choice = choices[0]
    delta = choice.get("delta") or {}
    if "content" in delta and delta["content"]:
        return delta["content"]
    message = choice.get("message") or {}
    if "content" in message and message["content"]:
        return message["content"]
    text = choice.get("text")
    return text or ""
