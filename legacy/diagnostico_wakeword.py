r"""
diagnostico_wakeword.py  –  compatible CP1252 / UTF-8
======================================================
Muestra en tiempo real el score del wake word.
Ejecutar:  .venv\Scripts\python.exe diagnostico_wakeword.py
"""
from __future__ import annotations

import io
import os
import sys
import time

# Forzar stdout a UTF-8 para evitar UnicodeEncodeError en consolas Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
import pyaudio
from openwakeword.model import Model
from openwakeword.utils import download_models
from legacy.voice.input_device_selection import (
    DetectionStats,
    filter_input_candidates,
    native_chunk_size,
    resample_to_16khz,
    select_device,
)

# ── Configuracion ─────────────────────────────────────────────────────────────
SAMPLE_RATE  = 16000
MODEL_NAME   = "hey_jarvis"
# Configurables sin tocar el diagnóstico: JARVIS_DIAG_THRESHOLD y
# JARVIS_DIAG_DEVICE.
THRESHOLD    = float(os.getenv("JARVIS_DIAG_THRESHOLD", "0.03"))
PREFERRED_DEVICES = []
# ── Descarga modelo ───────────────────────────────────────────────────────────
print(f"[DIAG] Cargando modelo '{MODEL_NAME}'...", flush=True)
try:
    download_models(model_names=[MODEL_NAME])
except Exception as e:
    print(f"[DIAG] WARN al descargar modelo: {e}")

model = Model(wakeword_models=[MODEL_NAME], inference_framework="onnx")
print(f"[DIAG] Modelo cargado. Threshold = {THRESHOLD}", flush=True)

# ── Listado de dispositivos ───────────────────────────────────────────────────
pa = pyaudio.PyAudio()
input_devices = []
print("\n[DIAG] Dispositivos de entrada disponibles:")
for i in range(pa.get_device_count()):
    info = pa.get_device_info_by_index(i)
    if int(info.get("maxInputChannels", 0)) > 0:
        input_devices.append(info)
        name = info['name'].encode('ascii', errors='replace').decode('ascii')
        print(f"  [{i:2d}] {name}  (ch={info['maxInputChannels']}, rate={int(info['defaultSampleRate'])})")

def probe_input_rms(candidate: int | None) -> float:
    stream = None
    print(f"[DIAG] Probando dispositivo index={candidate}...", flush=True)
    try:
        capture_rate = int(next(info["defaultSampleRate"] for info in input_devices if info["index"] == candidate))
        capture_chunk_size = native_chunk_size(capture_rate)
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=capture_rate,
            input=True,
            frames_per_buffer=capture_chunk_size,
            input_device_index=candidate,
        )
        rms_values = []
        for _ in range(4):
            frame = np.frombuffer(
                stream.read(capture_chunk_size, exception_on_overflow=False),
                dtype=np.int16,
            ).astype(np.float32)
            if frame.size:
                rms_values.append(float(np.sqrt(np.mean(frame ** 2))))
        return float(np.median(rms_values)) if rms_values else 0.0
    except Exception:
        return 0.0
    finally:
        if stream is not None:
            stream.stop_stream()
            stream.close()


print("[DIAG] Probando señal real; habla durante un instante...", flush=True)
probe_candidates = filter_input_candidates(input_devices, PREFERRED_DEVICES)
forced_device = os.getenv("JARVIS_DIAG_DEVICE")
if forced_device:
    try:
        forced_index = int(forced_device)
    except ValueError:
        print(f"[DIAG] ERROR: JARVIS_DIAG_DEVICE no es un indice: {forced_device!r}", flush=True)
        pa.terminate()
        sys.exit(1)
    if forced_index not in probe_candidates:
        print(f"[DIAG] ERROR: index={forced_index} no es una entrada segura.", flush=True)
        pa.terminate()
        sys.exit(1)
    probe_candidates = [forced_index]
print(f"[DIAG] Candidatos seguros: {probe_candidates}", flush=True)
if not probe_candidates:
    print("[DIAG] ERROR: no hay dispositivos de entrada seguros.", flush=True)
    pa.terminate()
    sys.exit(1)
selected_device, capture_rms = select_device(probe_candidates, probe_input_rms)
capture_status = "OK" if capture_rms >= 3.0 else "FALLO"
print(f"[DIAG] Prueba de captura: {capture_status}, rms={capture_rms:.2f}", flush=True)
if capture_rms < 3.0:
    if forced_device:
        print("[DIAG] ERROR: el dispositivo elegido no entrega señal.", flush=True)
        pa.terminate()
        sys.exit(1)
    print("[DIAG] Sin señal durante la sonda; se conservará el primer dispositivo seguro.", flush=True)

