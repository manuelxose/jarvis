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
- Lanzar `python -m jarvis run` (el `main.py` de la v1 quedo en `legacy/`).

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
.\run_jarvis.ps1 -PythonPath "C:\Users\usuario\AppData\Local\Programs\Python\Python311\python.exe"
```

Saltar descarga del modelo (si ya esta):

```powershell
.\bootstrap.ps1 -SkipModelPull
```

## Ruta cloud-first opcional (configuracion local no versionada)

Por defecto Jarvis usa Ollama local. Para priorizar un proveedor cloud compatible con
OpenAI sin guardar ninguna credencial en el repositorio:

1. Copia la plantilla versionada a su hermano ignorado:

```powershell
Copy-Item config.local.example.json config.local.json
```

2. Proporciona la clave solo por variable de entorno (nunca dentro de un archivo versionado):

```powershell
$env:DEEPSEEK_API_KEY = "sk-tu-clave"
```

`config.local.json` referencia `${DEEPSEEK_API_KEY}` y el valor se resuelve en tiempo de
ejecucion; la clave nunca se escribe en `config.json`, en la plantilla, en logs ni en informes.

3. Comprueba el enrutado con `doctor`:

```powershell
.\.venv\Scripts\python.exe -m jarvis doctor
.\.venv\Scripts\python.exe -m jarvis doctor --json
```

La salida muestra la seccion `model routing`:

```text
model routing:
  primary: deepseek (kind=openai_compat, model=deepseek-flash, base_url=https://api.deepseek.com, api_key=present)
  fallback: ollama (kind=ollama, model=mistral:7b-instruct, base_url=http://127.0.0.1:11434, api_key=none)
  local fallback ready: yes
```

`local fallback ready: yes` significa que Ollama responde; `no` significa que el primario
cloud sigue configurado pero el fallback local no esta disponible. En modo JSON, `model`
expone `provider_order`, `primary`, `fallbacks` y `local_fallback_ready`, y la clave solo
aparece como `has_api_key: true/false` (nunca el valor).

Coste: cada respuesta cloud registra sus tokens (`stream_options.include_usage`) y los
valora con `input_usd_per_million`/`output_usd_per_million` del proveedor. El gasto del dia
se guarda en `%LOCALAPPDATA%\jarvis\spend.json`; al alcanzar `models.max_daily_usd`
(por defecto 1.0 USD) Jarvis deja de llamar a la nube hasta medianoche y responde con
Ollama. Sin tarifas configuradas el tope no puede contar el gasto (`priced: false` en
`doctor --json`). Cualquier endpoint compatible con OpenAI (p. ej. Qwen en Model Studio)
sirve: copia su id de modelo y tarifas de la pagina oficial de precios.
La plantilla de DeepSeek desactiva el razonamiento (`"extra_body": {"thinking": {"type": "disabled"}}`):
con el razonamiento activo, `deepseek-flash` gastaba el limite de tokens pensando y la voz
empezaba ~1 s mas tarde y se cortaba.

Reglas:
- Manten `config.json` y `config.local.example.json` libres de secretos.
- No subas `config.local.json` al control de versiones (ya esta en `.gitignore`).
- Sin `config.local.json`, Jarvis usa la configuracion Ollama versionada.

## Voz clonada local (Faster Qwen3-TTS 0.6B, recomendada)

Jarvis habla con tu propia voz clonada en la GPU local (RTX 3070); la muestra de voz
nunca sale del equipo. Detalles, medidas y arquitectura:
`docs/engineering/local-voice-clone.md`.

1. Entorno aislado del motor de voz (solo descarga el modelo 0.6B Base, ~2 GB):

```powershell
scripts\setup_tts_worker.bat
```

2. Graba tu voz (lee en voz alta el texto que aparece, ~10 s, sitio silencioso):

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m jarvis.voice_profile record
```

Genera `%LOCALAPPDATA%\jarvis\voice\default\preview.wav`: escuchalo antes de usarlo.
Tambien puedes importar un WAV de 16 bits:
`... -m jarvis.voice_profile enroll --audio mi_voz.wav --text "transcripcion exacta"`
(modo por defecto: huella de voz, el mas rapido; `--icl` usa el audio en contexto, mas lento). Otros comandos: `status`, `prepare`
(regenera la vista previa), `delete` (borra perfil, grabacion y condicionamiento).

3. `config.win.json` ya usa `"tts": {"provider": "qwen_clone", "chunk_size": 4}`. Mientras el modelo se
calienta (~20 s), si no hay perfil, o si el motor falla, Jarvis habla con la voz SAPI de
Windows y `doctor` lo indica (`TTS voice clone: degraded ...`).

Interrupcion por voz (barge-in): desactivada por defecto porque sin cancelacion de eco
los altavoces interrumpirian a Jarvis; con auriculares activa `"audio": {"barge_in": true}`.

## Voz en la nube (Alibaba Model Studio / Qwen, opcional)

Jarvis usa un unico proveedor cloud para voz: Alibaba Cloud Model Studio (Qwen),
con fallback local automatico (`faster-whisper` para STT, SAPI para TTS) si la
nube falla o no esta configurada. Por defecto, sin `config.local.json`, Jarvis
usa SAPI (TTS) y whisper local (STT); XTTS local (`provider: "local"`) usa tus
muestras de `voice_samples/` pero tarda ~87s por respuesta en CPU.

1. Crea una API key en Alibaba Cloud Model Studio (region Singapore/international)
   y, si tu cuenta la requiere, un workspace id. Expórtalas solo por variable de entorno:

```powershell
$env:DASHSCOPE_API_KEY = "sk-tu-clave"
$env:ALIBABA_MODEL_STUDIO_WORKSPACE_ID = "ws-tu-workspace"   # requerido para region singapore
```

2. Clona tu voz una sola vez a partir de una muestra WAV limpia (mono, 16-bit,
   16kHz+, 3-60s):

```powershell
.\.venv\Scripts\python.exe scripts\alibaba_voice_clone.py create voice_samples\sample.wav
```

El script valida la muestra (duracion, canales, formato, clipping, silencio),
la sube y registra la voz clonada, e imprime el `voice_id` resultante junto con
el bloque JSON listo para pegar. La voz solo sirve con el mismo `--target-model`
usado al crearla (restriccion de Alibaba, no de Jarvis).

3. Copia ese bloque a `config.local.json` (crealo si no existe):

```json
{
  "tts": {
    "provider": "alibaba_qwen",
    "voice": "<voice_id impreso por el script>",
    "api_key": "${DASHSCOPE_API_KEY}"
  },
  "stt": {
    "provider": "alibaba_qwen",
    "api_key": "${DASHSCOPE_API_KEY}"
  },
  "alibaba": {
    "region": "singapore",
    "workspace_id": "${ALIBABA_MODEL_STUDIO_WORKSPACE_ID}",
    "tts_model": "qwen-audio-3.0-tts-flash"
  }
}
```

`alibaba.tts_model` debe coincidir con el `--target-model` usado en `create`.
Verifica el `voice_id` con:

```powershell
.\.venv\Scripts\python.exe scripts\alibaba_voice_clone.py test <voice_id>
```

4. Comprueba el estado con `doctor`: la seccion `TTS` debe reportar
`alibaba_qwen (cloud, cloned voice, sapi fallback)` en vez de
`alibaba_qwen api_key, voice, or workspace_id not configured`.

La clave, el workspace id y el `voice_id` nunca se escriben en `config.json`, en
la plantilla ni en logs. Sin `config.local.json`, Jarvis sigue usando SAPI/whisper
local. Ver `docs/engineering/alibaba-qwen-voice-research.md` para el detalle de
modelos, endpoints y limitaciones conocidas de esta integracion.

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
