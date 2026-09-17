# Windows voice performance acceptance

Run from the Windows Python environment against the repository through `\\wsl.localhost\\...`.
These checks are intentionally local-only and do not require cloud credentials.

```powershell
python diagnostico_audio_wasapi.py
python diagnostico_stt.py
python main.py
```

Acceptance observations:

1. `diagnostico_audio_wasapi.py` reports the G435 input, WASAPI, native 16 kHz rate, and non-zero RMS.
2. `diagnostico_stt.py` records approximately four seconds and reports the expected Spanish phrase. After the STT model is loaded, `STT transcription finished` must be <= 2 seconds.
3. `main.py` logs `Audio capture resolved: backend=WASAPI` and the actual device name/index/rate. A PyAudio endpoint must not be reported as the active STT backend.
4. `Jarvis listo` appears without any `Pre-generated cache audio` work beforehand. Optional cache generation is not started by startup.
5. Saying `Jarvis ... <command>` produces one wake transcript and uses the inline command without a second capture. A wake-only utterance may intentionally perform a second capture for the command.
6. When Ollama is stopped, startup reports `ollama serve`; when the configured model is missing, it reports `ollama pull <model>`.

The Linux CI host cannot provide hardware timing or WASAPI evidence. Its automated evidence is the deterministic backend/preflight regression suite, compile checks, and full Python test suite.
