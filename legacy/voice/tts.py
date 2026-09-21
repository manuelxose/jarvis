from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from legacy.cache.audio_cache import AudioCache, pregenerate_common_responses
from legacy.voice.audio_utils import play_audio
from legacy.voice.runtime_support import timed_phase
from legacy.voice.sapi_tts import synthesize_to_file as synthesize_with_sapi


LOGGER = logging.getLogger(__name__)

VOICE_UNSAFE_MARKDOWN = re.compile(r"[`*_#>\[\]\(\)]")


def sanitize_voice_text(text: str) -> str:
    cleaned = VOICE_UNSAFE_MARKDOWN.sub("", text)
    return " ".join(cleaned.strip().split())


class TTSService:
    """Text-to-speech service with Windows SAPI and optional XTTS fallback."""

    def __init__(
        self,
        tts_config: dict[str, Any],
        cache: AudioCache,
        base_dir: Path,
    ) -> None:
        self.model_name = tts_config.get("model", "tts_models/multilingual/multi-dataset/xtts_v2")
        self.provider = str(tts_config.get("provider", "xtts")).strip().lower()
        self.language = tts_config.get("language", "es")
        self.sapi_rate = int(tts_config.get("sapi_rate", 175))
        self.sapi_volume = float(tts_config.get("sapi_volume", 1.0))
        self.cache_enabled = bool(tts_config.get("cache_enabled", True))
        self.auto_accept_cpml = bool(tts_config.get("auto_accept_cpml", True))
        self.device = str(tts_config.get("device", "")).strip().lower() or None

        speaker_dir_value = tts_config.get("speaker_wav_dir", "voice_samples/")
        speaker_dir = Path(speaker_dir_value)
        if not speaker_dir.is_absolute():
            speaker_dir = base_dir / speaker_dir

        self.speaker_wavs = sorted(speaker_dir.glob("*.wav"))
        if self.provider == "xtts" and not self.speaker_wavs:
            raise FileNotFoundError(
                f"No WAV voice samples found in: {speaker_dir}. Add sample*.wav files first."
            )

        self.cache = cache
        if self.provider == "xtts" and self.auto_accept_cpml:
            # XTTS-v2 is distributed under CPML terms. This enables non-interactive startup.
            os.environ["COQUI_TOS_AGREED"] = "1"
            LOGGER.info("COQUI_TOS_AGREED=1 habilitado para inicializacion no interactiva de XTTS.")

        self.engine = None
        if self.provider == "xtts":
            LOGGER.info("XTTS model deferred until first synthesis: %s", self.model_name)
        else:
            LOGGER.info("TTS provider selected: %s (rate=%d)", self.provider, self.sapi_rate)

    def _pick_device(self) -> str:
        if self.device in {"cpu", "cuda"}:
            return self.device
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
        except Exception:
            pass
        return "cpu"

    def _ensure_engine(self) -> None:
        if self.engine is not None:
            return
        import torch

        # torch cu118 can load an incompatible cuDNN from PATH (e.g. Ollama's)
        # and hard-crash with "cudnnGetLibConfig ... error 127" (0xC0000409).
        # Native implementations are enough for XTTS on GPU.
        torch.backends.cudnn.enabled = False
        from TTS.api import TTS

        device = self._pick_device()
        with timed_phase(LOGGER, "tts_model_load"):
            LOGGER.info("Loading XTTS model '%s' on %s...", self.model_name, device)
            self.engine = TTS(self.model_name)
            try:
                self.engine.to(device)
            except Exception as exc:
                LOGGER.warning("TTS engine .to('%s') failed (%s); continuing with default device.", device, exc)

    def synthesize_to_file(self, text: str, output_path: Path) -> Path:
        clean_text = sanitize_voice_text(text)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.provider in {"sapi", "pyttsx3", "system"}:
            with timed_phase(LOGGER, "synthesis"):
                return synthesize_with_sapi(
                    clean_text,
                    output_path,
                    language=self.language,
                    rate=self.sapi_rate,
                    volume=self.sapi_volume,
                )
        if self.provider != "xtts":
            raise ValueError(f"Proveedor TTS no soportado: {self.provider}")
        self._ensure_engine()
        try:
            with timed_phase(LOGGER, "synthesis"):
                self.engine.tts_to_file(
                    text=clean_text,
                    file_path=str(output_path),
                    speaker_wav=[str(wav) for wav in self.speaker_wavs],
                    language=self.language,
                )
        except Exception as exc:
            message = str(exc).lower()
            if "out of memory" not in message and "cuda" not in message:
                raise
            LOGGER.warning("GPU synthesis failed (%s); retrying on CPU once.", exc)
            try:
                self.engine.to("cpu")
            except Exception:
                pass
            self.engine.tts_to_file(
                text=clean_text,
                file_path=str(output_path),
                speaker_wav=[str(wav) for wav in self.speaker_wavs],
                language=self.language,
            )
        return output_path

    def generate_speech(self, text: str) -> Path:
        clean_text = sanitize_voice_text(text)
        if not clean_text:
            clean_text = "..."

        if self.cache_enabled:
            cached = self.cache.get_cached_audio(clean_text)
            if cached:
                return cached

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            temp_path = Path(tmp.name)

        self.synthesize_to_file(clean_text, temp_path)
        audio_bytes = temp_path.read_bytes()
        temp_path.unlink(missing_ok=True)

        if self.cache_enabled:
            return self.cache.cache_audio(clean_text, audio_bytes)

        fallback_path = self.cache.cache_dir / "last_response.wav"
        fallback_path.write_bytes(audio_bytes)
        return fallback_path

    def speak(self, text: str, blocking: bool = True) -> Path:
        audio_path = self.generate_speech(text)
        play_audio(audio_path, blocking=blocking)
        return audio_path

    def pregenerate_common_cache(self, background: bool = True) -> None:
        pregenerate_common_responses(
            synthesizer=lambda phrase, path: self.synthesize_to_file(phrase, path),
            cache=self.cache,
            background=background,
        )
