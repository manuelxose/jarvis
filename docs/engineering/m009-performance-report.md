# M009 — Performance & intelligent activation: implementation and validation report

Date: 2026-09-24 · Baseline: `bb29c0f` · Hardware: i7-12700H, RTX 3070 Laptop 8 GB,
Windows 11, laptop mic array (Intel Smart Sound) and speakers.
Tests: **614 passed, 3 skipped** (baseline 568/3). The M009 suites (198 tests) also
pass on the Windows venv.

Raw benchmark data: `docs/engineering/bench/baseline-fast.json`,
`baseline-models.json` (bb29c0f) and `after-m009.json`. Reproduce with
`scripts\perf_bench.py` (silent: device timings use zero-amplitude audio).

## 1. What changed

| Area | Before (bb29c0f) | After |
|---|---|---|
| Activation | 3 claps; confirmed after waiting for a possible 4th | **2 claps**; 1st clap = candidate (speculative voice load, no sound); 2nd confirms immediately; 3rd lands in cooldown |
| Voice model | new worker + model per session, killed at session end | **one shared model** (`VoiceModelManager`): cold → speculative → warm → cooldown (10 min) → evicted; VRAM-pressure and GPU-busy-app eviction; never during synthesis |
| Session end | only "a dormir" (a session ran all night transcribing noise) | also after `session_idle_seconds` (15 min) without a turn |
| Voice cache | welcomes keyed by text only | **versioned** (profile id + version + text + settings + format), LRU-bounded; success acks recorded from the clone on first use; failures and values never cached |
| Degraded startup | waited up to 60 s for the clone, then spoke | waits ≤ 8 s, then the truthful warning in the fallback voice; `startup.degraded` event + log **before** speaking; 30 s hard cap on the announcement |
| Welcome timing | welcome at music + 2.0 s + 0.35 s (duck) | ducking starts early; voice lands at music + 2.0 s |
| Local commands | "minimiza chrome", network activity → cloud LLM; "volumen al 40" → a stub that did nothing | window min/max/restore/focus, graceful close, network, CPU/RAM/GPU usage, terminal, open project by name: all local |
| Events | none | typed `EventHub` (activation/startup/voice/speech/agent/system), `logs/events.jsonl` |
| Console windows | TTS worker/Hermes got their own console; closing it killed the model (seen overnight) | launched with `CREATE_NO_WINDOW` |

## 2. Benchmarks (p50 / p95, same machine)

| Metric | Baseline | After | Note |
|---|---|---|---|
| Gesture confirmation after the last clap | 555 / 646 ms (3 claps) | **48 / 55 ms** (2 claps) | 40/40 synthetic gestures detected in both |
| First-clap candidate (speculation trigger) | — | 30 ms | |
| Trigger → chime (real output stream) | 69 ms (first run 731) | 77 ms (first run 121) | stream open dominates |
| Trigger → music | 30 ms | 28 ms | track preloaded |
| Trigger → welcome audio | 2399 / 3057 ms | **2037 / 2122 ms** | target 2.0 s |
| Cached acknowledgement → audio device | — | **0.18 / 0.7 ms** | + device buffer latency |
| Intent routing | 0.01 / 0.03 ms | 0.01 / 0.04 ms | |
| Local command (route + tool) | 0.11 / 0.30 ms | 0.08 / 0.26 ms | TTS excluded |
| Interruption (playback stopped) | 4.5 / 10.3 ms | 4.3 / 9.3 ms | real device |
| Voice ready, **new session after a previous one** | cold load every time | **0.1 ms** (model reused) | cooldown reuse |
| Voice ready, cold session with 1st-clap speculation | — | 23.7 s | speculation saves only the clap gap (~0.4 s) |
| Clone cold load (worker start → ready) | 74.3 s | 25.1 s | **not comparable**: baseline ran with a cold disk cache |
| Clone warm time-to-first-audio | 210 / 218 ms | 208 / 220 ms | unchanged (same model) |
| Whisper load / transcribe 2.5 s | 11.4 s / 253 ms | 8.6 s / 264 ms | unchanged |
| DeepSeek time to first token | 737 / 953 ms | 769 / 930 ms | provider-bound |
| End of utterance → first audible reply (warm) | 1754 / 1878 ms | 1382 / 1552 ms | **no pipeline change targeted this**; baseline ran with the GPU at 83 °C, after at 58–79 °C — treat as run variance |
| Idle sentinel CPU | 0.0 % | 1.1 % of one core | 10 s sample |
| Idle sentinel RAM (working set) | 62 MB | 143 MB | **not comparable**: the baseline process had been idle 8 h and Windows had trimmed its working set; a fresh bb29c0f daemon measured ~140 MB yesterday |
| VRAM, voice warm → after eviction | — | 5540 → 1275 MB | eviction returns the ~4.2 GB |

