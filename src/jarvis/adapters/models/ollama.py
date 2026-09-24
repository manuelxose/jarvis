"""Streaming adapter for a local Ollama ``/api/chat`` endpoint."""

from __future__ import annotations

import json
from typing import AsyncIterator

from jarvis.core.contracts import TurnContext
from jarvis.core.errors import ProviderConfigError

from ._transport import MAX_RESPONSE_TOKENS, stream_lines, voice_messages

# Keep the model resident between turns; Ollama's 5 min default unloads it and
# the next turn pays a multi-second cold load.
KEEP_ALIVE = "30m"
# As a cloud fallback, free the GPU soon after an offline burst ends so the
# resident voice-clone TTS model keeps its VRAM (8 GB laptop GPU).
FALLBACK_KEEP_ALIVE = "2m"


class OllamaProvider:
    """Stream tokens from a local Ollama server (NDJSON chat protocol)."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "mistral:7b-instruct",
        timeout_seconds: float = 60.0,
        temperature: float = 0.7,
        name: str = "ollama",
        keep_alive: str = KEEP_ALIVE,
    ) -> None:
        # Windows resolves "localhost" to ::1 first; Ollama listens on IPv4 only,
        # so every request paid a ~2 s IPv6 connect timeout before falling back.
        self.base_url = base_url.rstrip("/").replace("://localhost", "://127.0.0.1")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.name = name
        self.keep_alive = keep_alive

    def warm_up(self, timeout_seconds: float = 120.0) -> bool:
        """Load the model into memory now so the first turn skips the cold load."""
        import urllib.error  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        payload = json.dumps({"model": self.model, "keep_alive": self.keep_alive}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return response.status < 400
        except (urllib.error.URLError, OSError, ValueError):
            return False

    async def generate(self, prompt: str, context: TurnContext) -> AsyncIterator[str]:
        if not self.base_url:
            raise ProviderConfigError(
                f"{self.name} has no base URL configured", provider=self.name
            )
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": voice_messages(prompt),
            "stream": True,
            "keep_alive": self.keep_alive,
            "options": {"temperature": self.temperature, "num_predict": MAX_RESPONSE_TOKENS},
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
