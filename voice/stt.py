from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
from faster_whisper import WhisperModel

from voice.audio_utils import record_until_silence


LOGGER = logging.getLogger(__name__)


def _clean_text(text: str) -> str:
    return " ".join(text.strip().split())


def _prepare_for_whisper(audio_data: np.ndarray) -> np.ndarray:
    if audio_data.size == 0:
        return audio_data

    audio = np.asarray(audio_data, dtype=np.float32).copy()
    audio -= float(np.mean(audio))

    # Lightweight pre-emphasis to reduce low-frequency rumble.
    if audio.size > 1:
        audio[1:] = audio[1:] - 0.97 * audio[:-1]

    peak = float(np.max(np.abs(audio)))
    if peak > 1e-6:
        audio = (audio / peak) * 0.9
    return audio


def _resample_linear(audio_data: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if audio_data.size == 0 or source_rate == target_rate:
        return audio_data.astype(np.float32, copy=False)

    duration = float(audio_data.size) / float(source_rate)
    if duration <= 0.0:
        return np.array([], dtype=np.float32)

    target_size = max(1, int(round(duration * float(target_rate))))
    src_x = np.linspace(0.0, duration, num=audio_data.size, endpoint=False)
    dst_x = np.linspace(0.0, duration, num=target_size, endpoint=False)
    resampled = np.interp(dst_x, src_x, audio_data).astype(np.float32)
    return resampled


class STTService:
    """Speech-to-text service using faster-whisper optimized for CPU."""

    def __init__(self, stt_config: dict[str, Any], audio_config: dict[str, Any]) -> None:
        self.model_size = stt_config.get("model", "small")
        self.device = stt_config.get("device", "cpu")
        self.compute_type = stt_config.get("compute_type", "int8")
        raw_language = stt_config.get("language", "es")
        if isinstance(raw_language, str) and raw_language.strip().lower() in {"", "auto", "detect"}:
            self.language: str | None = None
        else:
            self.language = str(raw_language) if raw_language is not None else None
        initial_prompt = stt_config.get("initial_prompt", "")
        self.initial_prompt = str(initial_prompt).strip() if initial_prompt is not None else ""
        self.no_speech_threshold = float(stt_config.get("no_speech_threshold", 0.75))
        self.log_prob_threshold = float(stt_config.get("log_prob_threshold", -1.2))
        self.temperature = float(stt_config.get("temperature", 0.0))
        self.silence_duration = float(stt_config.get("silence_duration", 1.5))
        self.min_speech_duration = float(stt_config.get("min_speech_duration", 0.5))
        self.max_record_seconds = float(stt_config.get("max_record_seconds", 20.0))
        self.vad_mode = int(stt_config.get("vad_mode", 1))
        self.whisper_vad_filter = bool(stt_config.get("whisper_vad_filter", True))
        self.whisper_beam_size = int(stt_config.get("whisper_beam_size", 1))

        self.capture_sample_rate = int(audio_config.get("sample_rate", 16000))
        self.model_sample_rate = 16000
        self.channels = int(audio_config.get("channels", 1))
        self.input_device = audio_config.get("input_device")
        self.capture_backend = audio_config.get("capture_backend")

        LOGGER.info(
            "Loading faster-whisper model '%s' on %s (%s)...",
            self.model_size,
            self.device,
            self.compute_type,
        )
        model_load_start = time.monotonic()
        self.model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )
        LOGGER.info(
            "STT model '%s' loaded in %.2fs (%s/%s).",
            self.model_size,
            time.monotonic() - model_load_start,
            self.device,
            self.compute_type,
        )

    def transcribe_audio(self, audio_data: np.ndarray) -> str:
        if audio_data.size == 0:
            return ""
        prepared = _prepare_for_whisper(audio_data)
        if self.capture_sample_rate != self.model_sample_rate:
            prepared = _resample_linear(
                prepared,
                source_rate=self.capture_sample_rate,
                target_rate=self.model_sample_rate,
            )

        kwargs: dict[str, Any] = {
            "beam_size": self.whisper_beam_size,
            "vad_filter": self.whisper_vad_filter,
            "condition_on_previous_text": False,
            "temperature": self.temperature,
            "no_speech_threshold": self.no_speech_threshold,
            "log_prob_threshold": self.log_prob_threshold,
        }
        if self.language:
            kwargs["language"] = self.language
        if self.initial_prompt:
            kwargs["initial_prompt"] = self.initial_prompt

        try:
            segments, _ = self.model.transcribe(prepared, **kwargs)
        except ValueError as exc:
            # When auto language detection receives no speech probabilities,
            # faster-whisper can raise "max() arg is an empty sequence".
            if self.language is None and "empty sequence" in str(exc).lower():
                return ""
            raise
        text = " ".join(segment.text.strip() for segment in segments if segment.text).strip()
        return _clean_text(text)

    def transcribe_from_mic(self) -> str:
        """Record from mic until silence and return transcription."""
        try:
            capture_start = time.monotonic()
            audio_data = record_until_silence(
                sample_rate=self.capture_sample_rate,
                channels=self.channels,
                silence_threshold=self.silence_duration,
                min_speech_duration=self.min_speech_duration,
                max_record_seconds=self.max_record_seconds,
                vad_mode=self.vad_mode,
                input_device_index=self.input_device,
                backend=self.capture_backend,
            )
            capture_elapsed = time.monotonic() - capture_start
            audio_seconds = float(audio_data.size) / float(self.capture_sample_rate) if audio_data.size else 0.0
            LOGGER.info(
                "STT captured %.2fs audio in %.2fs (samples=%d, sample_rate=%d).",
                audio_seconds,
                capture_elapsed,
                int(audio_data.size),
                self.capture_sample_rate,
            )

            transcribe_start = time.monotonic()
            text = self.transcribe_audio(audio_data)
            transcribe_elapsed = time.monotonic() - transcribe_start
            LOGGER.info(
                "STT transcription finished in %.2fs (chars=%d).",
                transcribe_elapsed,
                len(text),
            )
            return text
        except Exception as exc:
            LOGGER.exception("STT pipeline failed: %s", exc)
            return ""

    def transcribe_for_wake(
        self,
        max_record_seconds: float = 2.2,
        silence_duration: float = 0.45,
        min_speech_duration: float = 0.2,
    ) -> str:
        """
        Lightweight mic capture for wake fallback by STT phrase matching.
        """
        try:
            audio_data = record_until_silence(
                sample_rate=self.capture_sample_rate,
                channels=self.channels,
                silence_threshold=silence_duration,
                min_speech_duration=min_speech_duration,
                max_record_seconds=max_record_seconds,
                vad_mode=self.vad_mode,
                input_device_index=self.input_device,
                backend=self.capture_backend,
            )
            return self.transcribe_audio(audio_data)
        except Exception as exc:
            LOGGER.debug("STT wake fallback failed: %s", exc)
            return ""

    def set_capture_sample_rate(self, sample_rate: int) -> None:
        self.capture_sample_rate = int(sample_rate)
