"""Streaming adapter for a local Ollama ``/api/chat`` endpoint."""

from __future__ import annotations

import json
from typing import AsyncIterator

from jarvis.core.contracts import ModelProvider, TurnContext
from jarvis.core.errors import ProviderConfigError, ProviderUnavailable

from ._transport import stream_lines


class OllamaProvider:
    """Stream tokens from a local Ollama server (NDJSON chat protocol)."""

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        model: str = "mistral:7b-instruct",
        timeout_seconds: float = 60.0,
        temperature: float = 0.7,
        name: str = "ollama",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.name = name

    async def generate(self, prompt: str, context: TurnContext) -> AsyncIterator[str]:
        if not self.base_url:
            raise ProviderConfigError(
                f"{self.name} has no base URL configured", provider=self.name
            )
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "options": {"temperature": self.temperature},
        }
        headers = {"Content-Type": "application/json"}
        async for line in stream_lines(
            url, payload, headers, context, self.timeout_seconds, self.name
        ):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            token = (obj.get("message") or {}).get("content")
            if token:
                yield token
            if obj.get("done"):
                break
