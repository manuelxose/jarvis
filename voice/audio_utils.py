from __future__ import annotations

import io
import logging
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

from voice.directshow_audio import DirectShowCaptureError, capture_first_available
from voice.runtime_support import CaptureBackend

import numpy as np
import pyaudio
import sounddevice as sd
import soundfile as sf
import webrtcvad


LOGGER = logging.getLogger(__name__)

_MIC_HINTS = ("mic", "microphone", "micrófono", "array", "frontmic", "input")
_BAD_INPUT_HINTS = (
    "stereo mix",
    "mezcla",
    "output",
    "speaker",
    "loopback",
    "mapper",
    "controlador primario",
    "primary sound capture",
    "microsoft sound mapper",
)


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


def resolve_capture_backend(
    preferred_index: int | None = None,
    sample_rate: int = 16000,
    channels: int = 1,
    verify: bool = False,
) -> CaptureBackend:
    """Resolve the backend used by both microphone checks and STT capture."""
    reason: str | None = None
    if sys.platform == "win32":
        try:
            hostapis = list(sd.query_hostapis())
            all_devices = list(sd.query_devices())
            native_hostapis = [
                (index, host)
                for index, host in enumerate(hostapis)
                if any(
                    name in str(host.get("name", "")).upper()
                    for name in ("WASAPI", "WDM-KS")
                )
            ]
            native_candidates: list[tuple[int, float, int, dict[str, Any], str, int]] = []
            for hostapi_index, hostapi in native_hostapis:
                default_index = int(hostapi.get("default_input_device", -1))
                candidates: list[tuple[int, float, int, dict[str, Any]]] = []
                for candidate, device in enumerate(all_devices):
                    if int(device.get("hostapi", -1)) != hostapi_index:
                        continue
                    score = _score_input_device(device, sample_rate, channels)
                    if score == float("-inf"):
                        continue
                    priority = 2 if candidate == preferred_index else 1 if candidate == default_index else 0
                    candidates.append((priority, score, candidate, device))
                if not candidates:
                    continue
                backend_name = "WDM-KS" if "WDM-KS" in str(hostapi.get("name", "")).upper() else "WASAPI"
                for priority, score, candidate, device in sorted(candidates, reverse=True):
                    native_rate = int(float(device.get("default_samplerate", sample_rate)))
                    native_candidates.append(
                        (priority, score, candidate, device, backend_name, native_rate)
                    )

            native_candidates.sort(
                key=lambda item: (item[0], item[1], item[4] == "WASAPI"),
                reverse=True,
            )
            first_open: CaptureBackend | None = None
            for _, _, candidate, device, backend_name, native_rate in native_candidates:
                backend = CaptureBackend(
                    backend_name,
                    candidate,
                    str(device.get("name", "unknown")),
                    native_rate,
                )
                if verify:
                    rms = _probe_native_rms(backend, channels)
                    LOGGER.info("Native input probe: %s rms=%.2f", backend.describe(), rms)
                    if rms < 0:
                        continue
                    if first_open is None:
                        first_open = backend
                    if rms < 1.0:
                        continue
                LOGGER.info("Audio capture resolved: %s", backend.describe())
                return backend
            if first_open is not None:
                LOGGER.warning("All native inputs were silent; using first open endpoint: %s", first_open.describe())
                return first_open
            raise RuntimeError("Windows has no usable native audio input device")
        except Exception as exc:
            reason = f"WASAPI unavailable: {exc}"
            LOGGER.warning("Audio capture fallback requested: %s", reason)
    backend = CaptureBackend("PyAudio", preferred_index, "PyAudio fallback", sample_rate, reason if sys.platform == "win32" else None)
    LOGGER.info("Audio capture resolved: %s", backend.describe())
    return backend


