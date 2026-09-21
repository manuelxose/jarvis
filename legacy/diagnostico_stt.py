"""Comprueba que el micrófono recibe y Whisper transcribe una frase normal."""
from __future__ import annotations

import io
import logging
import os
import sys
import wave
from pathlib import Path

import numpy as np
import yaml

from legacy.voice.audio_utils import format_audio_device, resolve_input_device
from legacy.voice.stt import STTService


sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="[STT] %(message)s")

base_dir = Path(__file__).resolve().parent
with (base_dir / "config.yaml").open(encoding="utf-8") as config_file:
    config = yaml.safe_load(config_file) or {}

stt_config = dict(config.get("stt", {}))
if os.getenv("JARVIS_DIAG_STT_DISABLE_VAD") == "1":
    stt_config["whisper_vad_filter"] = False
    print("[DIAG] VAD interno de Whisper desactivado para esta prueba.", flush=True)
audio_config = dict(config.get("audio", {}))
audio_config.setdefault("sample_rate", 16000)
audio_config.setdefault("channels", 1)
audio_config["input_device"] = resolve_input_device(
    preferred_index=None,
    sample_rate=int(audio_config["sample_rate"]),
    channels=int(audio_config["channels"]),
)

print(f"[DIAG] Usando {format_audio_device(audio_config['input_device'])}", flush=True)
print("[DIAG] Cargando STT...", flush=True)
stt = STTService(stt_config=stt_config, audio_config=audio_config)
print("[DIAG] Di una frase normal y espera a que termine el silencio.", flush=True)

audio_data = stt.capture_from_mic()
capture_path = base_dir / "diagnostico_stt.wav"
if audio_data.size:
    pcm = (np.clip(audio_data, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(capture_path), "wb") as capture_file:
        capture_file.setnchannels(1)
        capture_file.setsampwidth(2)
        capture_file.setframerate(stt.capture_sample_rate)
        capture_file.writeframes(pcm.tobytes())
    print(f"[DIAG] Audio guardado: {capture_path}", flush=True)

text = stt.transcribe_audio(audio_data)
if text:
    print(f"[DIAG] TRANSCRIPCION: {text}")
    print("[DIAG] RESULTADO: El micrófono y el reconocimiento de voz funcionan.")
else:
    print("[DIAG] SIN TRANSCRIPCION: no se reconoció habla.")
    print("[DIAG] Repite una frase más clara y cerca del micrófono.")
