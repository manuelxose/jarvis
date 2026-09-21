"""Cloud ElevenLabs TTS adapter (instant voice cloning, low-latency Flash model)."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import AsyncIterator, Optional

from jarvis.core.contracts import TurnContext
from jarvis.core.errors import ProviderConfigError, ProviderUnavailable

_BASE_URL = "https://api.elevenlabs.io/v1/text-to-speech"

# ponytail: wav_16000 keeps decode_wav() (stdlib wave) able to read the response
# directly with no PCM-to-WAV wrapping. 16kHz stays on ElevenLabs' non-Pro tier;
# upgrade path if quality needs improving is a higher pcm_*/wav_* sample rate.
_OUTPUT_FORMAT = "wav_16000"


class ElevenLabsTTS:
    """Synthesize text chunks through the ElevenLabs cloud API using a cloned voice."""

    def __init__(
        self,
        *,
        api_key: Optional[str],
        voice_id: str,
        language: str = "es",
        model_id: str = "eleven_flash_v2_5",
        timeout_seconds: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.voice_id = voice_id
        self.language = language
        self.model_id = model_id
        self.timeout_seconds = timeout_seconds

    async def synthesize(self, text: AsyncIterator[str], context: TurnContext) -> AsyncIterator[bytes]:
        if not self.api_key:
            raise ProviderConfigError("elevenlabs has no API key configured", provider="tts")
        if not self.voice_id:
            raise ProviderConfigError("elevenlabs has no cloned voice_id configured", provider="tts")

        loop = asyncio.get_running_loop()
        async for chunk in text:
            context.cancellation.raise_if_cancelled()
            chunk = chunk.strip()
            if not chunk:
                continue
            yield await loop.run_in_executor(None, self._synthesize_chunk, chunk)

    def _synthesize_chunk(self, chunk: str) -> bytes:
        url = f"{_BASE_URL}/{self.voice_id}?output_format={_OUTPUT_FORMAT}"
        payload = json.dumps({"text": chunk, "model_id": self.model_id}).encode("utf-8")
        request = urllib.request.Request(url, data=payload, method="POST")
        request.add_header("xi-api-key", self.api_key)
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            body = error.read(200).decode("utf-8", "replace")
            if error.code in (400, 401, 403, 404):
                raise ProviderConfigError(
                    f"elevenlabs rejected the request ({error.code}): {body}", provider="tts"
                ) from error
            raise ProviderUnavailable(
                f"elevenlabs returned {error.code}: {body}", provider="tts"
            ) from error
        except urllib.error.URLError as error:
            raise ProviderUnavailable(f"elevenlabs unreachable: {error.reason}", provider="tts") from error


def elevenlabs_available(*, api_key: Optional[str], voice_id: str) -> bool:
    """Return whether enough configuration is present to call ElevenLabs (no network probe)."""
    return bool(api_key) and bool(voice_id)