def _probe_native_rms(backend: CaptureBackend, channels: int, probe_seconds: float = 0.25) -> float:
    try:
        if backend.requires_callback:
            audio = _capture_callback_audio(backend, probe_seconds, channels)
        else:
            audio = sd.rec(
                max(1, int(backend.sample_rate * probe_seconds)),
                samplerate=backend.sample_rate,
                channels=channels,
                dtype="int16",
                device=backend.device_index,
                blocking=True,
            )
        if not audio.size:
            return 0.0
        samples = audio.astype(np.float32)
        return float(np.sqrt(np.mean(np.square(samples))))
    except Exception as exc:
        LOGGER.warning("Native input probe failed: %s (%s)", backend.describe(), exc)
        return -1.0


def _wasapi_default_input() -> tuple[int, int] | None:
    backend = resolve_capture_backend() if sys.platform == "win32" else None
    if backend is None or not backend.is_wasapi or backend.device_index is None:
        return None
    return (backend.device_index, backend.sample_rate)


def _score_input_device(device: dict[str, Any], target_sample_rate: int, channels: int) -> float:
    if int(device.get("max_input_channels", 0)) < channels:
        return float("-inf")

    name = str(device.get("name", "")).lower()
    score = 0.0

    if any(hint in name for hint in _BAD_INPUT_HINTS):
        return float("-inf")
    if any(hint in name for hint in _MIC_HINTS):
        score += 50.0

    score += min(int(device.get("max_input_channels", 0)), 4) * 2.0

    default_rate = float(
        device.get("default_sample_rate", device.get("default_samplerate", 0.0))
    )
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


def _probe_input_rms(
    index: int | None,
    sample_rate: int,
    channels: int,
    probe_seconds: float = 0.4,
) -> float:
    pa = pyaudio.PyAudio()
    stream = None
    try:
        chunk = 512
        loops = max(1, int((sample_rate * probe_seconds) / chunk))
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=sample_rate,
            input=True,
            frames_per_buffer=chunk,
            input_device_index=index,
        )

        rms_values: list[float] = []
        for _ in range(loops):
            frame = stream.read(chunk, exception_on_overflow=False)
            pcm = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
            if pcm.size == 0:
                continue
            rms_values.append(float(np.sqrt(np.mean(np.square(pcm)))))

        if not rms_values:
            return 0.0
        return float(np.median(np.asarray(rms_values, dtype=np.float32)))
    except Exception:
        return 0.0
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        pa.terminate()


def _capture_callback_audio(
    backend: CaptureBackend,
    duration_seconds: float,
    channels: int,
) -> np.ndarray:
    """Capture WDM-KS input through its callback-only PortAudio API."""
    frames: queue.Queue[np.ndarray] = queue.Queue()

    def callback(indata, frame_count, time_info, status) -> None:
        if status:
            LOGGER.warning("Audio callback status: %s", status)
        if frame_count:
            frames.put(np.asarray(indata, dtype=np.int16).copy())

    stream = sd.InputStream(
        samplerate=backend.sample_rate,
        channels=channels,
        dtype="int16",
        blocksize=max(1, int(backend.sample_rate * 0.03)),
        device=backend.device_index,
        callback=callback,
    )
    collected: list[np.ndarray] = []
    deadline = time.monotonic() + duration_seconds
    try:
        stream.start()
        while time.monotonic() < deadline:
            try:
                collected.append(frames.get(timeout=min(0.25, max(0.01, deadline - time.monotonic()))))
            except queue.Empty:
                continue
    finally:
        try:
            stream.stop()
        finally:
            stream.close()
    if not collected:
        return np.empty((0, channels), dtype=np.int16)
    return np.concatenate(collected, axis=0)


def _capture_directshow_audio(
    preferred_device_name: str | None,
    duration_seconds: float,
    sample_rate: int,
) -> tuple[np.ndarray, CaptureBackend]:
    device_name, pcm = capture_first_available(
        preferred_device_name,
        duration_seconds,
        sample_rate=sample_rate,
    )
    audio = np.frombuffer(pcm, dtype=np.int16).reshape(-1, 1)
    if not audio.size:
        raise DirectShowCaptureError("FFmpeg DirectShow devolvio una captura vacia.")
    return audio, CaptureBackend("DirectShow", None, device_name, sample_rate)


