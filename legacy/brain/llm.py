from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Generator

from legacy.voice.runtime_support import timed_phase

import requests


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class OllamaPreflight:
    available: bool
    message: str
    elapsed_seconds: float


class OllamaClient:
    """Client for local Ollama API."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "mistral:7b-instruct",
        temperature: float = 0.7,
        timeout: int = 30,
        num_predict: int = 128,
        num_ctx: int = 2048,
        keep_alive: str = "10m",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.num_predict = num_predict
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive

    def preflight(self, timeout: float = 2.0) -> OllamaPreflight:
        """Check the local service and configured model before the first turn."""
        started = time.monotonic()
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            names = {str(item.get("name", "")) for item in payload.get("models", [])}
            if self.model not in names:
                message = (
                    f"Ollama is running but model '{self.model}' is missing. "
                    f"Run: ollama pull {self.model}"
                )
                result = OllamaPreflight(False, message, time.monotonic() - started)
            else:
                result = OllamaPreflight(True, f"Ollama ready with model '{self.model}'.", time.monotonic() - started)
        except Exception as exc:
            result = OllamaPreflight(
                False,
                f"Ollama is unavailable at {self.base_url}. Run: ollama serve "
                f"and verify the local service. Detail: {exc}",
                time.monotonic() - started,
            )
        LOGGER.info("Ollama preflight available=%s elapsed_seconds=%.3f message=%s", result.available, result.elapsed_seconds, result.message)
        return result

    def check_availability(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=self.timeout)
            response.raise_for_status()
            return True
        except Exception as exc:
            LOGGER.error("Ollama not reachable at %s: %s", self.base_url, exc)
            return False

    def check_model_available(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
            models = payload.get("models", [])
            names = {model.get("name", "") for model in models}
            return self.model in names
        except Exception as exc:
            LOGGER.error("Could not verify Ollama model availability: %s", exc)
            return False

    def _build_payload(self, messages: list[dict[str, str]], system_prompt: str, stream: bool) -> dict[str, Any]:
        ollama_messages = [{"role": "system", "content": system_prompt}]
        ollama_messages.extend(messages)
        return {
            "model": self.model,
            "messages": ollama_messages,
            "stream": stream,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.num_predict,
                "num_ctx": self.num_ctx,
            },
        }

    def chat(self, messages: list[dict[str, str]], system_prompt: str) -> str:
        payload = self._build_payload(messages=messages, system_prompt=system_prompt, stream=False)
        try:
            with timed_phase(LOGGER, "ollama_request"):
                response = requests.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                data = response.json()
                return data.get("message", {}).get("content", "").strip()
        except Exception as exc:
            raise RuntimeError(
                "Ollama chat request failed. Verify Ollama is running and model is available."
            ) from exc

    def chat_stream(self, messages: list[dict[str, str]], system_prompt: str) -> Generator[str, None, None]:
        payload = self._build_payload(messages=messages, system_prompt=system_prompt, stream=True)
        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
                stream=True,
            )
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                decoded = line.decode("utf-8").strip()
                try:
                    chunk = json.loads(decoded)
                except json.JSONDecodeError:
                    continue
                token = chunk.get("message", {}).get("content")
                if token:
                    yield token
                if chunk.get("done"):
                    break
        except Exception as exc:
            raise RuntimeError(
                "Ollama streaming request failed. Verify Ollama service and model."
            ) from exc
