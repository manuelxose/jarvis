"""Streaming adapter for OpenAI-compatible chat-completions endpoints."""

from __future__ import annotations

import json
from typing import AsyncIterator

from jarvis.core.contracts import ModelProvider, TurnContext
from jarvis.core.errors import ProviderConfigError, ProviderUnavailable

from ._transport import stream_lines


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
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.name = name

    async def generate(self, prompt: str, context: TurnContext) -> AsyncIterator[str]:
        if not self.api_key:
            raise ProviderConfigError(
                f"{self.name} has no API key configured", provider=self.name
            )
        if not self.base_url:
            raise ProviderConfigError(
                f"{self.name} has no base URL configured", provider=self.name
            )
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "temperature": self.temperature,
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
            token = _extract_token(obj)
            if token:
                yield token


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
