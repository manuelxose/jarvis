# Guía de instalación

Paso a paso para instalar, configurar y arrancar Jarvis en un PC con Windows desde cero.
Tiempo aproximado: 20–40 minutos (la mayor parte, descargas).

## 0. Qué necesitas

| Requisito | Para qué | Obligatorio |
|---|---|---|
| Windows 10/11, micrófono y altavoces | Todo | Sí |
| [Git](https://git-scm.com/download/win) | Descargar el repositorio | Sí |
| `winget` (viene con Windows 11) | Que el bootstrap instale Python y Ollama solo | Recomendado |
| GPU NVIDIA con ≥ 6 GB de VRAM y driver con soporte CUDA 11.8 (≥ 520) | Voz clonada y Whisper rápido | Para la voz clonada |
| [uv](https://docs.astral.sh/uv/) (`winget install astral-sh.uv`) | Crear el entorno aislado de la voz clonada | Para la voz clonada |
| Cuenta en un proveedor compatible con OpenAI (p. ej. DeepSeek) | Respuestas más inteligentes y rápidas | No (sin ella usa Ollama local) |

Sin GPU NVIDIA Jarvis funciona igual, pero habla con la voz SAPI de Windows y
transcribe en CPU (más lento).

## 1. Descarga e instala

```powershell
git clone https://github.com/manuelxose/jarvis.git
cd jarvis
.\bootstrap.ps1
```

El bootstrap:

1. Busca Python 3.11 y, si no está, lo instala (con `winget` o descarga directa).
2. Crea el entorno virtual `.venv` e instala `requirements.txt` (solo cuando cambia).
3. Instala y arranca Ollama y descarga `mistral:7b-instruct` (~4 GB).

Si PowerShell bloquea el script: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force`
(solo afecta a esa ventana). Opciones útiles: `-SkipModelPull` (no descargar el modelo),
`-ForceDependencies` (reinstalar dependencias), `-PythonPath C:\ruta\python.exe`.

## 2. Prepara la línea de comandos

Todos los comandos se lanzan con el Python del entorno virtual. En cada ventana nueva de PowerShell:

```powershell
cd ruta\a\jarvis
$env:PYTHONPATH = "src"
function jarvis { .\.venv\Scripts\python.exe -m jarvis @args --config config.win.json }
```

A partir de aquí, `jarvis doctor` equivale a
`.\.venv\Scripts\python.exe -m jarvis doctor --config config.win.json`.

## 3. Tu configuración personal

```powershell
Copy-Item config.local.example.json config.local.json
notepad config.local.json
```

`config.local.json` no se sube nunca a git y se fusiona encima de `config.win.json`. Ajusta:

- `welcome.owner_name`: cómo te llama Jarvis al saludarte.
- `welcome.music_path`: (opcional) un fichero de música tuyo para el arranque, p. ej.
  `voice_samples/intro.mp3`.
- `desktop.authorized_scopes`: carpetas donde Jarvis puede crear, mover o borrar ficheros y
  ejecutar comandos de desarrollo sin pedirte confirmación. Déjalo vacío (`[]`) para que
  pregunte siempre.
- `models`: el proveedor en la nube. **Si no vas a usar ninguno, borra el bloque `models`**
  y Jarvis usará solo Ollama.

La clave del proveedor se guarda como variable de entorno **de usuario**, para que también
la vea el centinela que arranca con Windows:

```powershell
[Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", "sk-...", "User")
```

Cierra y vuelve a abrir PowerShell para que la variable esté disponible.

Más ajustes (palmadas, voz, perfiles de trabajo, música): [configuration.md](configuration.md).

## 4. Comprueba que todo responde

```powershell
jarvis doctor
```

Cada componente aparece como `healthy`, `degraded` (funciona con un respaldo) o `failed`.
Es normal ver `TTS voice clone: degraded` hasta completar el paso 5.

## 5. Voz clonada (opcional, requiere GPU NVIDIA)

```powershell
scripts\setup_tts_worker.bat
```

Crea `%LOCALAPPDATA%\jarvis\venv-tts`, instala el motor Faster Qwen3-TTS y descarga el modelo
0.6B (~2 GB). Después graba tu voz: lee en voz alta el texto que aparece (~10 s, sitio
silencioso, a un palmo del micrófono):

```powershell
.\.venv\Scripts\python.exe -m jarvis.voice_profile record
```

Escucha `%LOCALAPPDATA%\jarvis\voice\default\preview.wav`. Si no te convence, repite la
grabación. Para usar un audio que ya tengas:
`... -m jarvis.voice_profile enroll --audio voice_samples\mi_voz.wav --text "lo que dices en el audio"`.

Por último, graba los saludos con tu voz (así el arranque no espera a que cargue el modelo):

```powershell
jarvis welcome record
```

## 6. Palmadas

```powershell
jarvis claps calibrate          # 4 s de silencio y luego dos tandas de palmadas
jarvis claps test --seconds 30  # cada detección imprime una línea GESTURE
```

Da dos palmadas secas, separadas unos 0,3 s. Si no detecta nada, recalibra más cerca del
micrófono o sube `claps.sensitivity`. Si detecta palmadas al teclear, pon
`claps.confirm_quiet_seconds: 0.25` en tu `config.local.json`.

## 7. Arranca

```powershell
jarvis daemon
```

Da dos palmadas (o pulsa `Ctrl+Alt+J`): suena un aviso, se abre tu perfil de trabajo y
Jarvis te saluda. Habla: «Jarvis, ¿qué hora es?», «Jarvis, minimiza chrome», «Jarvis, a dormir».

Para que arranque solo al iniciar sesión (sin ventana, sin permisos de administrador):

```powershell
jarvis autostart install     # jarvis autostart remove para quitarlo
```

Desde otra ventana puedes controlarlo con `jarvis status`, `jarvis activate`, `jarvis sleep` y
`jarvis quit`. Registro: `%LOCALAPPDATA%\jarvis\logs\daemon.log`.

## Problemas frecuentes

| Síntoma | Solución |
|---|---|
| `uv not found` en el paso 5 | `winget install astral-sh.uv` y abre una ventana nueva. |
| `CUDA unavailable` en el paso 5 | Actualiza el driver de NVIDIA; comprueba `nvidia-smi`. |
| `environment variable 'DEEPSEEK_API_KEY' is required` | Define la variable (paso 3) o borra el bloque `models` de `config.local.json`. |
| No oye nada / micrófono equivocado | Lista dispositivos con `.\.venv\Scripts\python.exe -c "import sounddevice; print(sounddevice.query_devices())"` y pon el índice en `audio.input_device` (y `daemon.input_device` para las palmadas). |
| Habla con voz de Windows en vez de la tuya | El modelo aún está cargando (20–70 s la primera vez) o no hay perfil de voz: mira `jarvis doctor`. |
| `jarvis activate` dice que no hay centinela | Arranca `jarvis daemon` o revisa `daemon.log`. Si el puerto 47811 está ocupado, cambia `daemon.control_port`. |
| La respuesta tarda mucho con Ollama | Con la voz clonada cargada no cabe un modelo de 7B en 8 GB de VRAM; usa un proveedor en la nube o un modelo local más pequeño. |
| Se activa solo | Recalibra (`jarvis claps calibrate`), baja `claps.sensitivity` o usa `claps.confirm_quiet_seconds`. |

## Desinstalar

```powershell
jarvis autostart remove
Remove-Item -Recurse "$env:LOCALAPPDATA\jarvis"   # voz, cachés, logs, venv del motor de voz
```

y borra la carpeta del repositorio. Ollama y Python se desinstalan desde la configuración de Windows.
