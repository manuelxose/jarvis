"""A small, safe supervised agent child for Hermes.

Streams one Ollama chat turn over stdio JSON-lines: it POSTs the inbound user
text to a local Ollama ``/api/chat`` endpoint and re-emits each token as a
``partial_response`` event so the supervisor can observe progress in real time.
It performs no destructive actions.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

SYSTEM_PROMPT = "Eres Jarvis, asistente en español."


def emit(request_id: str, turn_id: str, event_type: str, payload: dict | None = None) -> None:
    message = {
        "request_id": request_id,
        "turn_id": turn_id,
        "event_type": event_type,
        "payload": payload or {},
        "timestamp": time.time(),
    }
    print(json.dumps(message, separators=(",", ":")), flush=True)


def _stream_ollama(base_url: str, model: str, text: str):
    """Yield content tokens from a streaming Ollama ``/api/chat`` turn."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "stream": True,
        "options": {"temperature": 0.7},
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        for line in response:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            token = (obj.get("message") or {}).get("content")
            if token:
                yield token
            if obj.get("done"):
                break


def handle(request_id: str, turn_id: str, text: str) -> None:
    if text.strip() == "__CRASH__":
        sys.exit(1)
    base_url = os.environ.get("JARVIS_OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.environ.get("JARVIS_OLLAMA_MODEL", "mistral:7b-instruct")
    emit(request_id, turn_id, "started", {})
    try:
        for token in _stream_ollama(base_url, model, text):
            emit(request_id, turn_id, "partial_response", {"text": token})
        emit(request_id, turn_id, "completed", {"detail": "done"})
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        emit(request_id, turn_id, "failed", {"detail": str(exc)})


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        request_id = message.get("request_id")
        turn_id = message.get("turn_id")
        event_type = message.get("event_type")
        payload = message.get("payload")
        if not all(isinstance(value, str) and value for value in (request_id, turn_id, event_type)):
            continue
        if not isinstance(payload, dict):
            continue
        if event_type == "request":
            handle(request_id, turn_id, str(payload.get("text", "")))
        elif event_type == "cancel":
            emit(request_id, turn_id, "cancelled", {})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