def resolve_input_device(
    preferred_index: int | None,
    sample_rate: int = 16000,
    channels: int = 1,
    auto_select: bool = True,
) -> int | None:
    """
    Return a working microphone index.

    Priority:
    1) Keep preferred/default only if they are usable.
    2) Score all usable input devices.
    3) Probe the top candidates and pick one with actual signal.
    4) Fallback to top score when signal probing is inconclusive.
    """
    if sys.platform == "win32":
        backend = resolve_capture_backend(preferred_index, sample_rate, channels, verify=True)
        if backend.is_wasapi:
            return backend.device_index

    if preferred_index is not None and not _can_open_input_device(
        preferred_index,
        sample_rate=sample_rate,
        channels=channels,
    ):
        LOGGER.warning(
            "Configured input_device=%s is not usable. Falling back to auto selection.",
            preferred_index,
        )
        preferred_index = None

    pa = pyaudio.PyAudio()
    try:
        try:
            default_info = pa.get_default_input_device_info()
            default_index = int(default_info["index"])
        except Exception:
            default_index = None
    finally:
        pa.terminate()

    if not auto_select:
        return preferred_index if preferred_index is not None else default_index

    devices = get_audio_devices()
    scored_candidates: list[tuple[float, int]] = []
    for device in devices:
        if int(device.get("max_input_channels", 0)) < channels:
            continue

        index = int(device["index"])
        if not _can_open_input_device(index, sample_rate=sample_rate, channels=channels):
            continue

        score = _score_input_device(
            device,
            target_sample_rate=sample_rate,
            channels=channels,
        )
        if score == float("-inf"):
            continue
        if index == preferred_index:
            score += 8.0
        if index == default_index:
            score += 4.0
        scored_candidates.append((score, index))

    if not scored_candidates:
        if preferred_index is not None:
            return preferred_index
        return default_index

    scored_candidates.sort(key=lambda item: item[0], reverse=True)

    # Probe top candidates and pick one with actual signal.
    # This avoids selecting devices that open but stay silent (common in Windows virtual inputs).
    top = scored_candidates[: min(5, len(scored_candidates))]
    best_by_signal: tuple[float, int] | None = None
    for _, index in top:
        rms = _probe_input_rms(
            index=index,
            sample_rate=sample_rate,
            channels=channels,
            probe_seconds=0.45,
        )
        LOGGER.info(
            "Input probe index=%s rms=%.2f",
            index,
            rms,
        )
        if best_by_signal is None or rms > best_by_signal[0]:
            best_by_signal = (rms, index)

    if best_by_signal is not None and best_by_signal[0] >= 3.0:
        return best_by_signal[1]

    return scored_candidates[0][1]


