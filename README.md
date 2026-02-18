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
- Verificar muestras WAV en `voice_samples/`.
- Lanzar `main.py`.

Si `winget` no esta disponible, intenta instalacion directa por URL para Python y Ollama.

## Comandos utiles

Preparar entorno sin arrancar Jarvis:

```powershell
.\bootstrap.ps1
```

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
- El arranque define `COQUI_TOS_AGREED=1` para evitar el prompt interactivo de CPML en XTTS.
- Wake word se ejecuta en `onnxruntime` (sin `tflite-runtime`).
