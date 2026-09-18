# Windows voice hardware acceptance (M003) — real WASAPI/G435/STT

This is the exact procedure for the remaining M003 acceptance gap: real
target-machine evidence for the Windows audio backend (WASAPI), Logitech G435
selection, sample rate, real microphone capture, approximately four seconds of
speech transcribed within the ~2-second target, and a 30-second silence
regression.

The diagnostic runs the **v2 production path** under `src/jarvis/`:
`MicCapture` (sounddevice/WASAPI) and the configured STT provider
(`faster-whisper` or SAPI). It does not import legacy `main.py`/`voice/*`, and
it uses no fake adapters, generated audio, or a mocked WASAPI/STT provider.

## 1. Exact commands (Windows, from the repository root)

Preferred CLI form (package installed):

```powershell
.\.venv\Scripts\jarvis.exe diagnose voice --acceptance
```

Equivalent standalone script (no package install needed):

```powershell
.\.venv\Scripts\python.exe .\diagnostico_voice_acceptance.py config.win.json
```

Use `config.win.json` (or `config.json`) as the v2 JSON configuration. The
configured `audio.input_device` (or `null` → the Windows default input device)
must resolve to the Logitech G435 microphone; select the G435 as the Windows
default input device and enable desktop microphone access in Windows privacy
settings if capture fails.

## 2. What the diagnostic reports

- `environment.os` — Windows version (system/release/version/machine)
- `environment.python` — Python version
- `environment.commit_sha` — `git rev-parse HEAD`
- `environment.configured_audio` — sample rate, channels, chunk size, input device
- `environment.configured_stt` — provider, model, language, device
- `audio` — sounddevice availability, WASAPI host API (present, default
  input/output device), discovered input devices, selected device, device
  native/default sample rate, requested stream sample rate, channels, capture
  format (`int16 mono PCM`)
- `stt` — provider, model, compute device, compute type, sample rate
- `model_load_seconds` — cold faster-whisper model load (0 for SAPI)
- `cold_inference_latency_seconds` — first real inference
- `warm_inference_latency_seconds` + `runs[]` — five warm runs (per-run
  `audio_seconds`, `transcription_seconds`, `transcript`)
- `transcription_summary` — p50 / p95 / max over warm runs
- `silence_test` — 30-second capture, transcript, `false_transcripts`,
  `false_command_dispatches`

## 3. Speech timing test (primary acceptance metric)

1. Speak approximately four seconds of normal Spanish for the cold run, then
   again for each of the five warm runs (the script prompts).
2. Each run records via `MicCapture` + `EnergyVAD` (VAD-bounded, max 4 s) and
   transcribes via the production STT adapter, measured with `time.monotonic()`.

The acceptance criterion is the milestone's existing approximately-two-second
transcription timing for approximately four seconds of audio. It is not to be
reinterpreted after the fact.

## 4. Silence regression

Stay quiet with normal room noise for 30 seconds. Acceptance: empty (or
non-actionable) transcript, `false_transcripts == 0`, and
`false_command_dispatches == 0` (the diagnostic never dispatches commands).

## 5. Recording evidence

Save the full JSON report. The report distinguishes automated (Linux,
stub-based) evidence from this real Windows hardware evidence — do not label
the former as target-machine acceptance.