def check_microphone_capture(
    input_device_index: int | None,
    sample_rate: int = 16000,
    channels: int = 1,
    probe_seconds: float = 0.8,
) -> tuple[bool, float, str]:
    """
    Validate microphone capture and return (ok, rms, message).
    """
    backend = resolve_capture_backend(input_device_index, sample_rate, channels)
    if backend.is_wasapi and backend.device_index is not None:
        device_index, capture_rate = backend.device_index, backend.sample_rate
        try:
            if backend.requires_callback:
                audio = _capture_callback_audio(backend, probe_seconds, channels)
            else:
                audio = sd.rec(
                    max(1, int(capture_rate * probe_seconds)),
                    samplerate=capture_rate,
                    channels=channels,
                    dtype="int16",
                    device=device_index,
                    blocking=True,
                )
            rms = float(np.sqrt(np.mean(np.square(audio.astype(np.float32)))))
            if rms < 1.0:
                return (False, rms, f"La entrada {backend.name} seleccionada llega en silencio.")
            return (True, rms, f"Microfono {backend.name} operativo ({backend.describe()}).")
        except Exception as exc:
            native_backend_name = backend.name
            fallback_index = input_device_index
            backend = CaptureBackend("PyAudio", fallback_index, "PyAudio fallback", sample_rate, f"{native_backend_name} health check failed: {exc}")
            LOGGER.warning("Microphone health fallback: %s", backend.describe())
            input_device_index = fallback_index

    if sys.platform == "win32":
        try:
            audio, directshow_backend = _capture_directshow_audio(
                backend.device_name,
                probe_seconds,
                sample_rate,
            )
            rms = float(np.sqrt(np.mean(np.square(audio.astype(np.float32)))))
            if rms >= 1.0:
                return (True, rms, f"Microfono DirectShow operativo ({directshow_backend.describe()}).")
            LOGGER.warning("DirectShow abrió la entrada, pero llega en silencio: %s", directshow_backend.describe())
        except DirectShowCaptureError as exc:
            LOGGER.warning("DirectShow microphone health fallback failed: %s", exc)

    pa = pyaudio.PyAudio()
    stream = None
    try:
        chunk = 512
        loops = max(1, int((sample_rate * probe_seconds) / chunk))
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=sample_rate,
            input=True,
            frames_per_buffer=chunk,
            input_device_index=input_device_index,
        )

        rms_values: list[float] = []
        for _ in range(loops):
            frame = stream.read(chunk, exception_on_overflow=False)
            pcm = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
            if pcm.size == 0:
                continue
            rms_values.append(float(np.sqrt(np.mean(np.square(pcm)))))

        if not rms_values:
            return (False, 0.0, "No se recibieron frames de audio del dispositivo.")

        rms = float(np.median(np.asarray(rms_values, dtype=np.float32)))
        if rms < 1.0:
            return (
                False,
                rms,
                "El microfono abre pero llega silencio constante. "
                "Puede estar bloqueado por permisos de Windows o por otro software.",
            )

        return (True, rms, f"Microfono operativo ({backend.describe()}).")
    except Exception as exc:
        return (False, 0.0, f"No se pudo abrir/capturar audio del microfono: {exc}")
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        pa.terminate()


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