capture_rate = int(next(info["defaultSampleRate"] for info in input_devices if info["index"] == selected_device))
capture_chunk_size = native_chunk_size(capture_rate)

dev_label = f"index={selected_device}" if selected_device is not None else "default"
print(f"\n[DIAG] Usando dispositivo: {dev_label} (rate={capture_rate})")
print("[DIAG] Di 'hey jarvis' frente al microfono. Ctrl+C para salir.\n")

# ── Bucle de escucha ─────────────────────────────────────────────────────────
try:
    stream = pa.open(
        format=pyaudio.paInt16, channels=1, rate=capture_rate,
        input=True, frames_per_buffer=capture_chunk_size,
        input_device_index=selected_device,
    )
except Exception as exc:
    print(f"[DIAG] ERROR al abrir stream: {exc}")
    pa.terminate()
    sys.exit(1)

detection_stats = DetectionStats()
peak_rms     = capture_rms
frames_read  = 0
silent_chunks = 0

try:
    while True:
        frame_bytes = stream.read(capture_chunk_size, exception_on_overflow=False)
        native_frame = np.frombuffer(frame_bytes, dtype=np.int16)
        # Capturamos al rate nativo y remuestreamos cada chunk de forma
        # continua al rate fijo que espera OpenWakeWord.
        audio_frame = resample_to_16khz(native_frame, capture_rate)
        frames_read += 1

        rms = float(np.sqrt(np.mean(audio_frame.astype(np.float32) ** 2)))
        peak_rms = max(peak_rms, rms)
        silent_chunks = (silent_chunks + 1) if rms < 60 else 0

        scores = model.predict(audio_frame)
        if not scores:
            continue

        score = float(scores.get(MODEL_NAME) or max(scores.values(), default=0.0))
        detection_stats.observe(score, THRESHOLD)

        bar_len = int(score * 40)
        bar     = "#" * bar_len + "." * (40 - bar_len)
        status  = " <-- DETECTADO!" if score >= THRESHOLD else ""
        warn    = "  [MIC SIN SEÑAL?]" if silent_chunks > 80 else ""

        print(
            f"\r[{bar}] {score:.3f}  peak={detection_stats.peak_score:.3f}"
            f"  rms={rms:.0f}/{peak_rms:.0f}{status}{warn}   ",
            end="",
            flush=True,
        )

        if score >= THRESHOLD:
            print(f"\n[DIAG] *** Wake word detectado! score={score:.3f} ***")

        if frames_read % 250 == 0 and silent_chunks > 200:
            print(f"\n[DIAG] ATENCION: El microfono no capta audio (RMS={rms:.1f}).")
            print("[DIAG] Comprueba los permisos y selecciona otro microfono con JARVIS_DIAG_DEVICE.\n")

except KeyboardInterrupt:
    print(
        f"\n\n[DIAG] Peak maximo: {detection_stats.peak_score:.3f}  "
        f"Detecciones: {detection_stats.detections}  "
        f"RMS maximo: {peak_rms:.0f}  (threshold={THRESHOLD})"
    )
    if detection_stats.detections > 0:
        print("[DIAG] RESULTADO: Wake word FUNCIONANDO correctamente.")
    elif detection_stats.peak_score < 0.1 and capture_rms < 3.0:
        print("[DIAG] PROBLEMA: El microfono no capta voz.")
        print("       -> Prueba otro indice con JARVIS_DIAG_DEVICE.")
    elif detection_stats.peak_score < 0.1:
        print("[DIAG] No se detecto 'hey jarvis', pero el microfono si entrega señal.")
        print("       -> Repite la frase claramente y mas cerca del microfono.")
    elif detection_stats.peak_score < THRESHOLD:
        new_t = max(0.01, round(detection_stats.peak_score * 0.75, 2))
        print(f"[DIAG] PROBLEMA: El score ({detection_stats.peak_score:.3f}) no llega al threshold ({THRESHOLD}).")
        print(f"       -> Baja el threshold en config.yaml a: {new_t}")
        print(f"       -> O di 'hey jarvis' mas cerca del microfono.")
    else:
        print(f"[DIAG] El score máximo ({detection_stats.peak_score:.3f}) no superó el threshold.")
        print(f"       -> El dispositivo correcto es index={selected_device}")
        print(f"       -> Puedes ponerlo en config.yaml:  audio: input_device: {selected_device}")
finally:
    stream.stop_stream()
    stream.close()
    pa.terminate()
