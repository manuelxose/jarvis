# Jarvis Local (Automatizado)

## Arranque en un comando (Windows)

Desde la carpeta `jarvis/`:

```powershell
.\run_jarvis.bat
```

El flujo automatico realiza:
- Detectar o instalar Python 3.11 (via `winget`).
- Crear `.venv` si no existe.
- Instalar dependencias solo cuando cambia `requirements.txt`.
- Detectar o instalar Ollama.
- Arrancar `ollama serve` si no esta activo.
- Verificar o descargar `mistral:7b-instruct`.
- Preparar muestras WAV en `voice_samples/` para el proveedor XTTS opcional.
- Lanzar `main.py`.

Si `winget` no esta disponible, intenta instalacion directa por URL para Python y Ollama.

## Comandos utiles

Preparar entorno sin arrancar Jarvis:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\bootstrap.ps1
```

El bypass solo afecta a la ventana actual de PowerShell. Despues del bootstrap,
usa siempre `.\.venv\Scripts\python.exe` para ejecutar Jarvis y las pruebas.
El bootstrap usa cmdlets nativos compatibles con `ConstrainedLanguage`.

Forzar reinstalacion de dependencias:

```powershell
.\run_jarvis.ps1 -ForceDependencies
```

Usar un Python concreto ya instalado:

```powershell
.\run_jarvis.ps1 -PythonPath "C:\Ruta\python.exe"
```

Usa una ruta real. Ejemplo:

```powershell
.\run_jarvis.ps1 -PythonPath "C:\Users\mgonzalezv.INDRA\AppData\Local\Programs\Python\Python311\python.exe"
```

Saltar descarga del modelo (si ya esta):

```powershell
.\bootstrap.ps1 -SkipModelPull
```

## Notas

- Necesitas `winget` habilitado para instalacion automatica de Python/Ollama.
- Si faltan archivos de voz (`*.wav`), el script avisa y no inicia Jarvis en modo `-Run`.
- Primer arranque puede tardar varios minutos por descargas.
- El instalador usa `--prefer-binary` y dependencias con ruedas Windows para evitar compilacion C++.
- La voz activa usa SAPI de Windows (`tts.provider: sapi`), que evita la síntesis XTTS de ~87 s en CPU. XTTS queda disponible como alternativa si se cambia el proveedor y se mantienen muestras WAV.
- Ollama conserva el modelo caliente durante 10 minutos y limita las respuestas a 128 tokens; las conversaciones no hacen una llamada adicional de clasificación de intención.
- `COQUI_TOS_AGREED=1` solo se usa si se activa XTTS.
- Wake word se ejecuta en `onnxruntime` (sin `tflite-runtime`).
- `audio.input_device: null` con `auto_select_input: true` selecciona automáticamente cualquier micrófono disponible mediante WASAPI o WDM-KS; si PortAudio no puede abrir su driver, usa FFmpeg DirectShow.
- El modelo STT se carga desde la caché local (`local_files_only: true`) para evitar consultas repetidas a Hugging Face.
