"""
diagnostico_wakeword.py  –  compatible CP1252 / UTF-8
======================================================
Muestra en tiempo real el score del wake word.
Ejecutar:  .venv\Scripts\python.exe diagnostico_wakeword.py
"""
from __future__ import annotations

import io
import sys
import time

# Forzar stdout a UTF-8 para evitar UnicodeEncodeError en consolas Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
import pyaudio
from openwakeword.model import Model
from openwakeword.utils import download_models

# ── Configuracion ─────────────────────────────────────────────────────────────
CHUNK_SIZE   = 1280
SAMPLE_RATE  = 16000
MODEL_NAME   = "hey_jarvis"
THRESHOLD    = 0.35
# Candidatos preferidos en este equipo (en orden de preferencia).
# None = dejar que PyAudio elija por defecto.
PREFERRED_DEVICES = [15, 12, 9, None]   # Realtek, G435, Intel 48k, default

# ── Descarga modelo ───────────────────────────────────────────────────────────
print(f"[DIAG] Cargando modelo '{MODEL_NAME}'...")
try:
    download_models(model_names=[MODEL_NAME])
except Exception as e:
    print(f"[DIAG] WARN al descargar modelo: {e}")

model = Model(wakeword_models=[MODEL_NAME], inference_framework="onnx")
print(f"[DIAG] Modelo cargado. Threshold = {THRESHOLD}")

# ── Listado de dispositivos ───────────────────────────────────────────────────
pa = pyaudio.PyAudio()
print("\n[DIAG] Dispositivos de entrada disponibles:")
for i in range(pa.get_device_count()):
    info = pa.get_device_info_by_index(i)
    if int(info.get("maxInputChannels", 0)) > 0:
        name = info['name'].encode('ascii', errors='replace').decode('ascii')
        print(f"  [{i:2d}] {name}  (ch={info['maxInputChannels']}, rate={int(info['defaultSampleRate'])})")

# Elegir dispositivo funcional
selected_device = None
for candidate in PREFERRED_DEVICES:
    try:
        test = pa.open(
            format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE,
            input=True, frames_per_buffer=CHUNK_SIZE,
            input_device_index=candidate,
        )
        test.stop_stream()
        test.close()
        selected_device = candidate
        break
    except Exception:
        pass

dev_label = f"index={selected_device}" if selected_device is not None else "default"
print(f"\n[DIAG] Usando dispositivo: {dev_label}")
print("[DIAG] Di 'hey jarvis' frente al microfono. Ctrl+C para salir.\n")

# ── Bucle de escucha ─────────────────────────────────────────────────────────
try:
    stream = pa.open(
        format=pyaudio.paInt16, channels=1, rate=SAMPLE_RATE,
        input=True, frames_per_buffer=CHUNK_SIZE,
        input_device_index=selected_device,
    )
except Exception as exc:
    print(f"[DIAG] ERROR al abrir stream: {exc}")
    pa.terminate()
    sys.exit(1)

peak_score   = 0.0
frames_read  = 0
silent_chunks = 0

try:
    while True:
        frame_bytes  = stream.read(CHUNK_SIZE, exception_on_overflow=False)
        audio_frame  = np.frombuffer(frame_bytes, dtype=np.int16)
        frames_read += 1

        rms = float(np.sqrt(np.mean(audio_frame.astype(np.float32) ** 2)))
        silent_chunks = (silent_chunks + 1) if rms < 60 else 0

        scores = model.predict(audio_frame)
        if not scores:
            continue

        score = float(scores.get(MODEL_NAME) or max(scores.values(), default=0.0))
        if score > peak_score:
            peak_score = score

        bar_len = int(score * 40)
        bar     = "#" * bar_len + "." * (40 - bar_len)
        status  = " <-- DETECTADO!" if score >= THRESHOLD else ""
        warn    = "  [MIC SIN SEÑAL?]" if silent_chunks > 80 else ""

        print(f"\r[{bar}] {score:.3f}  peak={peak_score:.3f}{status}{warn}   ", end="", flush=True)

        if score >= THRESHOLD:
            print(f"\n[DIAG] *** Wake word detectado! score={score:.3f} ***")
            peak_score = 0.0

        if frames_read % 250 == 0 and silent_chunks > 200:
            print(f"\n[DIAG] ATENCION: El microfono no capta audio (RMS={rms:.1f}).")
            print(f"[DIAG] Intenta cambiar PREFERRED_DEVICES en este script.\n")

except KeyboardInterrupt:
    print(f"\n\n[DIAG] Peak maximo: {peak_score:.3f}  (threshold={THRESHOLD})")
    if peak_score < 0.1:
        print("[DIAG] PROBLEMA: El microfono no capta voz.")
        print("       -> Cambia el orden de PREFERRED_DEVICES en el script.")
    elif peak_score < THRESHOLD:
        new_t = max(0.10, round(peak_score * 0.75, 2))
        print(f"[DIAG] PROBLEMA: El score ({peak_score:.3f}) no llega al threshold ({THRESHOLD}).")
        print(f"       -> Baja el threshold en config.yaml a: {new_t}")
        print(f"       -> O di 'hey jarvis' mas cerca del microfono.")
    else:
        print("[DIAG] RESULTADO: Wake word FUNCIONANDO correctamente.")
        print(f"       -> El dispositivo correcto es index={selected_device}")
        print(f"       -> Puedes ponerlo en config.yaml:  audio: input_device: {selected_device}")
finally:
    stream.stop_stream()
    stream.close()
    pa.terminate()
