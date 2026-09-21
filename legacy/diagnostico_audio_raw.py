"""Graba cinco segundos PCM nativo para aislar el driver del micrófono."""
from __future__ import annotations

import io
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np
import pyaudio

from legacy.voice.audio_utils import get_audio_device, resolve_input_device


sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

base_dir = Path(__file__).resolve().parent
selected_device = resolve_input_device(preferred_index=None, sample_rate=16000, channels=1)
device = get_audio_device(selected_device) if selected_device is not None else None
if not device:
    raise SystemExit("[DIAG] ERROR: no se encontró un micrófono utilizable.")

native_rate = int(device["default_sample_rate"])
raw_channels = os.getenv("JARVIS_DIAG_CHANNELS", "1")
channels = int(device["max_input_channels"]) if raw_channels == "max" else int(raw_channels)
max_channels = int(device["max_input_channels"])
if not 1 <= channels <= max_channels:
    raise SystemExit(
        f"[DIAG] ERROR: canales no válidos: {channels} (máximo={max_channels}, index={selected_device})"
    )
duration_seconds = 5
chunk_size = 1024
frame_count = int(native_rate * duration_seconds / chunk_size)
frame_seconds = chunk_size / native_rate

print(
    f"[DIAG] Usando index={selected_device}, rate nativo={native_rate}, "
    f"canales={channels}/{max_channels}",
    flush=True,
)
print("[DIAG] Habla ahora durante cinco segundos...", flush=True)

pa = pyaudio.PyAudio()
stream = None
try:
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=native_rate,
        input=True,
        frames_per_buffer=chunk_size,
        input_device_index=selected_device,
    )
    frames = []
    start_time = time.monotonic()
    for frame_index in range(frame_count):
        lead = (frame_index * frame_seconds) - (time.monotonic() - start_time)
        if lead > 0:
            time.sleep(lead)
        frames.append(stream.read(chunk_size, exception_on_overflow=False))
finally:
    if stream is not None:
        stream.stop_stream()
        stream.close()
    pa.terminate()

audio = np.frombuffer(b"".join(frames), dtype=np.int16)
audio_channels = audio.reshape(-1, channels)
channel_rms = np.sqrt(np.mean(audio_channels.astype(np.float32) ** 2, axis=0))
output_path = base_dir / "diagnostico_audio_raw.wav"
with wave.open(str(output_path), "wb") as output_file:
    output_file.setnchannels(channels)
    output_file.setsampwidth(2)
    output_file.setframerate(native_rate)
    output_file.writeframes(audio.tobytes())

print(f"[DIAG] WAV guardado: {output_path}  rms={channel_rms.round().astype(int).tolist()}", flush=True)
