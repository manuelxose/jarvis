from __future__ import annotations

import io
import logging
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pyaudio
import sounddevice as sd
import soundfile as sf
import webrtcvad


LOGGER = logging.getLogger(__name__)

_MIC_HINTS = ("mic", "microphone", "array", "frontmic", "input")
_BAD_INPUT_HINTS = ("stereo mix", "output", "speaker", "loopback", "mapper")


def get_audio_devices() -> list[dict[str, Any]]:
    """Return available audio devices from PortAudio."""
    pa = pyaudio.PyAudio()
    devices: list[dict[str, Any]] = []
    try:
        for index in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(index)
            devices.append(
                {
                    "index": index,
                    "name": info.get("name"),
                    "max_input_channels": int(info.get("maxInputChannels", 0)),
                    "max_output_channels": int(info.get("maxOutputChannels", 0)),
                    "default_sample_rate": float(info.get("defaultSampleRate", 0)),
                }
            )
    finally:
        pa.terminate()
    return devices


def get_audio_device(index: int) -> dict[str, Any] | None:
    for device in get_audio_devices():
        if int(device["index"]) == int(index):
            return device
    return None


def format_audio_device(index: int | None) -> str:
    if index is None:
        return "default device"
    info = get_audio_device(index)
    if not info:
        return f"index={index} (no info)"
    return f"index={index}, name={info.get('name')}"


def _score_input_device(device: dict[str, Any], target_sample_rate: int, channels: int) -> float:
    if int(device.get("max_input_channels", 0)) < channels:
        return float("-inf")

    name = str(device.get("name", "")).lower()
    score = 0.0

    if any(hint in name for hint in _BAD_INPUT_HINTS):
        score -= 60.0
    if any(hint in name for hint in _MIC_HINTS):
        score += 50.0

    score += min(int(device.get("max_input_channels", 0)), 4) * 2.0

    default_rate = float(device.get("default_sample_rate", 0.0))
    if default_rate > 0:
        diff = abs(default_rate - target_sample_rate)
        score += max(0.0, 25.0 - (diff / 1000.0))

    return score


def _can_open_input_device(index: int | None, sample_rate: int, channels: int) -> bool:
    pa = pyaudio.PyAudio()
    stream = None
    try:
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=sample_rate,
            input=True,
            frames_per_buffer=512,
            input_device_index=index,
        )
        return True
    except Exception:
        return False
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        pa.terminate()


def resolve_input_device(
    preferred_index: int | None,
    sample_rate: int = 16000,
    channels: int = 1,
    auto_select: bool = True,
) -> int | None:
    """
    Return a working microphone index.

    Priority:
    1) Explicit index from config (if valid and usable).
    2) PortAudio default input device (if usable).
    3) Best scored available input device.
    4) None (let PortAudio decide).
    """
    if preferred_index is not None:
        if _can_open_input_device(preferred_index, sample_rate=sample_rate, channels=channels):
            return int(preferred_index)
        LOGGER.warning(
            "Configured input_device=%s is not usable. Falling back to auto selection.",
            preferred_index,
        )

    if not auto_select:
        return None

    pa = pyaudio.PyAudio()
    try:
        try:
            default_info = pa.get_default_input_device_info()
            default_index = int(default_info["index"])
        except Exception:
            default_index = None
    finally:
        pa.terminate()

    if default_index is not None and _can_open_input_device(
        default_index,
        sample_rate=sample_rate,
        channels=channels,
    ):
        default_device = get_audio_device(default_index)
        if default_device is not None:
            default_score = _score_input_device(
                default_device,
                target_sample_rate=sample_rate,
                channels=channels,
            )
            if default_score >= 0:
                return default_index

    devices = get_audio_devices()
    candidates = [
        device
        for device in devices
        if int(device.get("max_input_channels", 0)) >= channels
    ]
    candidates.sort(
        key=lambda item: _score_input_device(
            item,
            target_sample_rate=sample_rate,
            channels=channels,
        ),
        reverse=True,
    )

    for device in candidates:
        index = int(device["index"])
        if _can_open_input_device(index, sample_rate=sample_rate, channels=channels):
            return index

    return None


def play_beep(
    frequency: int = 880,
    duration: float = 0.15,
    sample_rate: int = 16000,
    volume: float = 0.25,
) -> None:
    """Play a short confirmation beep."""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    wave = (np.sin(2 * np.pi * frequency * t) * volume).astype(np.float32)
    sd.play(wave, samplerate=sample_rate, blocking=True)


def _read_audio_source(
    audio_path_or_bytes: str | Path | bytes, default_sample_rate: int
) -> tuple[np.ndarray, int]:
    if isinstance(audio_path_or_bytes, (str, Path)):
        data, sample_rate = sf.read(str(audio_path_or_bytes), dtype="float32", always_2d=False)
        return np.asarray(data, dtype=np.float32), int(sample_rate)

    if isinstance(audio_path_or_bytes, bytes):
        with io.BytesIO(audio_path_or_bytes) as buffer:
            data, sample_rate = sf.read(buffer, dtype="float32", always_2d=False)
        return np.asarray(data, dtype=np.float32), int(sample_rate)

    raise TypeError("audio_path_or_bytes must be path or bytes.")


def play_audio(
    audio_path_or_bytes: str | Path | bytes,
    blocking: bool = False,
    default_sample_rate: int = 24000,
) -> None:
    """Play WAV data from path or bytes."""

    def _playback() -> None:
        audio_data, sample_rate = _read_audio_source(audio_path_or_bytes, default_sample_rate)
        sd.play(audio_data, samplerate=sample_rate, blocking=True)

    if blocking:
        _playback()
        return

    thread = threading.Thread(target=_playback, daemon=True)
    thread.start()


def record_until_silence(
    sample_rate: int = 16000,
    channels: int = 1,
    silence_threshold: float = 1.5,
    min_speech_duration: float = 0.5,
    max_record_seconds: float = 20.0,
    vad_mode: int = 2,
    frame_duration_ms: int = 30,
    input_device_index: int | None = None,
) -> np.ndarray:
    """
    Record from microphone and stop after trailing silence.

    Returns float32 mono PCM in range [-1.0, 1.0].
    """
    if frame_duration_ms not in {10, 20, 30}:
        raise ValueError("frame_duration_ms must be 10, 20 or 30")

    vad = webrtcvad.Vad(vad_mode)
    chunk_size = int(sample_rate * frame_duration_ms / 1000)
    frame_seconds = chunk_size / sample_rate

    pa = pyaudio.PyAudio()
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=sample_rate,
        input=True,
        frames_per_buffer=chunk_size,
        input_device_index=input_device_index,
    )

    speech_started = False
    speech_duration = 0.0
    trailing_silence = 0.0
    collected_frames: list[bytes] = []
    start_time = time.monotonic()

    try:
        while time.monotonic() - start_time < max_record_seconds:
            frame = stream.read(chunk_size, exception_on_overflow=False)
            is_speech = vad.is_speech(frame, sample_rate)

            if is_speech:
                speech_started = True
                speech_duration += frame_seconds
                trailing_silence = 0.0
                collected_frames.append(frame)
                continue

            if not speech_started:
                continue

            trailing_silence += frame_seconds
            collected_frames.append(frame)
            if speech_duration >= min_speech_duration and trailing_silence >= silence_threshold:
                break
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()

    if not collected_frames:
        return np.array([], dtype=np.float32)

    audio_int16 = np.frombuffer(b"".join(collected_frames), dtype=np.int16)
    audio_float32 = audio_int16.astype(np.float32) / 32768.0
    return audio_float32
