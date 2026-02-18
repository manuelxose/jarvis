# Guia Rapida de Uso - Jarvis Local

Tu estado actual es correcto:

- `Jarvis listo. Esperando wake word...`

Eso significa que el sistema ya arranco bien y ahora espera activacion por voz.

## 1) Como hablarle para que responda

1. Deja la consola abierta.
2. Di en voz clara: `hey jarvis` (wake word).
3. Espera el `beep` corto de confirmacion.
4. Justo despues del beep, di tu orden en una frase:
   - `abre chrome`
   - `estado del sistema`
   - `que hora es`
   - `estado del trading`
5. Espera respuesta de voz.

Nota: en CPU, la primera respuesta TTS puede tardar varios segundos.

## 2) Prueba minima recomendada

Con Jarvis corriendo:

1. Di `hey jarvis`
2. Cuando suene el beep, di `que hora es`
3. Deberias ver en consola:
   - `Usuario: ...`
   - `Jarvis: ...`

Si ves esas dos lineas, el pipeline completo funciona.

## 3) Si no responde

### A) No detecta wake word (no beep)

- Acercate al micro y di literalmente `hey jarvis`.
- Comprueba en consola la linea `Input audio device selected: ...`.
- Si no es tu micro real, fija el indice manual en `config.yaml`:
  - `audio.input_device: <indice>`
- Reinicia Jarvis.

### B) Detecta wake word pero no entiende (STT vacio)

- Revisa micro en Windows (dispositivo predeterminado correcto).
- Habla tras el beep (no antes).
- Haz una frase corta y clara.

### C) Responde por texto en consola pero no se oye audio

- Revisa altavoz por defecto de Windows.
- Sube volumen del sistema.

## 4) Ver dispositivos de audio (opcional)

Desde `jarvis/`:

```powershell
.\.venv\Scripts\python.exe -c "from voice.audio_utils import get_audio_devices; import pprint; pprint.pp(get_audio_devices())"
```

Si necesitas, luego puedes fijar indices en `config.yaml`:

- `audio.input_device`
- `audio.output_device`

## 5) Reinicio limpio

```powershell
.\run_jarvis.ps1 -ForceDependencies
```
