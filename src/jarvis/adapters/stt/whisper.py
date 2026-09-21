"""Local faster-whisper STT adapter (lazy-imported, CPU-first)."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional

from jarvis.core.contracts import SpeechToText, Transcript, TurnContext
from jarvis.core.errors import ProviderUnavailable


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except ImportError:
        return False


class WhisperSTT:
    """Transcribe int16 PCM frames with faster-whisper on CPU."""

    def __init__(
        self,
        *,
        model: str = "tiny",
        language: Optional[str] = "es",
        device: str = "cpu",
        compute_type: str = "int8",
        sample_rate: int = 16000,
    ) -> None:
        self.model_size = model
        self.language = language
        self.device = device
        self.compute_type = compute_type
        self.sample_rate = sample_rate
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel  # noqa: PLC0415
            except ImportError as error:
                raise ProviderUnavailable(
                    "faster-whisper is not installed; local STT unavailable", provider="stt"
                ) from error
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                local_files_only=True,
            )
        return self._model

    async def transcribe(self, audio: AsyncIterator[bytes], context: TurnContext) -> AsyncIterator[Transcript]:
        model = self._load_model()

        import numpy as np  # noqa: PLC0415

        frames = bytearray()
        async for chunk in audio:
            context.cancellation.raise_if_cancelled()
            frames.extend(chunk)
        audio_array = np.frombuffer(bytes(frames), dtype=np.int16).astype(np.float32) / 32768.0

        kwargs: dict = {"beam_size": 1, "vad_filter": True, "condition_on_previous_text": False}
        if self.language:
            kwargs["language"] = self.language

        loop = asyncio.get_running_loop()

        def _transcribe() -> str:
            segments, _ = model.transcribe(audio_array, **kwargs)
            return " ".join(segment.text.strip() for segment in segments)

        text = await loop.run_in_executor(None, _transcribe)
        yield Transcript(text, is_final=True)
