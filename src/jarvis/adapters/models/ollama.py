"""Streaming adapter for a local Ollama ``/api/chat`` endpoint."""

from __future__ import annotations

import json
from typing import AsyncIterator

from jarvis.core.contracts import TurnContext
from jarvis.core.errors import ProviderConfigError
from jarvis.observability.cost import UsageRecord

from ._transport import MAX_RESPONSE_TOKENS, stream_lines, voice_messages

# Keep the model resident between turns; Ollama's 5 min default unloads it and
# the next turn pays a multi-second cold load.
KEEP_ALIVE = "30m"
# D043: as a cloud fallback, unload immediately after each request completes
# so the resident voice-clone TTS model never has to share VRAM with a 7B
# model on an 8 GB laptop GPU. "2m" previously left the fallback resident long
# enough to still collide with the clone worker during a fallback burst.
FALLBACK_KEEP_ALIVE = "0"


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
        extra_body: dict | None = None,
    ) -> None:
        # Windows resolves "localhost" to ::1 first; Ollama listens on IPv4 only,
        # so every request paid a ~2 s IPv6 connect timeout before falling back.
        self.base_url = base_url.rstrip("/").replace("://localhost", "://127.0.0.1")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.name = name
        self.keep_alive = keep_alive
        self.extra_body = dict(extra_body or {})
        self.last_usage_record: UsageRecord | None = None

    def is_cpu_only(self) -> bool:
        """True when the config pins this model to system RAM (``num_gpu: 0``)."""
        return (self.extra_body.get("options") or {}).get("num_gpu") == 0

    def _merged_body(self, base: dict, options: dict) -> dict:
        """Overlay ``extra_body`` on ``base``; ``options`` is dict-merged, not replaced."""
        extra = dict(self.extra_body)
        extra_options = extra.pop("options", None)
        body = {**base, **extra}
        merged = dict(options)
        if isinstance(extra_options, dict):
            merged.update(extra_options)
        if merged:
            body["options"] = merged
        return body

    def warm_up(self, timeout_seconds: float = 120.0) -> bool:
        """Load the model into memory now so the first turn skips the cold load."""
        import urllib.error  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        # Same options as generate(): Ollama reloads a model whose num_gpu differs.
        payload = json.dumps(
            self._merged_body({"model": self.model, "keep_alive": self.keep_alive}, {})
        ).encode("utf-8")
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
        self.last_usage_record = None
        if not self.base_url:
            raise ProviderConfigError(
                f"{self.name} has no base URL configured", provider=self.name
            )
        url = f"{self.base_url}/api/chat"
        payload = self._merged_body(
            {
                "model": self.model,
                "messages": voice_messages(prompt),
                "stream": True,
                "keep_alive": self.keep_alive,
            },
            {"temperature": self.temperature, "num_predict": MAX_RESPONSE_TOKENS},
        )
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
                self.last_usage_record = UsageRecord(
                    provider=self.name,
                    kind="llm",
                    model=self.model,
                    input_tokens=int(obj.get("prompt_eval_count") or 0),
                    output_tokens=int(obj.get("eval_count") or 0),
                )
                break
