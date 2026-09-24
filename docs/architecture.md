# Architecture

Jarvis is a single Python package (`src/jarvis`) plus one isolated child process
for the cloned voice. It is organised in layers; dependencies only point inwards.

| Layer | Package | Contents |
|---|---|---|
| Core | `jarvis.core` | Provider-neutral ports (`contracts.py`), typed errors, turn context and cancellation, runtime state, supervisor, circuit breaker. No I/O. |
| Adapters | `jarvis.adapters` | Implementations of the ports: audio (mic, VAD, claps, wake word, mixer, output), STT, TTS, LLM providers, memory (SQLite), Hermes child, tools. |
| Application | `jarvis.application` | Composition (`runtime.py`), routing, turn manager, voice loop, startup sequence, voice-model lifecycle, desktop planner, workspaces. |
| Apps | `jarvis.apps` | Entry points: CLI, background daemon, operator commands, autostart. |
| Observability | `jarvis.observability` | Structured logging with secret redaction, tracing, metrics, cost tracking, event hub. |

`jarvis.config` validates the whole configuration at load time; an invalid file
fails fast with a precise message.

## Life of an activation

```
sign-in ─► Sentinel (pythonw, no window)                          apps/daemon.py
             │ mic ─► ClapDetector (numpy)       1st clap: speculative voice load
             │ Ctrl+Alt+J (RegisterHotKey)      2nd clap / hotkey / wake word: confirm
             │ optional "hey Jarvis" (openWakeWord)
             │ 127.0.0.1 control socket (jarvis activate|sleep|status|quit|restart)
             ▼ confirmed
     StartupSequence                                               application/startup.py
       chime ─► music fades in ─┬─► build runtime (worker thread)
                                ├─► workspace profile (VS Code, terminal, services)
                                └─► cloned voice ready?  (VoiceModelManager)
       welcome (cached recording, or live and truthful if something is degraded)
       music ducks under speech, then continues quietly in the background
     VoiceLoop until "a dormir", 15 min without a turn, or `jarvis sleep`
     ─► back to the Sentinel; the voice model stays warm for 10 min (cooldown)
```

The clap listener is closed while a session runs, so the music and Jarvis's own
voice cannot re-trigger it. The control socket bind doubles as the
single-instance lock.

## Life of a turn

```
MicCapture ─► EnergyVAD (music-aware threshold, 300 ms pre-roll) ─► STT      application/voice_loop.py
                                                                     │
                                          ActivationManager (wake word / follow-up window)
                                                                     ▼
                                                   Router (deterministic patterns)   application/routing.py
             ┌───────────────────────────────┬───────────────────────┴──────────────┐
             ▼                               ▼                                      ▼
      fast_command                     fast_model                               hermes
      ToolGateway (risk policy)        ProviderChain: cloud (spend-capped)      supervised agent child
      or DesktopPlanner (JSON plan)    ─► Ollama fallback before 1st token      (streamed tokens)
             └───────────────────────────────┴──────────────────────────────────────┘
                                                                     ▼
                              SentenceChunker (Spanish phrase boundaries, stall flush)
                                                                     ▼
                              TTS chain: cached phrase │ QwenCloneTTS ─► SAPI fallback
                                                                     ▼
                              AudioOutputQueue (turn-tagged) ─► StreamRenderer (one persistent stream)
```

`TurnManager` owns the turn's cancellation token and propagates it through every
stage. Barge-in or "para" stops the *speech*; mutating tools run on their own
execution token, so an interrupted reply never leaves a half-done operation. Only
"cancela la operación" stops running operations.

## Providers and fallbacks

| Stage | Primary | Fallback |
|---|---|---|
| LLM | First entry of `models.providers` (e.g. DeepSeek, OpenAI-compatible) | Next entries, typically local Ollama. Fallback happens only before the first token, so a reply is never duplicated. A daily USD cap (`models.max_daily_usd`) switches to local models until midnight. |
| STT | `faster-whisper` (CUDA when available) | CPU Whisper; optional Alibaba Qwen cloud ASR |
| TTS | Cloned owner voice (Faster Qwen3-TTS worker) | Windows SAPI; optional Alibaba Qwen cloud TTS or Coqui XTTS |

Each cloud adapter sits behind a circuit breaker; its health is reported by
`jarvis doctor`.

## The cloned-voice worker

`faster-qwen3-tts` needs `transformers` 5.x while Jarvis pins 4.x, so the model
runs in its own venv as a child process (`adapters/tts/qwen_worker.py`), talking
JSON lines over stdio (no port is opened). The adapter supervises it: bounded
restarts with backoff, hang detection, per-request ids so stale audio is dropped
after a cancel. `VoiceModelManager` shares one instance between sessions: cold →
speculative → warm → cooldown → evicted, with eviction on VRAM pressure or when a
configured GPU-heavy app is running, never mid-synthesis. Details and
measurements: [voice-clone.md](voice-clone.md).

## Local data

Everything personal stays on the machine, under `%LOCALAPPDATA%\jarvis`
(`~/.local/share/jarvis` elsewhere):

| Path | Contents |
|---|---|
| `voice\<profile>\` | Reference recording, cached conditioning (`prompt.pt`), preview |
| `cache\voice\` | Versioned cache of synthesized welcomes and acknowledgements |
| `venv-tts\` | Isolated environment of the voice worker |
| `logs\daemon.log`, `logs\events.jsonl` | Daemon log and typed events |
| `audit.jsonl` | One line per tool decision (arguments redacted) |
| `startup-report.json` | Timings of the last activation |
| `clap_calibration.json` | Owner's clap calibration |
| `spend.json` | Today's cloud spend |
| `workspace-state.json`, `desktop_context.json` | What Jarvis launched; last project/file/command for references like "ese proyecto" |

Inside the repository, `memory/` (SQLite conversation memory) and `logs/` are
created at runtime and are gitignored.

## Events

`EventHub` publishes versioned, schema-checked events (`activation.*`,
`startup.*`, `voice.*`, `speech.*`, `agent.*`, `system.metrics`); the schemas live
in `jarvis.observability.event_hub.SCHEMAS`. Invalid events are dropped and
logged, never raised. The daemon writes them to `logs\events.jsonl`.
