# Jarvis v2 requirements

## Verification scenarios

- **V-01:** wake → speech → STT → route → response → TTS → audio.
- **V-02:** `abre Spotify` → deterministic route → Windows action.
- **V-03:** fast conversational provider → streamed TTS.
- **V-04:** complex request → Hermes → tools/memory → streamed response.
- **V-05:** store memory → restart → recall.
- **V-06:** historical session → relevant retrieval.
- **V-07:** Jarvis speaking → user interrupts → audio stops → new listening turn.
- **V-08:** primary provider failure → fallback.
- **V-09:** Hermes failure → degraded mode → local actions continue → recovery.
- **V-10:** healthy startup → calibrated double clap → music/phrase → `LISTENING`.
- **V-11:** production acceptance on actual microphone, speakers/headphones, network, configured API providers, local fallback, Hermes process, persisted memory, and Windows actions.

## Requirements

| ID | Requirement | Phase | Verification |
| --- | --- | --- | --- |
| FND-01 | Establish the Windows-first runtime foundation: typed configuration, lifecycle/state transitions, bounded runtime messages, cancellation, health checks, and graceful shutdown. | Phase 01 — Foundation | Unit lifecycle/state/configuration checks; V-10 |
| VOI-01 | Provide a streaming voice path and measure `speech_end_to_first_audio_ms` at p50 and p95. | Phase 03 — Voice vertical slice; Phase 11 — Performance | V-01, V-03, V-07, V-11 |
| PRV-01 | Keep STT, TTS, LLM, and routing providers neutral behind capability, timeout, cancellation, and fallback contracts; use hybrid cloud-first selection with local fallback only after measurement. | Phase 02 — Provider bake-off; Phase 07 — Provider routing and failover | Provider-contract checks; V-03, V-08, V-11 |
| HRM-01 | Supervise managed Hermes as a local child process with health, restart, shutdown, cancellation, and degraded local-operation behavior. | Phase 05 — Hermes integration; Phase 12 — Reliability | Hermes process-management integration checks; V-04, V-09, V-11 |
| MEM-01 | Keep persistent memory local and user-controlled: inspect, correct, delete, and configure retention; bound retrieval and write consolidation; exclude sensitive values from logs and commits. | Phase 06 — Memory | SQLite persistence checks; V-05, V-06, V-11 |
| TOL-01 | Route all system actions through a validated, allowlisted Tool Gateway with typed inputs, timeout/cancellation, redacted audit metadata, and trace-bound expiring confirmation for destructive or externally visible actions. | Phase 09 — Tools and integrations | Tool validation/permission checks; V-02, V-04, V-11 |
| OBS-01 | Emit structured, redacted traces, logs, metrics, and latency spans across the end-to-end turn, including `speech_end_to_first_audio_ms`. | Phase 11 — Performance; Phase 12 — Reliability | Observability and benchmark checks; V-01, V-03, V-08, V-09 |
| CLI-01 | Provide validated startup/diagnostic commands plus provider, STT, LLM, TTS, end-to-end, and voice benchmarking CLI commands; never expose secrets in output. | Phase 10 — Startup; Phase 11 — Performance | CLI/configuration checks; V-10, V-11 |
| ACC-01 | Require real-hardware production acceptance in addition to mocks, covering Windows actions and every configured audio, network, provider, fallback, Hermes, and persisted-memory dependency. | Phase 13 — Production acceptance | V-11 |
