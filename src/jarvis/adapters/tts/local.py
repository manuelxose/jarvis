"""Local Coqui XTTS-v2 TTS adapter (lazy-imported)."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional

from jarvis.core.contracts import TurnContext
from jarvis.core.errors import ProviderUnavailable


def local_tts_available() -> bool:
    """Return True when Coqui ``TTS`` (XTTS) is importable."""
    try:
        import TTS  # noqa: F401

        return True
    except ImportError:
        return False


class LocalTTS:
    """Synthesize text chunks with Coqui XTTS-v2, buffering to WAV bytes."""

    def __init__(self, *, language: str = "es", speaker_wav_dir: Optional[str] = None) -> None:
        self.language = language
        self.speaker_wav_dir = speaker_wav_dir
        self._engine = None

    def _load_engine(self):
        if self._engine is None:
            try:
                from TTS.api import TTS  # noqa: PLC0415
            except ImportError as error:
                raise ProviderUnavailable(
                    "coqui-tts is not installed; local TTS unavailable", provider="tts"
                ) from error
            self._engine = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
        return self._engine

    async def synthesize(self, text: AsyncIterator[str], context: TurnContext) -> AsyncIterator[bytes]:
        engine = self._load_engine()

        import io
        import soundfile as sf  # noqa: PLC0415

        loop = asyncio.get_running_loop()

        async for chunk in text:
            context.cancellation.raise_if_cancelled()
            chunk = chunk.strip()
            if not chunk:
                continue

            def _synth() -> bytes:
                buffer = io.BytesIO()
                wav = engine.tts(text=chunk, language=self.language)
                import numpy as np  # noqa: PLC0415

                sf.write(buffer, np.asarray(wav), 22050, format="WAV")
                return buffer.getvalue()

            data = await loop.run_in_executor(None, _synth)
            yield data
