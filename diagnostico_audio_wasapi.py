"""Graba el dispositivo predeterminado de Windows WASAPI, sin STT."""
from __future__ import annotations

import io
import sys
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd


sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

wasapi = next(
    (host_api for host_api in sd.query_hostapis() if host_api["name"] == "Windows WASAPI"),
    None,
)
if wasapi is None or int(wasapi["default_input_device"]) < 0:
    raise SystemExit("[DIAG] ERROR: Windows WASAPI no tiene una entrada predeterminada.")

device_index = int(wasapi["default_input_device"])
device = sd.query_devices(device_index)
sample_rate = int(device["default_samplerate"])
duration_seconds = 5

print(
    f"[DIAG] WASAPI input={device_index}, name={device['name']}, rate={sample_rate}",
    flush=True,
)
print("[DIAG] Habla ahora durante cinco segundos...", flush=True)
audio = sd.rec(
    int(duration_seconds * sample_rate),
    samplerate=sample_rate,
    channels=1,
    dtype="int16",
    device=device_index,
    blocking=True,
)

rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2))) if audio.size else 0.0
output_path = Path(__file__).resolve().parent / "diagnostico_audio_wasapi.wav"
with wave.open(str(output_path), "wb") as output_file:
    output_file.setnchannels(1)
    output_file.setsampwidth(2)
    output_file.setframerate(sample_rate)
    output_file.writeframes(audio.tobytes())

print(f"[DIAG] WAV guardado: {output_path}  rms={rms:.0f}", flush=True)
