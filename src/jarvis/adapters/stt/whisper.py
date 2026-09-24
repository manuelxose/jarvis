"""Local faster-whisper STT adapter (lazy-imported; CUDA with CPU fallback)."""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import sys
import threading
import unicodedata
from typing import AsyncIterator, Optional

from jarvis.core.contracts import Transcript, TurnContext
from jarvis.core.errors import ProviderUnavailable

logger = logging.getLogger(__name__)


def whisper_available() -> bool:
    """Return True when ``faster_whisper`` is importable."""
    try:
        import faster_whisper  # noqa: F401

        return True
    except ImportError:
        return False


# Whisper's training data leaks these credits into noise-only audio.
_HALLUCINATED_PHRASES = ("subtitulos realizados", "subtitulos por", "amara.org", "gracias por ver")


def _is_hallucination(segment) -> bool:
    """Drop segments Whisper itself flags as non-speech or degenerate."""
    if segment.no_speech_prob > 0.6 and segment.avg_logprob < -1.0:
        return True
    if segment.compression_ratio > 2.4:  # "jajajajaja...", repeated loops
        return True
    folded = "".join(
        ch for ch in unicodedata.normalize("NFKD", segment.text.lower()) if not unicodedata.combining(ch)
    )
    return any(phrase in folded for phrase in _HALLUCINATED_PHRASES)


def _add_nvidia_dll_dirs() -> None:
    """Expose pip-installed cuBLAS/cuDNN DLLs (nvidia-*-cu12 wheels) to CTranslate2 on Windows."""
    if sys.platform != "win32":
        return
    for directory in glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin")):
        os.add_dll_directory(directory)
        os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")


class WhisperSTT:
    """Transcribe int16 PCM frames with faster-whisper (CUDA when configured, else CPU)."""

    def __init__(
        self,
        *,
        model: str = "tiny",
        language: Optional[str] = "es",
        device: str = "cpu",
        compute_type: Optional[str] = None,
        sample_rate: int = 16000,
        hotwords: Optional[str] = None,
    ) -> None:
        # Biases decoding toward the wake word so "Jarvis" isn't heard as "Javi".
        self.hotwords = hotwords
        self.model_size = model
        self.language = language
        self.device = device
        self.compute_type = compute_type or ("int8_float16" if device == "cuda" else "int8")
        self.sample_rate = sample_rate
        self._model = None
        self._load_lock = threading.Lock()  # startup warm-up and first turn may race

    def _load_model(self):
        with self._load_lock:
            return self._load_model_locked()

    def _load_model_locked(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel  # noqa: PLC0415
            except ImportError as error:
                raise ProviderUnavailable(
                    "faster-whisper is not installed; local STT unavailable", provider="stt"
                ) from error
            if self.device == "cuda":
                _add_nvidia_dll_dirs()
                try:
                    model = self._build(WhisperModel, "cuda", self.compute_type)
                    # CUDA libraries load lazily: run one tiny pass so a missing
                    # cuBLAS/cuDNN fails here (and falls back) instead of mid-turn.
                    # It also pays the first-kernel warm-up before the user speaks.
                    import numpy as np  # noqa: PLC0415

                    list(model.transcribe(np.zeros(8000, dtype=np.float32), language=self.language)[0])
                    self._model = model
                except Exception as error:  # noqa: BLE001 - any GPU failure degrades to CPU
                    logger.warning("whisper CUDA unavailable (%s); falling back to CPU", error)
                    self.device, self.compute_type = "cpu", "int8"
            if self._model is None:
                self._model = self._build(WhisperModel, self.device, self.compute_type)
        return self._model

    def _build(self, whisper_model, device: str, compute_type: str):
        return whisper_model(
            self.model_size,
            device=device,
            compute_type=compute_type,
            local_files_only=True,
            # ponytail: half the logical cores approximates physical cores;
            # CTranslate2 defaults to 4 threads regardless of the CPU.
            cpu_threads=max(4, (os.cpu_count() or 4) // 2),
        )

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
        if self.hotwords:
            kwargs["hotwords"] = self.hotwords

        loop = asyncio.get_running_loop()

        def _transcribe() -> str:
            segments, _ = model.transcribe(audio_array, **kwargs)
            return " ".join(
                segment.text.strip() for segment in segments if not _is_hallucination(segment)
            )

        text = await loop.run_in_executor(None, _transcribe)
        yield Transcript(text, is_final=True)
