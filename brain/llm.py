from __future__ import annotations

import json
import logging
from typing import Any, Generator

import requests


LOGGER = logging.getLogger(__name__)


class OllamaClient:
    """Client for local Ollama API."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "mistral:7b-instruct",
        temperature: float = 0.7,
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout

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
            "options": {"temperature": self.temperature},
        }

    def chat(self, messages: list[dict[str, str]], system_prompt: str) -> str:
        payload = self._build_payload(messages=messages, system_prompt=system_prompt, stream=False)
        try:
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

