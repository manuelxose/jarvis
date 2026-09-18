# Windows voice performance acceptance

Run from PowerShell at the repository root through
`\\wsl.localhost\\Ubuntu\\home\\manuelxose\\workspace\\jarvis`.
These checks are intentionally local-only and do not require cloud credentials.

## Prepare the Windows environment

If PowerShell blocks the unsigned local bootstrap script, allow scripts only in
the current PowerShell process and prepare the project virtual environment:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\bootstrap.ps1 -SkipModelPull
```

Use the project interpreter for every check. The correct path is
`.\.venv\Scripts\python.exe` (not `..venv\Scripts\python.exe`):

```powershell
.\.venv\Scripts\python.exe diagnostico_audio_wasapi.py
.\.venv\Scripts\python.exe diagnostico_stt.py
```

If the runtime reports `WASAPI unavailable` or `Invalid input device`, run the
audio diagnostic first. It prefers Windows WASAPI, falls back to WDM-KS when
WASAPI has no inputs, and enumerates a usable microphone without requiring a
device brand or fixed index. Enable microphone access for desktop applications
in Windows privacy settings.
Selection now measures a short audio sample from each endpoint. An input that
opens but only returns zeros is skipped while other inputs are tried. If all
open inputs are silent, Jarvis starts with a warning and rechecks connected
microphones after repeated empty captures; speak or reconnect the desired
microphone while it is running. A driver that cannot open still reports its
specific PortAudio error in the log.
Some Bluetooth WDM-KS drivers reject every PortAudio stream with error `-9999`.
The diagnostic and runtime then use the FFmpeg DirectShow endpoint matching the
selected microphone and try the other DirectShow inputs only when it cannot open.
This requires the `ffmpeg` detected by bootstrap; it does not require a fixed
device index or a specific headset.

To prepare dependencies and start the full runtime through the supported
launcher, use `.\run_jarvis.bat`; it already applies the PowerShell bypass.

With `wake_word.openwakeword_enabled: false` (the default), Jarvis does not
load or download OpenWakeWord assets. It starts in the STT activation mode and
expects the phrase to include `Jarvis`; enable OpenWakeWord only after its ONNX
assets have been downloaded successfully.

If the bootstrap reports that Ollama is running but does not respond, verify
the IPv4 listener directly:

```powershell
Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue
Test-NetConnection 127.0.0.1 -Port 11434
curl.exe -sS http://127.0.0.1:11434/api/tags
```

The bootstrap probes `127.0.0.1` explicitly because some Windows installations
resolve `localhost` to IPv6 (`::1`) while Ollama listens only on IPv4.

## Ollama preflight

`OllamaClient.preflight()` returns `available`, `message`, and
`elapsed_seconds`. Remediation is included in `message`; it does not return
`ok`, `error`, `duration_seconds`, `remediation`, or `detail` fields.

```powershell
.\.venv\Scripts\python.exe -c "from brain.llm import OllamaClient; r=OllamaClient().preflight(); print(f'available={r.available} message={r.message} elapsed={r.elapsed_seconds:.3f}s')"
```

Acceptance observations:

1. `diagnostico_audio_wasapi.py` reports the selected microphone, WASAPI/WDM-KS or DirectShow, native rate, and non-zero RMS while speaking.
2. `diagnostico_stt.py` records until 0.45 s of silence or a three-second maximum and reports the expected Spanish phrase. After the STT model is loaded, `STT transcription finished` must be <= 2 seconds.
3. `main.py` logs the actual input endpoint. When WDM-KS produces `-9999`, it must log `backend=DirectShow` rather than failing over to an invalid PyAudio default.
4. `Jarvis listo` appears without any `Pre-generated cache audio` work beforehand. Optional cache generation is not started by startup.
5. Saying `Jarvis ... <command>` produces one wake transcript and uses the inline command without a second capture. A wake-only utterance may intentionally perform a second capture for the command.
6. When Ollama is stopped, the preflight message includes `ollama serve`; when
   the configured model is missing, it includes `ollama pull <model>`.
7. With the default `tts.provider: sapi`, startup logs `TTS provider selected: sapi`,
   synthesis completes in roughly one second, and no `XTTS model deferred` or
   multi-second `Processing time` entry appears. XTTS is optional and should only
   be selected when cloned voice samples are intentionally required.
8. A normal conversation produces one `ollama_request` per turn. The intent
   router must not issue a separate classification request before the answer.

The Linux CI host cannot provide hardware timing or WASAPI evidence. Its automated evidence is the deterministic backend/preflight regression suite, compile checks, and full Python test suite.

The STT model is loaded with `local_files_only: true` after its first download,
so normal startup does not contact Hugging Face. If the model is not cached yet,
temporarily set it to `false`, run `diagnostico_stt.py` once with Internet access,
then restore `true`.