def _resample_pcm16(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or samples.size == 0:
        return samples.astype(np.int16, copy=False)

    output_size = max(1, round(samples.size * target_rate / source_rate))
    source_positions = np.arange(samples.size, dtype=np.float64)
    target_positions = np.arange(output_size, dtype=np.float64) * source_rate / target_rate
    target_positions = np.minimum(target_positions, samples.size - 1)
    return np.rint(np.interp(target_positions, source_positions, samples)).astype(np.int16)


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
    frame_seconds = frame_duration_ms / 1000
    max_frames = max(1, int(max_record_seconds / frame_seconds))

    backend = resolve_capture_backend(input_device_index, sample_rate, channels)
    directshow_preferred_name = backend.device_name
    pa = None
    stream = None
    directshow_audio: np.ndarray | None = None
    callback_frames: queue.Queue[np.ndarray] | None = None
    if backend.is_wasapi and backend.device_index is not None:
        try:
            capture_rate = backend.sample_rate
            chunk_size = int(capture_rate * frame_seconds)
            if backend.requires_callback:
                callback_frames = queue.Queue()

                def callback(indata, frame_count, time_info, status) -> None:
                    if status:
                        LOGGER.warning("Audio callback status: %s", status)
                    if frame_count:
                        callback_frames.put(np.asarray(indata, dtype=np.int16).copy())

                stream = sd.InputStream(
                    samplerate=capture_rate,
                    channels=channels,
                    dtype="int16",
                    blocksize=chunk_size,
                    device=backend.device_index,
                    callback=callback,
                )
            else:
                stream = sd.InputStream(
                    samplerate=capture_rate,
                    channels=channels,
                    dtype="int16",
                    blocksize=chunk_size,
                    device=backend.device_index,
                )
            stream.start()
            LOGGER.info("STT capture started: %s", backend.describe())
        except Exception as exc:
            backend = CaptureBackend("PyAudio", input_device_index, "PyAudio fallback", sample_rate, f"{backend.name} capture failed: {exc}")
            LOGGER.warning("%s", backend.describe())
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    LOGGER.debug("Failed to close rejected native audio stream.", exc_info=True)
            stream = None
    if stream is None and sys.platform == "win32":
        try:
            directshow_audio, backend = _capture_directshow_audio(
                directshow_preferred_name,
                max_record_seconds,
                sample_rate,
            )
            capture_rate = sample_rate
            chunk_size = int(capture_rate * frame_seconds)
            LOGGER.info("STT capture started: %s", backend.describe())
        except DirectShowCaptureError as exc:
            LOGGER.warning("DirectShow STT capture fallback failed: %s", exc)
    if stream is None and directshow_audio is None:
        pa = pyaudio.PyAudio()
        capture_rate = sample_rate
        if input_device_index is not None:
            try:
                device_info = pa.get_device_info_by_index(input_device_index)
                capture_rate = int(device_info.get("defaultSampleRate", sample_rate))
            except Exception:
                capture_rate = sample_rate
        if capture_rate <= 0:
            capture_rate = sample_rate
        chunk_size = int(capture_rate * frame_seconds)
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=capture_rate,
            input=True,
            frames_per_buffer=chunk_size,
            input_device_index=input_device_index,
        )

    speech_started = False
    speech_duration = 0.0
    trailing_silence = 0.0
    collected_frames: list[bytes] = []
    start_time = time.monotonic() - (max_record_seconds if directshow_audio is not None else 0.0)
    expected_audio_seconds = 0.0
    frames_read = 0

    try:
        while frames_read < max_frames:
            # Some Windows drivers can return frames much faster than realtime.
            # Keep capture pace near realtime to avoid collecting huge stale buffers.
            elapsed = time.monotonic() - start_time
            lead = expected_audio_seconds - elapsed
            if lead > frame_seconds:
                time.sleep(min(lead - frame_seconds, frame_seconds))

            if directshow_audio is not None:
                start = frames_read * chunk_size
                input_frame = np.asarray(directshow_audio[start : start + chunk_size, 0], dtype=np.int16)
                if input_frame.size == 0:
                    break
                if input_frame.size < chunk_size:
                    input_frame = np.pad(input_frame, (0, chunk_size - input_frame.size))
            elif backend.is_wasapi and stream is not None:
                if callback_frames is not None:
                    frame = callback_frames.get(timeout=max(1.0, frame_seconds * 2.0))
                    input_frame = np.asarray(frame[:, 0], dtype=np.int16)
                else:
                    frame, _ = stream.read(chunk_size)
                    input_frame = np.asarray(frame[:, 0], dtype=np.int16)
            else:
                frame = stream.read(chunk_size, exception_on_overflow=False)
                input_frame = np.frombuffer(frame, dtype=np.int16)
            frames_read += 1
            expected_audio_seconds += frame_seconds
            frame_int16 = _resample_pcm16(input_frame, capture_rate, sample_rate)
            if frame_int16.size > 1:
                # Reduce low-frequency rumble/DC that can trigger false VAD positives.
                hp = frame_int16.astype(np.int32)
                hp[1:] = hp[1:] - hp[:-1]
                hp = np.clip(hp, -32768, 32767).astype(np.int16)
                vad_frame = hp.tobytes()
            else:
                vad_frame = frame_int16.tobytes()
            is_speech = vad.is_speech(vad_frame, sample_rate)

            if is_speech:
                speech_started = True
                speech_duration += frame_seconds
                trailing_silence = 0.0
                collected_frames.append(frame_int16.tobytes())
                continue

            if not speech_started:
                continue

            trailing_silence += frame_seconds
            collected_frames.append(frame_int16.tobytes())
            if speech_duration >= min_speech_duration and trailing_silence >= silence_threshold:
                break
    finally:
        if stream is not None:
            if backend.is_wasapi:
                stream.stop()
            else:
                stream.stop_stream()
            stream.close()
        if pa is not None:
            pa.terminate()

    if not collected_frames:
        return np.array([], dtype=np.float32)

    audio_int16 = np.frombuffer(b"".join(collected_frames), dtype=np.int16)
    audio_float32 = audio_int16.astype(np.float32) / 32768.0
    return audio_float32
