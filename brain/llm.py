from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Generator

import requests


LOGGER = logging.getLogger(__name__)


SERVICE_UNAVAILABLE = "service_unavailable"
MODEL_MISSING = "model_missing"


@dataclass(frozen=True)
class OllamaPreflight:
    """Structured result of a bounded Ollama startup preflight probe.

    ``error`` is ``None`` on success and otherwise one of
    ``SERVICE_UNAVAILABLE`` (Ollama unreachable, timed out, or malformed) or
    ``MODEL_MISSING`` (Ollama reachable but the configured model is not pulled)
    so startup can fail early with a distinct, actionable diagnostic instead of
    a late opaque interaction failure.
    """

    ok: bool
    error: str | None
    duration_seconds: float
    remediation: str | None
    detail: str | None


class OllamaClient:
    """Client for local Ollama API."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "mistral:7b-instruct",
        temperature: float = 0.7,
        timeout: int = 30,
        preflight_timeout: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.preflight_timeout = preflight_timeout

    def preflight(self, timeout: float | None = None) -> OllamaPreflight:
        """Probe Ollama once and report a bounded, actionable startup result.

        A single ``GET /api/tags`` distinguishes an unreachable service from a
        reachable service whose configured model is not pulled, and always
        returns within ``timeout`` (default ``self.preflight_timeout``). The
        result carries the measured duration and an exact local remediation
        command so callers can fail early and tell the user what to run.
        """
        started = time.monotonic()
        probe_timeout = self.preflight_timeout if timeout is None else timeout
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=probe_timeout)
            response.raise_for_status()
        except requests.exceptions.Timeout as exc:
            duration = time.monotonic() - started
            LOGGER.error(
                "Ollama preflight timed out after %.2fs at %s: %s",
                probe_timeout,
                self.base_url,
                exc,
            )
            return OllamaPreflight(
                ok=False,
                error=SERVICE_UNAVAILABLE,
                duration_seconds=duration,
                remediation="ollama serve",
                detail=f"Timed out after {probe_timeout:.1f}s reaching {self.base_url}",
            )
        except Exception as exc:
            duration = time.monotonic() - started
            LOGGER.error("Ollama not reachable at %s: %s", self.base_url, exc)
            return OllamaPreflight(
                ok=False,
                error=SERVICE_UNAVAILABLE,
                duration_seconds=duration,
                remediation="ollama serve",
                detail=f"Could not reach {self.base_url}: {exc}",
            )

        try:
            payload = response.json()
            models = payload.get("models", [])
            names = {model.get("name", "") for model in models}
        except Exception as exc:
            duration = time.monotonic() - started
            LOGGER.error("Ollama returned a malformed response: %s", exc)
            return OllamaPreflight(
                ok=False,
                error=SERVICE_UNAVAILABLE,
                duration_seconds=duration,
                remediation="ollama serve",
                detail=f"Malformed response from {self.base_url}: {exc}",
            )

        duration = time.monotonic() - started
        if self.model not in names:
            return OllamaPreflight(
                ok=False,
                error=MODEL_MISSING,
                duration_seconds=duration,
                remediation=f"ollama pull {self.model}",
                detail=(
                    f"Model {self.model} not present among {len(names)} "
                    "local model(s)"
                ),
            )
        return OllamaPreflight(
            ok=True,
            error=None,
            duration_seconds=duration,
            remediation=None,
            detail=f"Ollama ready: {self.model} present",
        )

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

