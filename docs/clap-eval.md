# Evaluación de la activación por palmadas

Por defecto el gesto de activación son **dos palmadas**; la triple palmada se activa con `claps_required: 3` en la sección `claps` de la configuración. El detector es `src/jarvis/adapters/audio/claps.py`; el harness de evaluación es `scripts/clap_eval.py` (solo `numpy`; usa `soundfile` si está instalado y, si no, el módulo `wave` de la biblioteca estándar para WAV). No escribe nada a disco.

## Cómo ejecutarla

```bash
# Corpus sintético etiquetado (semilla fija): habla, música, TTS, tecleo/golpes, sala en silencio + palmadas
python scripts/clap_eval.py --synthetic

# Igual que el demonio: tuning de config.claps + calibración guardada
python scripts/clap_eval.py --synthetic --config config.win.json

# Grabaciones propias (una línea JSON por archivo; --claps-required 2|3 fuerza el modo)
python scripts/clap_eval.py --config config.win.json grabacion1.wav grabacion2.wav

# Reproducir una grabación por el camino exacto del CLI
jarvis claps test --file grabacion.wav --config config.win.json
```

Salida: una línea JSON por caso (`activations`, `activation_times`, `latency_ms`, `single_claps_accepted`, `rejected_transients`, `cpu_realtime_factor`) y una línea final `summary`. Código de salida distinto de 0 si hay cualquier falsa activación o positivo no detectado.

## Resultados sintéticos (capturados)

Bloques de 20 ms, como el listener en vivo. Todas las categorías negativas se evalúan en modo 2 y 3 palmadas; cada positivo solo en su modo (en modo 2 palmadas un triple ya confirma en la segunda).

| Caso | Modo | Linux act. | Linux lat. ms | Linux CPU-RTF | Windows act. | Windows lat. ms | Windows CPU-RTF |
|---|---|---|---|---|---|---|---|
| speech_like (neg) | 2 | 0 | - | 0.00114 | 0 | - | 0.00098 |
| speech_like (neg) | 3 | 0 | - | 0.00111 | 0 | - | 0.00088 |
| music_like_120bpm (neg) | 2 | 0 | - | 0.00042 | 0 | - | 0.00058 |
| music_like_120bpm (neg) | 3 | 0 | - | 0.00044 | 0 | - | 0.00058 |
| music_like_90bpm (neg) | 2 | 0 | - | 0.00048 | 0 | - | 0.00058 |
| music_like_90bpm (neg) | 3 | 0 | - | 0.00046 | 0 | - | 0.00057 |
| tts_like (neg) | 2 | 0 | - | 0.00057 | 0 | - | 0.00073 |
| tts_like (neg) | 3 | 0 | - | 0.00054 | 0 | - | 0.00068 |
| typing_knocks (neg) | 2 | 0 | - | 0.00049 | 0 | - | 0.00069 |
| typing_knocks (neg) | 3 | 0 | - | 0.00049 | 0 | - | 0.00071 |
| quiet_room (neg) | 2 | 0 | - | 0.00039 | 0 | - | 0.00060 |
| quiet_room (neg) | 3 | 0 | - | 0.00043 | 0 | - | 0.00060 |
| two_claps (pos) | 2 | 1 | 30 | 0.00048 | 1 | 30 | 0.00061 |
| three_claps (pos) | 3 | 1 | 510 | 0.00060 | 1 | 510 | 0.00066 |

Resumen: 14 casos, 0 falsas activaciones, 0 positivos perdidos, latencia máxima 510 ms (triple palmada; el doble se confirma a los 30 ms de la segunda palmada), CPU-RTF máximo 0.00114 (Linux) / 0.00098 (Windows), es decir ~0.1 % de un núcleo en tiempo real.

Entornos:

- **Linux (WSL2)**: `python3` 3.12, solo `numpy`, tuning por defecto (`ClapTuning()`), sin `--config`.
- **Windows** (vía `powershell.exe`): `.venv\Scripts\python.exe` (Python 3.11.9, numpy 1.26.4, soundfile 0.12.1) con `--config config.win.json`, por lo que se usó el tuning del demonio; existe `clap_calibration.json` en el directorio de datos de Jarvis (no se mostró su contenido).
- `rejected_transients` difiere entre plataformas en música (Linux 1, Windows 0) y tecleo (18 vs 20) probablemente por el tuning/calibración y la versión de numpy distintos (no investigado); en ambas hay 0 activaciones.

## Suite completa

`python3 -m unittest discover -s tests` en Linux (sin `PYTHONPATH`): **772 tests, OK (3 omitidos)**.

Antes de esta slice, ese comando fallaba en el host Linux (10 fallos y 1 error, ya presentes en el commit `436fab5`) porque `jarvis` no está instalado y `discover` no añade `src` al path: `tests/test_acceptance.py` importaba `jarvis` sin insertar `src`, y `tests/test_hermes_child.py` lanza el hijo Hermes como subproceso que no heredaba `src`. Se corrigió solo en los tests (`sys.path` y `PYTHONPATH` apuntando a `src`); no cambia código de producción.

La slice suma 18 tests respecto a la base (`tests/test_clap_eval.py`, `tests/test_claps_cli.py`).

## Automatizado vs observado por humanos

Automatizado (capturado arriba): corpus sintético en Linux y Windows, replay de WAV por `jarvis claps test --file`, suite de tests, latencia de detección y coste de CPU sobre audio sintético.

Sin ejecutar / pendiente:

- **live-mic: pending owner UAT.** No se lanzó la prueba con micrófono real (`jarvis claps test --seconds 30`); no se afirma nada sobre falsas activaciones con voz, música o TTS reales de la sala del propietario.
- **Grabaciones reales: ninguna evaluada.** `voice_samples/` solo contiene `README.md`; solo se listó el nivel superior del directorio de datos del demonio (sin WAV de grabaciones sueltas) y no se buscó en el resto del disco. Para evaluar las propias: `python scripts/clap_eval.py --config config.win.json grabacion.wav` (no las copies al repo).
- El corpus sintético valida regresiones y reglas del detector, no sustituye la validación con audio real.
