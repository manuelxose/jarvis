from __future__ import annotations

import logging
from typing import Any

import numpy as np
from faster_whisper import WhisperModel

from voice.audio_utils import record_until_silence


LOGGER = logging.getLogger(__name__)


def _clean_text(text: str) -> str:
    return " ".join(text.strip().split())


class STTService:
    """Speech-to-text service using faster-whisper optimized for CPU."""

    def __init__(self, stt_config: dict[str, Any], audio_config: dict[str, Any]) -> None:
        self.model_size = stt_config.get("model", "small")
        self.device = stt_config.get("device", "cpu")
        self.compute_type = stt_config.get("compute_type", "int8")
        self.language = stt_config.get("language", "es")
        self.silence_duration = float(stt_config.get("silence_duration", 1.5))
        self.min_speech_duration = float(stt_config.get("min_speech_duration", 0.5))
        self.max_record_seconds = float(stt_config.get("max_record_seconds", 20.0))
        self.vad_mode = int(stt_config.get("vad_mode", 1))

        self.sample_rate = int(audio_config.get("sample_rate", 16000))
        self.channels = int(audio_config.get("channels", 1))
        self.input_device = audio_config.get("input_device")

        LOGGER.info(
            "Loading faster-whisper model '%s' on %s (%s)...",
            self.model_size,
            self.device,
            self.compute_type,
        )
        self.model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )

    def transcribe_audio(self, audio_data: np.ndarray) -> str:
        if audio_data.size == 0:
            return ""

        segments, _ = self.model.transcribe(
            audio_data,
            language=self.language,
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        text = " ".join(segment.text.strip() for segment in segments if segment.text).strip()
        return _clean_text(text)

    def transcribe_from_mic(self) -> str:
        """Record from mic until silence and return transcription."""
        try:
            audio_data = record_until_silence(
                sample_rate=self.sample_rate,
                channels=self.channels,
                silence_threshold=self.silence_duration,
                min_speech_duration=self.min_speech_duration,
                max_record_seconds=self.max_record_seconds,
                vad_mode=self.vad_mode,
                input_device_index=self.input_device,
            )
            return self.transcribe_audio(audio_data)
        except Exception as exc:
            LOGGER.exception("STT pipeline failed: %s", exc)
            return ""
