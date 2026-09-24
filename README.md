# Jarvis

Asistente de voz en español, local-first, para Windows. Se despierta con dos
palmadas, te saluda con **tu propia voz clonada** (generada en tu GPU, sin salir
del equipo), arranca tu entorno de trabajo y controla el escritorio por voz con
una política de riesgos explícita.

- **Activación**: dos palmadas, `Ctrl+Alt+J`, «hey Jarvis» (opcional) o `jarvis activate`.
- **Voz**: Faster Qwen3-TTS 0.6B clonando tu voz en local; SAPI de Windows como respaldo.
- **Oído**: `faster-whisper` (CUDA si hay GPU NVIDIA).
- **Cerebro**: cualquier endpoint compatible con OpenAI (p. ej. DeepSeek) con tope de gasto diario y
  respaldo automático a Ollama local; comandos frecuentes resueltos sin LLM en < 1 ms.
- **Escritorio**: ventanas, aplicaciones, ficheros, comandos, perfiles de trabajo (VS Code, terminal,
  servicios), con confirmación hablada para todo lo destructivo y registro de auditoría.
- **Privacidad**: la grabación de tu voz, la memoria y los registros se quedan en tu equipo; las claves
  solo se leen de variables de entorno.

## Requisitos

- Windows 10/11 con micrófono y altavoces (la ejecución se puede lanzar desde WSL).
- Python 3.11 (el bootstrap lo instala si falta).
- Para la voz clonada: GPU NVIDIA con ≥ 6 GB de VRAM (probado en RTX 3070 Laptop 8 GB).
- Opcional: [Ollama](https://ollama.com) para el modelo local, una API key de un proveedor
  compatible con OpenAI para el modelo en la nube.

## Instalación (Windows)

```powershell
git clone https://github.com/manuelxose/jarvis.git
cd jarvis
.\bootstrap.ps1          # Python 3.11, .venv, dependencias, Ollama y mistral:7b-instruct
```

Opciones: `-SkipModelPull` (no descargar el modelo de Ollama), `-ForceDependencies`
(reinstalar dependencias), `-PythonPath C:\ruta\python.exe` (usar un Python concreto),
`-Run` (arrancar al terminar; equivale a `.\run_jarvis.bat`).

En los ejemplos siguientes, `jarvis <comando>` significa:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m jarvis <comando> --config config.win.json
```

## Puesta en marcha

1. **Motor de voz clonada** (entorno aislado, descarga solo el modelo 0.6B, ~2 GB):

   ```powershell
   scripts\setup_tts_worker.bat
   ```

2. **Graba tu voz** (lee en voz alta el texto que aparece, ~10 s, en un sitio silencioso):

   ```powershell
   .\.venv\Scripts\python.exe -m jarvis.voice_profile record
   ```

   Escucha `%LOCALAPPDATA%\jarvis\voice\default\preview.wav`. También puedes importar un WAV:
   `... -m jarvis.voice_profile enroll --audio mi_voz.wav --text "transcripción exacta"`.

3. **Modelo en la nube** (opcional): copia la plantilla y da la clave por variable de entorno.

   ```powershell
   Copy-Item config.local.example.json config.local.json
   $env:DEEPSEEK_API_KEY = "sk-..."
   ```

4. **Calibra las palmadas** con tu micrófono y comprueba la detección:

   ```powershell
   jarvis claps calibrate
   jarvis claps test --seconds 30
   ```

5. **Diagnóstico**: `jarvis doctor` (o `doctor --json`) muestra el estado de cada componente,
   el enrutado de modelos y si el respaldo local está listo. Nunca imprime secretos.

6. **Arranque automático** al iniciar sesión (acceso directo en la carpeta Inicio, sin
   privilegios de administrador): `jarvis autostart install`.

## Uso

Da dos palmadas. Suena un aviso, entra la música (si configuraste `welcome.music_path`), se
abre tu perfil de trabajo y Jarvis te saluda. Después habla con normalidad: «Jarvis, …».

| Comando | Qué hace |
|---|---|
| `jarvis daemon` | Centinela en primer plano (palmadas, atajo, socket de control) |
| `jarvis activate` · `sleep` · `status` · `restart` · `quit` | Controlar el centinela en marcha |
| `jarvis run` | Bucle de voz directo, sin centinela |
| `jarvis workspace start\|stop\|status [perfil] [--force]` | Perfiles de trabajo |
| `jarvis tools` | Herramientas registradas y su clase de riesgo |
| `jarvis welcome record` | Regrabar los saludos con la voz clonada |
| `jarvis demo` · `accept` · `benchmark` | Demo, aceptación y benchmark sin hardware (adaptadores falsos) |

Ejemplos de voz: «arranca mi entorno de desarrollo», «¿qué está usando la GPU?», «minimiza
chrome», «baja la música», «cancela la operación», «a dormir». Lista completa y política de
riesgos en [docs/desktop-control.md](docs/desktop-control.md).

## Configuración

`config.win.json` es la configuración completa de Windows; `config.json` es una base mínima
(solo Ollama) para CI y otros sistemas. Tus ajustes personales y proveedores cloud van en
`config.local.json` (ignorado por git), que se fusiona encima. Los valores `"${VAR}"` se leen
de variables de entorno. Referencia completa: [docs/configuration.md](docs/configuration.md).

Voz en la nube con Alibaba Model Studio (Qwen) como alternativa a la voz local:
`scripts\alibaba_voice_clone.py create voice_samples\muestra.wav` registra la voz y muestra el
bloque de configuración a copiar; detalles en [docs/alibaba-qwen.md](docs/alibaba-qwen.md).

## Desarrollo

La suite no necesita audio, GPU ni Windows (usa adaptadores falsos) y corre en Linux/WSL:

```bash
python3.11 -m venv .venv-dev && . .venv-dev/bin/activate
pip install -e . numpy soundfile
python -m unittest discover -s tests
```

CI (GitHub Actions) ejecuta lo mismo en Python 3.11 y compila `src` y `tests`.

```
src/jarvis/
  core/           puertos, errores, turnos y cancelación, supervisor
  adapters/       audio, STT, TTS (incl. worker de voz clonada), LLM, memoria, Hermes, herramientas
  application/    composición, enrutado, turnos, bucle de voz, arranque, ciclo de vida de la voz
  apps/           CLI, centinela, comandos de operador, autoarranque
  observability/  logs con redacción de secretos, trazas, métricas, coste, eventos
scripts/          bootstrap del worker TTS, benchmarks y comprobaciones con hardware real
tests/            suite unittest (590+ tests)
docs/             arquitectura, configuración, control del escritorio, voz, rendimiento
```

## Documentación

- [Arquitectura](docs/architecture.md): capas, flujo de una activación y de un turno, datos locales.
- [Configuración](docs/configuration.md): todas las secciones y valores por defecto.
- [Control del escritorio](docs/desktop-control.md): comandos de voz, riesgos, perfiles de trabajo.
- [Voz clonada local](docs/voice-clone.md): diseño del worker, medidas, operación y modos degradados.
- [Rendimiento](docs/performance.md): benchmarks, objetivos y cuellos de botella.
- [Alibaba Qwen](docs/alibaba-qwen.md): referencia de la voz en la nube opcional.

## Privacidad y seguridad

- La muestra de tu voz, su condicionamiento, la caché de audio, la memoria de conversaciones y
  los logs se guardan en `%LOCALAPPDATA%\jarvis` o en carpetas ignoradas por git.
- Sin `config.local.json`, Jarvis no llama a ningún servicio en la nube.
- Las acciones de riesgo alto se confirman de viva voz justo antes de ejecutarse; las peticiones que
  vienen de un agente nunca heredan permisos de confianza; Jarvis no tiene ruta de elevación (UAC).
- Cada decisión de herramienta queda en `audit.jsonl` con los argumentos sensibles redactados.
