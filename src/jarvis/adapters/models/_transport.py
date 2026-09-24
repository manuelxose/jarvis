"""Shared blocking-HTTP streaming transport for model adapters.

``urllib.request`` is synchronous, so each request runs in a daemon thread that
pumps decoded response lines into an asyncio queue. The async consumer enforces
cancellation and timeout without blocking the event loop.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import AsyncIterator, Mapping, Optional

from jarvis.core.errors import ProviderConfigError, ProviderUnavailable
from jarvis.core.turn import TurnContext

# Persona + spoken-reply constraints: short answers cut generation and TTS time alike.
VOICE_SYSTEM_PROMPT = (
    "Eres J.A.R.V.I.S., la inteligencia artificial de Tony Stark en Iron Man, ahora al "
    "servicio del usuario. Hablas como un mayordomo británico: educado, sereno, eficiente, "
    "con ironía sutil y humor seco. Llamas al usuario «señor». Responde en el idioma del "
    "usuario, de forma directa y breve: una o dos frases, sin listas, sin markdown ni "
    "emojis, porque tu respuesta se lee en voz alta."
)
MAX_RESPONSE_TOKENS = 150


def voice_messages(prompt: str) -> list[dict]:
    """Chat messages for a voice turn: the spoken-style system prompt plus the user text."""
    return [
        {"role": "system", "content": VOICE_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]


def _build_request(url: str, payload: dict, headers: Mapping[str, str], timeout: float) -> urllib.request.Request:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    for key, value in headers.items():
        request.add_header(key, value)
    return request


async def stream_lines(
    url: str,
    payload: dict,
    headers: Mapping[str, str],
    context: TurnContext,
    timeout: float,
    provider: str,
) -> AsyncIterator[str]:
    """Yield one decoded line of the streaming response at a time."""
    context.cancellation.raise_if_cancelled()
    queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _put(value: Optional[str]) -> None:
        """Deliver worker output to the event loop that owns ``queue``."""
        if loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(queue.put_nowait, value)
        except RuntimeError:
            # A timed-out/cancelled consumer may have already closed its loop.
            return

    def _produce() -> None:
        request = _build_request(url, payload, headers, timeout)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                if status >= 400:
                    body = response.read(512).decode("utf-8", "replace")
                    _put(f"__HTTP_ERROR__{status}__{body}")
                    return
                for raw in response:
                    if context.cancellation.cancelled:
                        break
                    line = raw.decode("utf-8", "replace").strip()
                    if line:
                        _put(line)
        except urllib.error.HTTPError as error:
            body = ""
            try:
                body = error.read(512).decode("utf-8", "replace")
            except Exception:
                pass
            _put(f"__HTTP_ERROR__{error.code}__{body}")
        except urllib.error.URLError as error:
            _put(f"__NETWORK_ERROR__{error.reason}")
        except Exception as error:
            _put(f"__ERROR__{type(error).__name__}:{error}")
        finally:
            _put(None)

    thread = __import__("threading").Thread(target=_produce, daemon=True)
    thread.start()

    deadline = timeout
    while True:
        context.cancellation.raise_if_cancelled()
        try:
            line = await asyncio.wait_for(queue.get(), timeout=deadline)
        except asyncio.TimeoutError:
            raise ProviderUnavailable(
                f"{provider} timed out after {timeout}s", provider=provider
            )
        if line is None:
            break
        if line.startswith("__HTTP_ERROR__"):
            _raise_http_error(line, provider)
        if line.startswith("__NETWORK_ERROR__"):
            raise ProviderUnavailable(
                line[len("__NETWORK_ERROR__"):], provider=provider
            )
        if line.startswith("__ERROR__"):
            raise ProviderUnavailable(line[len("__ERROR__"):], provider=provider)
        yield line


def _raise_http_error(line: str, provider: str) -> None:
    body = line[len("__HTTP_ERROR__"):]
    status_str, _, detail = body.partition("__")
    status = int(status_str)
    if status in (400, 401, 403, 404):
        raise ProviderConfigError(
            f"{provider} rejected the request ({status}): {detail[:120]}", provider=provider
        )
    raise ProviderUnavailable(
        f"{provider} returned {status}: {detail[:120]}", provider=provider
    )
