"""Graba un micrófono WASAPI seleccionado automáticamente, sin STT."""
from __future__ import annotations

import io
import sys
import wave
from pathlib import Path

import numpy as np
import pyaudio
import sounddevice as sd
from voice.audio_utils import _capture_callback_audio, _probe_native_rms
from voice.directshow_audio import (
    DirectShowCaptureError,
    capture_first_available,
)
from voice.runtime_support import CaptureBackend


sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

devices = list(sd.query_devices())
hostapi_index = None
hostapi = None
device_index = -1
first_open = None
for candidate_hostapi_index, candidate_hostapi in enumerate(sd.query_hostapis()):
    if not any(name in candidate_hostapi["name"].upper() for name in ("WASAPI", "WDM-KS")):
        continue
    candidate_indices = []
    default_index = int(candidate_hostapi["default_input_device"])
    if (
        0 <= default_index < len(devices)
        and int(devices[default_index].get("hostapi", -1)) == candidate_hostapi_index
        and int(devices[default_index].get("max_input_channels", 0)) > 0
    ):
        candidate_indices.append(default_index)
    candidate_indices.extend(
        index
        for index, device in enumerate(devices)
        if int(device.get("hostapi", -1)) == candidate_hostapi_index
        and int(device.get("max_input_channels", 0)) > 0
        and "stereo mix" not in str(device.get("name", "")).lower()
        and "mezcla" not in str(device.get("name", "")).lower()
        and index not in candidate_indices
    )
    for candidate_index in candidate_indices:
        device = devices[candidate_index]
        backend = CaptureBackend(
            "WDM-KS" if "WDM-KS" in candidate_hostapi["name"].upper() else "WASAPI",
            candidate_index,
            str(device["name"]),
            int(device["default_samplerate"]),
        )
        rms = _probe_native_rms(backend, 1)
        if rms < 0:
            print(f"[DIAG] Endpoint rechazado: {backend.describe()}", flush=True)
            continue
        if first_open is None:
            first_open = (candidate_hostapi_index, candidate_hostapi, candidate_index)
        if rms < 1.0:
            print(f"[DIAG] Endpoint sin señal: {backend.describe()} rms={rms:.0f}", flush=True)
            continue
        hostapi_index, hostapi, device_index = (
            candidate_hostapi_index,
            candidate_hostapi,
            candidate_index,
        )
        break
    if hostapi is not None:
        break

if hostapi is None and first_open is not None:
    hostapi_index, hostapi, device_index = first_open
    print("[DIAG] Ninguna entrada dio señal durante la selección; probando la primera que abrió.", flush=True)

if hostapi is None:
    raise SystemExit("[DIAG] ERROR: Windows WASAPI/WDM-KS no tiene un micrófono utilizable.")
device = sd.query_devices(device_index)
sample_rate = int(device["default_samplerate"])
duration_seconds = 5

print(
    f"[DIAG] {hostapi['name']} input={device_index}, name={device['name']}, rate={sample_rate}",
    flush=True,
)
print("[DIAG] Habla ahora durante cinco segundos...", flush=True)
try:
    if "WDM-KS" in hostapi["name"].upper():
        audio = _capture_callback_audio(
            CaptureBackend("WDM-KS", device_index, str(device["name"]), sample_rate),
            duration_seconds,
            1,
        )
    else:
        audio = sd.rec(
            int(duration_seconds * sample_rate),
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            device=device_index,
            blocking=True,
        )
except Exception as sounddevice_error:
    print(f"[DIAG] Captura nativa no pudo abrirse: {sounddevice_error}", flush=True)
    print("[DIAG] Reintentando el mismo índice mediante PyAudio...", flush=True)
    pa = pyaudio.PyAudio()
    stream = None
    try:
        chunk_size = 1024
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=sample_rate,
            input=True,
            frames_per_buffer=chunk_size,
            input_device_index=device_index,
        )
        frames = [
            stream.read(chunk_size, exception_on_overflow=False)
            for _ in range(max(1, int(duration_seconds * sample_rate / chunk_size)))
        ]
        audio = np.frombuffer(b"".join(frames), dtype=np.int16).reshape(-1, 1)
    except Exception as pyaudio_error:
        print(f"[DIAG] PyAudio tampoco pudo abrirlo: {pyaudio_error}", flush=True)
        print("[DIAG] Reintentando mediante FFmpeg DirectShow...", flush=True)
        try:
            directshow_device, pcm = capture_first_available(
                str(device["name"]), duration_seconds, sample_rate=16000
            )
            print(f"[DIAG] DirectShow input={directshow_device!r}, rate=16000", flush=True)
            audio = np.frombuffer(pcm, dtype=np.int16).reshape(-1, 1)
            sample_rate = 16000
        except DirectShowCaptureError as directshow_error:
            raise SystemExit(
                "[DIAG] ERROR: ninguno de los backends pudo abrir el micrófono: "
                f"{directshow_error}"
            )
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()
        pa.terminate()

rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2))) if audio.size else 0.0
output_path = Path(__file__).resolve().parent / "diagnostico_audio_wasapi.wav"
with wave.open(str(output_path), "wb") as output_file:
    output_file.setnchannels(1)
    output_file.setsampwidth(2)
    output_file.setframerate(sample_rate)
    output_file.writeframes(audio.tobytes())

print(f"[DIAG] WAV guardado: {output_path}  rms={rms:.0f}", flush=True)