### Targets

| Target | Result |
|---|---|
| Local command acknowledgement < 300 ms | **Met** when the phrase is cached (route+tool < 1 ms, cache → device < 1 ms). First use of a phrase is live synthesis: clone TTFA ≈ 210 ms warm. |
| Cached audio startup < 150 ms | **Met** (0.18 ms to the device write; the stream's own buffer adds tens of ms). |
| First audible AI response < 1.5 s | **Met at p50 (1.38 s), missed at p95 (1.55 s)**. Measured from end of utterance; the VAD additionally waits 600 ms of silence to end an utterance. Bottleneck: DeepSeek first token (~770 ms). |
| Audio interruption < 200 ms | **Met** for stopping playback (4.3 ms). With `audio.barge_in` on, *detecting* the interruption needs 5 loud frames (~500 ms). |

### Bottlenecks that remain (measured, not changed)

1. **Clone cold start: 25–74 s**, of which 16 s is model load and the rest Python/torch
   import inside the worker (worse with a cold disk cache). The adaptive lifecycle hides
   it after the first session (reuse), and the recorded welcome hides it at activation;
   a question asked within ~20 s of a *cold* activation waits (up to
   `tts.warmup_wait_seconds`) for the cloned voice. `voice.preload: "always"` removes it
   at the cost of ~4.2 GB VRAM permanently.
2. **DeepSeek first token ~0.75 s** and **VAD end-of-speech 0.6 s**. Whisper variants
   were measured (no Silero pre-pass, no timestamps): ≤ 20 ms gain, and
   `without_timestamps` dropped the leading "Jarvis", so nothing was changed.

## 3. Two-clap validation

- Unit tests: one clap never activates (candidate expires, speculation undone); two
  claps confirm ~50 ms after the second; a third clap is ignored (cooldown); claps
  too far apart, speech, music with drums (120/170 bpm), tones and quiet taps never
  activate; music hits do not start speculation.
- Real recordings (your speech ×2, 190 s music): **0 activations** at default and
  maximum sensitivity in two-clap mode.
- **Keyboard risk (found and mitigated, not fully closed):** synthetic key clicks have
  a clap-like shape, so loudness is the discriminator. The calibration rule changed
  from "midpoint between room and claps" (−37 dBFS for your room) to "9 dB under your
  softest clap" (−17.4 dBFS), re-derived from your existing calibration file.
  Simulated quiet/moderate typing: 47 → 0 false activations per 5 min. Clicks as loud
  as a clap still pass; `claps.confirm_quiet_seconds` (e.g. 0.25) adds a
  rhythm guard at +250 ms latency. **Needs a real typing session** (see §6).

## 4. Configuration (new/changed keys, `config.win.json`)

```jsonc
"claps":  { "claps_required": 2,            // 2 (default) or 3
            "confirm_quiet_seconds": 0.0,   // >0: wait for no 3rd transient (anti-rhythm), adds latency
            "quiet_before_seconds": 0.8, "max_gap_seconds": 0.9 /* also the candidate lifetime */ },
"voice":  { "preload": "adaptive",          // adaptive | always | on_demand
            "speculative": true,            // load on the 1st clap
            "predictive_on_wake_word": true,
            "cooldown_seconds": 600,        // keep warm after a session
            "max_gpu_mb": 4500,             // only load with this much free VRAM
            "evict_on_pressure": true, "min_free_vram_mb": 700,
            "gpu_busy_processes": [] },     // e.g. ["cyberpunk2077.exe"]: never load/keep while running
"daemon": { "session_idle_seconds": 900,    // end a silent session (0 = never)
            "metrics_interval_seconds": 0,  // >0: publish system.metrics
            "events_log": true },           // %LOCALAPPDATA%\jarvis\logs\events.jsonl
"welcome":{ "voice_ready_timeout_seconds": 8, "announce_timeout_seconds": 30,
            "after_welcome": "restore" },   // music returns after the welcome, ducked under speech
"tts":    { "warmup_wait_seconds": 25 }
```
`daemon.preload_voice: true` (M007) still works and maps to `voice.preload: "always"`.
`jarvis status` now shows the voice state and cache size; `jarvis welcome record`
refills the versioned cache after re-enrolling the voice.

## 5. Events (schema v1, `jarvis.observability.event_hub.SCHEMAS`)

Every event: `{"v": 1, "name", "ts", "mono_ms", ...}`. Invalid publishes are dropped
and logged, never raised.

| Event | Fields |
|---|---|
| `activation.started` / `.cancelled` / `.confirmed` | `source` / `reason` / `source, confidence` |
| `startup.progress` / `.completed` / `.degraded` | `phase` / `welcome_source, timings_ms` / `issues, phase` |
| `voice.state` / `.loading` / `.ready` / `.evicted` | `state, reason` / `reason` / `load_ms` / `reason` |
| `speech.started` / `.completed` | `trace_id, source (live/cache)` / `trace_id, cancelled` |
| `agent.started` / `.progress` / `.completed` | `trace_id, route` / `trace_id, detail` / `trace_id, route, ok, elapsed_ms` |
| `system.metrics` | `cpu_percent, ram_percent, gpu` |

## 6. Failure scenarios → evidence

| Scenario | Covered by |
|---|---|
| One clap / two claps / three claps / clap-like sounds | `test_claps.TwoClapTests`, real-recording eval |
| Voice model unavailable / loading slowly | `test_startup` (fallback after deadline, cached welcome needs no model), `test_qwen_clone_tts` (bounded warm-up wait) |
| Insufficient GPU memory / GPU-heavy app | `test_voice_manager` (budget, pressure, busy process) |
| No Internet / DeepSeek timeout / quota | `test_provider_fallback` (first-token timeout and HTTP errors fall back once to Ollama), `test_model_adapters` (daily budget → skip without retry) |
| Music unavailable | `test_startup` (missing/undecodable media) |
| Audio device missing | `test_daemon.test_missing_output_device_does_not_stop_activation`, `test_audio_renderer` |
| User interrupts the welcome | `jarvis sleep` during startup cancels it (`test_sleep_during_startup_interrupts_the_welcome`) |
| Question before TTS loads | `test_qwen_clone_tts.test_warming_worker_is_not_retryable_and_turn_waits_for_it` |
| Simultaneous wake word + clap, repeated activation | `test_daemon.test_duplicate_activations_during_startup_are_ignored` |
| Application launch failure | `test_workspace` (missing executable, dependents skipped) |

## 7. Not verified (needs you, with sound)

- **Your real two claps** (`jarvis claps test --seconds 30`), including from across the room.
- **Typing next to the laptop** while `jarvis claps test` runs for a minute: any
  `GESTURE` line is a false activation. If it happens, set `claps.confirm_quiet_seconds: 0.25`.
- The audible sequence: chime, music, welcome ~2 s later with ducking, music restored
  after it; and that the welcome audio in the versioned cache sounds right (the
  recordings were checked for level only, not listened to).
- A first spoken acknowledgement being recorded and replayed from the cache.
- A game in `voice.gpu_busy_processes` actually freeing the GPU.
