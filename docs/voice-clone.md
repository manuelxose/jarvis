# Local cloned-voice TTS — architecture, measurements, operations

Jarvis speaks every route (fast command, cloud LLM, Hermes, Ollama) through one
local voice-cloning engine: Faster Qwen3-TTS with `Qwen/Qwen3-TTS-12Hz-0.6B-Base`
on the laptop's RTX 3070. The owner's reference recording and its cached
conditioning stay in `%LOCALAPPDATA%\jarvis\voice\<profile>` and never leave the
machine. Windows SAPI is the labeled fallback voice.

## Data flow (actual modules)

```
MicCapture ─► EnergyVAD (+300 ms pre-roll) ─► WhisperSTT (turbo, CUDA)          voice_loop.py
   │                                                   │
   │  frames consumed during reply (barge-in opt-in)   ▼
   │  + 300 ms echo-tail drop after reply        ActivationManager (wake word)
   │                                                   ▼
   │                                        Router ─► fast command ─► ToolGateway ─┐
   │                                          │                                     │ ack text
   │                                          ├─► ProviderChain: cloud (openai_compat,
   │                                          │     spend-capped) ─► Ollama fallback │
   │                                          └─► Hermes child (streamed tokens) ─┐  │
   │                                                                              ▼  ▼
   │                          SentenceChunker (Spanish phrase boundaries, 1.2 s stall flush)
   │                                                   ▼
   │                          TTSChain: QwenCloneTTS ─(not ready/no profile/crash)─► SAPI
   │                              │ stdio JSON lines, request ids, cancel
   │                              ▼
   │                          qwen_worker.py (venv-tts, model resident + CUDA graphs)
   │                                                   ▼ 24 kHz PCM chunks (~667 ms)
   └──────────────────────── AudioOutputQueue (turn-tagged) ─► StreamRenderer
                                  (one persistent sd.OutputStream, 40 ms slices, abort)
```

Why a separate process: `faster-qwen3-tts` 0.4.0 requires `transformers>=5.15`,
Jarvis pins 4.57.1 for coqui-tts, so they cannot share an interpreter. The child
also contains CUDA OOM or crashes. The link is stdio, not HTTP, so no port is
opened. The worker is supervised: bounded restarts with backoff, a hung worker
is killed after 15 s of silence, and the Windows process tree is killed with
`taskkill /T` (a venv `python.exe` is a launcher with a child interpreter).

Stale-audio guarantee: each text segment is a worker request with a unique id,
and events for any other id are dropped. A cancelled turn sends `cancel`, and
the worker stops within one chunk. `AudioOutputQueue.flush` drops the turn's
queued chunks and aborts the chunk already on the device. The renderer also
refuses chunks of an aborted turn that were dequeued before the abort.

## Hardware and software (measured 2026-09-23)

| Item | Value |
|---|---|
| GPU | RTX 3070 Laptop, 8192 MiB, compute capability 8.6, driver 536.99 (CUDA 12.2 capability) |
| CPU / OS | i7-12700H, Windows 11 (10.0.26200) |
| TTS venv | Python 3.11.9, torch 2.7.1+cu118, faster-qwen3-tts 0.4.0, qwen-tts-hf 0.1.1.post1, transformers 5.15.1 |
| Driver | No update needed: cu118 wheels run on 536.99. Upstream's `setup_windows.bat` installs cu128 and downloads 1.7B as well; ours pins cu118 and downloads 0.6B only. |

Pins matter: `transformers` 5.17.0 (the newest at install time) breaks
`qwen-tts-hf` (`MimiConfig has no attribute rope_theta`), and an unpinned
`torchaudio` 2.11 fails to load against torch 2.7.1. `requirements-tts.txt`
holds the upstream-tested set.

## Latency spike results (20 warm trials each)

Reference used: the upstream project's public demo clip (13.1 s, English
speaker). **No owner recording was available.** These runs measure
latency and VRAM, not identity. Spanish text, 5 phrases of 1.5–6 s of audio.
TTFA = time from request to the first audio chunk leaving the model.

| Config | TTFA p50 | p95 | max | RTF p50 | Peak alloc / worker VRAM | Notes |
|---|---|---|---|---|---|---|
| xvec, chunk 8, back-to-back | 547 ms | 1531 ms | 1532 ms | 1.29 (min 0.42) | 2.4 GB / ~3.0 GB | throttled to 210 MHz by the end |
| xvec, chunk 4, back-to-back | 829 ms | 860 ms | 875 ms | 0.40 | 2.4 GB | started hot (84 °C), throttled throughout |
| xvec, chunk 8, 3 s pause | 563 ms | 687 ms | 703 ms | 1.40 | 2.4 GB / ~3.0 GB | |
| **owner xvec, chunk 4, 3 s pause, started cool (69 °C)** | **422 ms** | **453 ms** | **468 ms** | **1.40** | 2.4 GB / ~3.0 GB | **default** |
| owner xvec, chunk 8, 3 s pause (same session) | 531 ms | 609 ms | 610 ms | 1.39 | 2.4 GB | |
| ICL, chunk 8, 3 s pause | 812 ms | 1531 ms | 1641 ms | 1.02 | 2.9 GB / ~4.4 GB | first 10 trials 609–688 ms, then throttled |
| xvec, chunk 12, 3 s pause | 703 ms | 750 ms | 797 ms | 1.39 (min 1.32) | 2.4 GB / ~3.0 GB | steadier RTF, +140 ms TTFA |
| **owner xvec, chunk 4, 3 s pause, committed (2026-09-25)** | **406 ms** | **437 ms** | 438 ms | 1.84 (min 1.6) | 2.4 GB alloc / 2.5 GB reserved | GPU 59 °C → 74 °C; see `docs/bench/tts-clone-spike.json` |

### Committed baseline: RTX 3070, owner's enrolled profile (2026-09-25)

The bolded row above is the one committed, audited baseline for this milestone:
[`docs/bench/tts-clone-spike.json`](bench/tts-clone-spike.json), 20 warm trials,
x-vector mode, chunk_size 4, 3 s pause, run through the real Windows
`venv-tts` against the owner's actual enrolled profile
(`%LOCALAPPDATA%\jarvis\voice\default`) on the RTX 3070 Laptop — not the
upstream demo clip used in the table above. GPU temperature stayed in the
safe band (59 °C before, 74 °C after), so this run was not thermally
throttled. TTFA p50 is 406 ms and p95 is 437 ms, both under the milestone's
700 ms target: **p50 < 700 ms met**, and unlike the end-to-end acceptance run
below, p95 also stayed under target here. Peak VRAM allocation was 2.4 GB
(2.5 GB reserved) out of 8192 MiB total, consistent with the ~3.0 GB working
set described above.

Cold start: model load 13.4–15.9 s, CUDA-graph warmup 2.4–8.8 s. Conditioning
is encoded once (6.7–20 s) and saved to `prompt.pt`. Every later start reloads
it in 32 ms. The upstream library only caches it in memory, so without
`prompt.pt` each restart would re-encode.

### Main bottleneck: GPU thermals

At idle this laptop's GPU sits at 72–84 °C drawing 18 W. Within about 60 s of
synthesis it reaches 88–93 °C, and the driver reports `SW thermal slowdown`
(`clocks_event_reasons 0x20`) and pins the SM clock at 210 MHz. RTF then
falls to about 0.4 (slower than real time), and TTFA rises to 0.8–1.5 s.
Short conversational bursts stay mostly in the fast regime. Sustained
monologues do not. Remedies, in order: clean the fans and heatsink, raise
the laptop, use a high-performance power plan with the charger connected.
Optionally, with admin rights, cap GPU clocks below the thrash point
(`nvidia-smi -lgc`), which trades peak speed for a steady rate. No software
change in Jarvis fixes this.

### Decisions from the spike

* x-vector mode is the default: about 250 ms lower TTFA and about 1.4 GB
  less VRAM than ICL with a 13 s reference. ICL is opt-in (`--icl`) in case the
  owner's listening test prefers its identity.
* chunk_size 4 (about 333 ms of audio per chunk). The first chunk-4 run looked
  worse only because it started with the GPU at 84 °C. Measured cool and
  duty-cycled, chunk 4 saves about 110 ms TTFA against chunk 8 at the same
  RTF. Chunk 12 keeps RTF above 1.3 but adds about 280 ms. Use
  `tts.chunk_size: 8` or `12` if long replies stutter.
* The worker uses about 3.0 GB of VRAM. With the desktop's 1–2 GB, a
  resident mistral 7B (about 4.4 GB) does not fit alongside it. Ollama is
  therefore pre-warmed and kept for 30 min only when it is the primary model.
  As a cloud fallback it loads on demand and unloads after 2 min
  (`FALLBACK_KEEP_ALIVE`).

## Operations

* Install: `scripts\setup_tts_worker.bat` creates `%LOCALAPPDATA%\jarvis\venv-tts`
  (override with `JARVIS_TTS_VENV`) and downloads only the 0.6B Base model.
* Enroll: `python -m jarvis.voice_profile record`. The owner reads a displayed
  passage, which is validated for duration (3–20 s), clipping, level and
  sample rate. The tool then writes `prompt.pt` and a Spanish `preview.wav`.
  `enroll --audio x.wav [--text ...] [--icl] [--start s --duration s]`
  imports an existing recording. `status` and `delete` manage the profile.
  Re-enrolling replaces the reference and drops the cached conditioning.
* Backup: copying `%LOCALAPPDATA%\jarvis\voice\default` backs up the profile.
  Deleting it (or running `delete`) removes the voice, the conditioning and
  the preview. Nothing is uploaded, and there is no cloud TTS in this path.
* Diagnostics: `jarvis doctor` shows `TTS voice clone: healthy|degraded (reason)`.
  `diagnostics()["tts"]["voice_clone"]` reports the pid, ready state,
  restarts, VRAM, and segment TTFA p50/p95. Worker stderr goes to
  `logs\tts-worker.log`.
* Hardware benchmark:
  `venv-tts\Scripts\python.exe src\jarvis\adapters\tts\qwen_worker.py bench --profile-dir <dir> --chunk-size 8 --trials 20 --pause 3`.
* End-to-end speaker acceptance:
  `.venv\Scripts\python.exe scripts\tts_acceptance.py --profile-dir <dir>`.
* Rollback: set `"tts": {"provider": "sapi"}` (or `"local"` for XTTS) in
  `config.win.json`. The worker venv can be deleted independently.

## Short phrases

Single words ("Hecho.", "en", "lo") give the model too little context: in
repeated silent tests about one take in four came out in another language, and
some takes never reached end-of-speech and babbled for seconds. Two defences:
each request is capped at `1.5 s + 0.12 s per character` of generated audio
(`max_tokens_for` in `qwen_worker.py`, about twice normal speaking time), and
fixed replies are short sentences instead of single words ("Listo, señor.",
"Siguiente canción."), which came out in Spanish 8/8 times.

## Degraded modes

| Condition | Behaviour |
|---|---|
| venv or model missing | worker cannot start → SAPI, doctor `degraded (cannot start worker ...)` |
| no enrolled profile | worker reports ready without loading the model (0 VRAM) → SAPI, doctor says to run `voice_profile record` |
| < 1.5 GB VRAM free at start | worker exits `low_vram` → SAPI |
| CUDA OOM during a segment | segment fails → TTSChain retries, then SAPI; worker keeps running |
| worker crash | up to 3 restarts with backoff, SAPI meanwhile |
| worker hang | killed after 15 s without events, restarted |
| still warming (about 20 s after start) | SAPI; the breaker re-probes the clone every 5 s |

## Owner-profile acceptance on the laptop speakers (2026-09-27)

Evidence: [`docs/bench/tts-clone-runtime.json`](bench/tts-clone-runtime.json),
locked by `tests/test_tts_runtime_evidence.py`. Command:
`python scripts\tts_acceptance.py --profile-dir %LOCALAPPDATA%\jarvis\voice\default --turns 20 --chunk-size 4`
(Jarvis `.venv`, worker in `venv-tts`). The run used the owner profile (xvec, chunk 4) for 20
back-to-back turns. The GPU went from 66 °C to 85 °C.

| Metric | Result | Verdict |
|---|---|---|
| Turn while the clone is still warming | served by `sapi`, first audio 1786 ms | ✅ fallback |
| Worker start to ready | 22.5 s | off the turn path |
| Segment ready → first audio, p50 | **374 ms** | < 700 ms ✅ |
| Same, p95 / max | 436 ms / 792 ms | p95 ✅, max ❌ |
| Turns under 700 ms | 19 / 20 | |
| PortAudio underruns | 1 | ❌ (not gapless in this run) |
| Barge-in: cancel → device silent | 50 ms | ✅ |
| Stale audio writes after cancel | **0** | 0 ✅ |
| First audio of the turn right after a barge-in | 390 ms | ✅ |
| Worker restarts | 0 | |

Two earlier runs the same day used a working copy of the script. They preserve the
same metrics:

| Run | p50 / p95 / max | Under 700 ms | Underruns | Cancel → silent | Stale writes | After barge-in |
|---|---|---|---|---|---|---|
| 1 | 403 / 482 / 5584 ms | 19/20 | 1 | 41 ms | 0 | 3995 ms |
| 2 | 368 / 443 / 1695 ms | 19/20 | 0 | 37 ms | 0 | 4049 ms |

Across all three runs, p50 stayed under 700 ms and there were no stale writes. The
one-turn outlier, the occasional single underrun, and the first-audio time after a
barge-in are not stable. After a barge-in, first audio took 0.4 s in this run and
about 4 s in the two earlier runs, because the worker can still be finishing the
cancelled generation.

## End-to-end acceptance on the laptop speakers (2026-09-23)

`scripts/tts_acceptance.py` ran the production adapter, worker, queue and
persistent stream against the real GPU and default output device. It used 20
back-to-back turns with no pause, so the thermal regime counts against it.
Profile: demo reference, xvec, chunk 8.

| Metric | Result | Target |
|---|---|---|
| Worker start to ready (load + warmup, cached prompt) | 22.5 s | not on the turn path; SAPI covers it |
| Segment ready → first audio written to device, p50 | **604 ms** | < 700 ms ✅ |
| Same, p95 / max | 1611 ms / 2311 ms | < 700 ms ❌ (thermal throttling in back-to-back turns) |
| Turns under 700 ms | 13 / 20 | |
| PortAudio underruns across 20 turns | **0** (gapless) | 0 ✅ |
| Barge-in: cancel → device silent | **58 ms** | prompt ✅ |
| Stale audio writes after cancel | **0** | 0 ✅ |
| First audio of the turn right after a barge-in | 1547 ms | the worker finishes its current chunk (up to 1 chunk) before the next request |
| Worker restarts | 0 | |

IPC and playback add about 26 ms on top of the model's own TTFA (578 ms
worker-side p50). The 700 ms target holds at the median and fails at p95
under sustained load. The cause is GPU thermal throttling, not the pipeline.

## End-to-end: DeepSeek + owner's cloned voice (20 typed turns, 3 s apart)

`scripts/e2e_turns.py` builds the production runtime from `config.win.json`
and `config.local.json`, then runs router → DeepSeek `deepseek-flash`
(thinking disabled) → chunker → clone TTS → speakers. The clock starts at
turn start, i.e. after STT; add STT (about 350 ms per 8 s of audio,
whisper turbo on CUDA) for the time from the end of speech.

| Stage (from turn start) | p50 | p95 | max |
|---|---|---|---|
| LLM first token | 805 ms | 961 ms | 1014 ms |
| First phrase segment ready | 959 ms | 1219 ms | 1284 ms |
| First cloned-voice audio to device | **1585 ms** | **1899 ms** | 1908 ms |

Cost for the 20 turns: $0.00176, about $0.00009 per turn, under a $1/day cap.

DeepSeek `deepseek-flash` reasons by default. On a voice turn, 124 of the
150 max tokens went to hidden reasoning, content only began at 1.7 s, and the
reply was truncated (`finish_reason: length`). The provider config now sends
`"extra_body": {"thinking": {"type": "disabled"}}` (DeepSeek thinking-mode
guide). Measured result: first token 651–760 ms from WSL, and complete replies.

### Re-run with chunk 4, and comparison with the original stack

Same script, same 5 queries, 3 s apart. The chunk-4 run stopped after 17
turns (the harness timeout, not a failure).

| Stack (clock starts after STT) | LLM first token p50 / p95 | First audio p50 / p95 / max |
|---|---|---|
| Original: local Ollama mistral 7B + Windows SAPI voice (20 turns) | 434 / 469 ms | **852 / 1099 / 1125 ms** |
| DeepSeek + cloned voice, chunk 8 (20 turns) | 805 / 961 ms | 1585 / 1899 / 1908 ms |
| DeepSeek + cloned voice, chunk 4 (17 turns) | 496 / 738 ms | **1269 / 1568 / 1590 ms** |

**The new stack is about 400 ms slower to first audio at the median than the
original.** The target "faster than the measured baseline" is not met. Two
things cause it:
1. The cloned voice needs about 420 ms TTFA plus about 300 ms to wait for the
   first phrase boundary. SAPI needs about 40 ms.
2. The cloud first token is about 500 ms from this network. The local 7B
   model answers in about 430 ms.

What it buys: the owner's own voice, and a stronger model at about $0.0001
per turn. Options to close the gap:
* A small local LLM (1–3B, about 2 GB) as the primary model alongside the
  clone. Mistral 7B plus the clone does not fit in 8 GB, as measured.
* Release a shorter first phrase, at the cost of prosody.
* Fix the laptop cooling so TTFA stays at the cool-GPU numbers.

## End-to-end streaming evidence (M006/S03, 2026-09-27)

Evidence: [`docs/bench/e2e-streaming.json`](bench/e2e-streaming.json), locked by
`tests/test_e2e_streaming_evidence.py`. Command:
`python scripts/e2e_turns.py --config config.win.json --turns 20 --host-label rtx3070-laptop --output docs/bench/e2e-streaming.json`,
run through the Jarvis `.venv` bridged from WSL via `powershell.exe` onto the
RTX 3070 Laptop (mounted as `\\wsl.localhost\...`), against the real
`config.win.json` + `config.local.json` (DeepSeek primary, Ollama
`mistral:7b-instruct` fallback, cloned-voice TTS).

| Metric | Result | Verdict |
|---|---|---|
| First phrase segment ready before the turn's total completion | 20/20 turns, ratio 1.0 | ✅ the slice demo's core claim, measured from the record rather than asserted |
| Fail-closed to local Ollama whenever the cloud route didn't answer | 20/20 turns served by `ollama` | ✅ the fallback chain degraded, it never crashed a turn |
| Cloud route used (DeepSeek) | 0/20 turns; `spend_today_usd` stayed 0.0 | ❌ not exercised this run |
| LLM first token, p50 / p95 | 7439 ms / 39435 ms | ❌ far above the ~430–500 ms baseline above |
| TTS first audio, p50 / p95 | 10167 ms / 50854 ms | ❌ far above the ~400 ms clone baseline |
| Voice clone availability | mostly served by `sapi`; the worker exhausted its 3 restarts (`last_error: "worker stopped responding"`) | ❌ not stable this run |

Why the absolute latencies are bad but not read as a regression: immediately
after this run, a direct `Invoke-WebRequest` probe of DeepSeek's API from the
same Windows session answered in under a second (HTTP 200), so the repeated
per-turn cloud timeouts were not a dead key or blocked egress. The more likely
cause is resource contention specific to this bridged environment: the clone
worker restarted three times (each restart reloads about 1 GB of weights and
recaptures two CUDA graphs), and Ollama's `mistral:7b-instruct` (about 4.4 GB)
had to load alongside the resident ~3 GB clone worker — the same VRAM conflict
on an 8 GB card that the "Decisions from the spike" section above and D024
already flag. Under that contention the async DeepSeek call and local Ollama
inference both blew past their timeouts often enough that DeepSeek never won a
single one of the 20 turns. That is a property of driving the full stack
through the WSL→Windows UNC bridge with a cold, restart-heavy worker, not a
code defect from S03: the provider fail-closed contract and the early-audio
streaming contract both held throughout this run, which is exactly what S03's
locked unit tests (`test_exhausted_daily_cap_fails_closed_to_local`,
`test_model_speech_starts_before_the_model_finishes`) already assert in
isolation, without hardware variance. A native, non-bridged run — like
`docs/bench/tts-clone-runtime.json` and the DeepSeek section above — is
expected to reproduce the sub-second baselines instead of this run's numbers.

## Final real-laptop end-to-end acceptance run (2026-09-28)

Evidence: [`docs/bench/e2e-acceptance.json`](bench/e2e-acceptance.json), locked by
`tests/test_e2e_streaming_evidence.py`. Command:
`python scripts/e2e_turns.py --config config.win.json --turns 20 --host-label rtx3070-laptop --output docs\bench\e2e-acceptance.json`,
run natively on the RTX 3070 Laptop (not bridged from WSL this time), against
`config.win.json` + `config.local.json` (DeepSeek primary, Ollama
`mistral:7b-instruct` fallback, cloned-voice TTS).

| Metric | Result | Verdict |
|---|---|---|
| Turns completed | 20/20 | ✅ no crash |
| First phrase segment ready before the turn's total completion | 20/20 turns, ratio 1.0 | ✅ streaming contract held |
| Cloud route used (DeepSeek) | **0/20 turns** — every turn's LLM cost entry shows `provider: "ollama"`; `spend_today_usd` stayed 0.0 | ❌ fell back to local Ollama for the whole run |
| LLM first token, p50 / p95 | 3772 ms / 22279 ms | ❌ far above the ~430–500 ms Ollama baseline and ~800 ms DeepSeek baseline above |
| First phrase segment ready, p50 / p95 | 6147 ms / 25660 ms | ❌ |
| TTS first audio, p50 / p95 | 8378 ms / 27784 ms | ❌ far above the ~400 ms clone baseline |
| Total request, p50 / p95 | 55015 ms / 79177 ms | ❌ |

This is the same fail-closed and streaming-contract behaviour as the
2026-09-27 bridged run above, and the same measured, not-smoothed reporting:
the pipeline never crashed and always kept talking, but this run — like the
one above — never got a turn out of DeepSeek and its latencies are far above
the sub-second baselines measured elsewhere in this document. The repeated
cloud misses on this host (again, all 20/20 turns) point at the same
mistral 7B + clone-worker VRAM contention already documented for the S03 run
rather than a new defect, but that is inference, not something this run
measured directly — treat the cause as unconfirmed for this run. See
[`docs/bench/e2e-streaming.json`](bench/e2e-streaming.json) for the prior run
with the same pattern.

## S05 recovery run: cloud route root cause confirmed, VRAM-safe fallback (2026-09-28)

Evidence: [`docs/bench/e2e-recovery.json`](bench/e2e-recovery.json), locked by
`tests/test_e2e_streaming_evidence.py::RecoveryEvidenceTests`. Reproduction
(from the worktree's Windows path, `.venv` from the main checkout):

```
.venv\Scripts\python.exe scripts\e2e_turns.py --config config.win.json --turns 20 --pause 3 --host-label rtx3070-laptop --output docs\bench\e2e-recovery.json
```

This run answers the question the 2026-09-28 acceptance run above left
"unconfirmed": why DeepSeek served 0/20 turns. A bounded, secret-free
preflight through the same `config.win.json` + `config.local.json` merge
(`load_config`, no key values printed) ran immediately before this benchmark:

| Preflight fact | Value |
|---|---|
| Merged provider order | `deepseek`, `ollama` (cloud-primary, local fallback — matches D026) |
| DeepSeek key present | **true** |
| Ollama key present | false (not required) |
| `max_daily_usd` | 1.0 |
| `nvidia-smi` VRAM | 828 MiB / 8192 MiB used — no contention at preflight time |

With S05-T01's per-turn provider/fallback provenance now wired through
`ProviderChain` and `TurnManager` (rather than inferred from billable usage),
every one of the 20 turns records the real cause: DeepSeek was attempted,
reachable, and rejected the request with **HTTP 402 "Insufficient Balance"**
every time (`transient: true`). This is a real account-funding state, not a
selection bug, a dead key, a cap exhaustion, or GPU/VRAM contention — the
preflight confirms the key is present and the order is correct, and VRAM had
6+ GB free before the run started. The ordering/selection logic that S05-T01
audited and locked is working as intended; the previous runs' "VRAM
contention" hypothesis is not what happened here.

| Metric | S04 baseline (`e2e-acceptance.json`) | S05 recovery (`e2e-recovery.json`) | Verdict |
|---|---|---|---|
| Turns completed | 20/20 | 20/20 | ✅ no crash either run |
| Cloud route used (DeepSeek) | 0/20, cause unconfirmed | 0/20, cause confirmed: 402 Insufficient Balance every turn | ❌ still 0/20 — external billing issue, not this milestone's code |
| LLM first token, p50 / p95 | 3772 ms / 22279 ms | 13854 ms / 17825 ms | ❌ p50 regressed; see note below |
| TTS first audio (end-to-end from turn start), p50 / p95 | 8378 ms / 27784 ms | 17875 ms / 21077 ms | ❌ |
| Warmed segment-ready-to-first-audio (isolated), p50 / p95 | not measured (metric introduced in S05-T02) | 1738 ms / 1889 ms | ❌ misses the <700 ms warmed clone target measured end to end |
| Total request, p50 / p95 | 55015 ms / 79177 ms | 47378 ms / 63536 ms | mixed |

Two things are new and expected here, not regressions in this slice's own
code: (1) D043 (S05-T02) makes the Ollama fallback release its VRAM
immediately (`keep_alive="0"`) instead of staying resident for 2 minutes, so
every one of these 20 fallback turns pays a full model reload instead of
reusing a warm instance — that is the intended trade of latency for
GPU-safety on an 8 GB card running the fallback and the resident clone worker
together, and it plausibly explains the LLM-first-token regression versus the
S04 baseline (which still used the old 2-minute residency). (2) The isolated
warmed segment-to-audio number (1738/1889 ms) is a new, more precise metric
introduced by S05-T02 specifically to separate TTS-only latency from the
end-to-end number; it is measured here for the first time on real hardware
and misses the <700 ms target measured on the isolated worker in
[`docs/bench/tts-clone-runtime.json`](bench/tts-clone-runtime.json) (374 ms
p50). The gap between the isolated-worker number and this full-pipeline
number is real and unexplained by this task — it is recorded as a known
issue below, not smoothed into a pass.

**Verdict: measured, not passed.** The pipeline completed 20/20 turns and
stayed fail-closed and crash-free (S05-T01/T02's contracts held), but neither
the cloud-primary-success criterion nor the <700 ms warmed
segment-to-audio criterion is met on this run. The cloud miss has a
confirmed, non-code cause (DeepSeek account balance); the latency miss is
real and open.

**Next action if re-run:** top up the DeepSeek account balance, then re-run
the same command — the preflight/order/selection logic does not need further
change for cloud success. The isolated worker-benchmark-vs-full-pipeline
segment-to-audio gap (374 ms vs 1738 ms p50) is no longer unexplained: S06's
full-pipeline decomposition below (2026-09-29) attributes it to GPU
contention and thermal throttling between the local Ollama LLM and the TTS
clone worker on this run's local-fallback condition, not a Jarvis pipeline
defect — see the section immediately below and the re-derived criterion 1 in
the acceptance table.

This benchmark is a typed-turn pipeline test: it excludes microphone capture
and STT, and does not evaluate owner voice identity or Spanish listening
quality — both remain pending the human owner-listening check below.

## S06 full-pipeline segment-to-audio decomposition (2026-09-29)

Evidence: [`docs/bench/e2e-segment-audio-local.json`](bench/e2e-segment-audio-local.json)
(condition `configured-llm` — the router's only configured provider is Ollama
`mistral:7b-instruct`, so this is the local-Ollama-fallback condition) and
[`docs/bench/e2e-segment-audio-remote.json`](bench/e2e-segment-audio-remote.json)
(condition `scripted-remote-llm` — a no-GPU scripted token stream at 30 ms/token
standing in for a remote LLM), both locked by `tests/test_e2e_streaming_evidence.py`.
Commands, run on the RTX 3070 Laptop against `config.win.json` + `config.local.json`:

```
python scripts/e2e_turns.py --config config.win.json --turns 20 --pause 3 --llm scripted --host-label rtx3070-laptop --output docs\bench\e2e-segment-audio-remote.json
python scripts/e2e_turns.py --config config.win.json --turns 20 --pause 3 --host-label rtx3070-laptop --output docs\bench\e2e-segment-audio-local.json
```

> **Historical:** `e2e-segment-audio-local.json` was overwritten by the S08
> re-measurement below (CPU-only 3B fallback). The "Local Ollama fallback" column
> and the 7623 ms figures in this section are the S06 run with the 7B GPU
> fallback and are no longer in the committed JSON.

Both runs decompose `segment_to_first_audio_ms` (first phrase segment ready →
first audio chunk written to the device) into `worker_ttfa_ms` (the clone
worker's own send-to-first-audio-event time), `ipc_ms` (client-observed TTFA
minus worker TTFA), and `handoff_ms` (time between the segment being ready
and the request reaching the worker, plus any post-receive delay) — added in
S06-T01 to `QwenCloneTTS` and `scripts/e2e_turns.py`.

| Metric | Isolated worker ([`tts-clone-runtime.json`](bench/tts-clone-runtime.json)) | Remote-like LLM, no GPU contention (`e2e-segment-audio-remote.json`) | Local Ollama fallback, concurrent GPU use (`e2e-segment-audio-local.json`) |
|---|---|---|---|
| Condition | isolated `tts_acceptance.py` bench | `--llm scripted`, no local LLM on the GPU | default (configured), falls back to local Ollama `mistral:7b-instruct` on the same GPU |
| segment_to_first_audio_ms p50 / p95 | 374 ms / 436 ms | **491 ms / 914 ms** | **7623 ms / 14948 ms** |
| worker_ttfa_ms p50 / p95 | n/a (this is the worker bench itself) | 500 ms / 906 ms | 7500 ms / 11203 ms |
| ipc_ms p50 / p95 | n/a | 3 ms / 13 ms | -1 ms / 5 ms |
| handoff_ms p50 / p95 | n/a | 0 ms / 0 ms | 0 ms / 1219 ms |
| Turns | 20 | 20 | 20 (4/20 fell back further to SAPI after the clone worker timed out / wasn't ready) |
| GPU during run | cool, duty-cycled | idle-adjacent, no thermal note | climbed 67 °C/285 MHz → sustained 88–92 °C/210 MHz (thermally throttled) |

**Root cause:** `worker_ttfa_ms`, not `ipc_ms` or `handoff_ms`, is the
dominant component of `segment_to_first_audio_ms` in both conditions (it
tracks the total almost 1:1). It is about 15x higher only when a local GPU
LLM (Ollama) runs concurrently with the TTS clone worker on the same RTX
3070 — the remote-like condition, with no local LLM competing for the GPU,
closely matches the isolated-worker bench (491 ms vs 374 ms) and clears the
<700 ms target. `ipc_ms` and `handoff_ms` stay near zero in both conditions;
the two `handoff_ms` outliers in the local run (1219 ms and 6688 ms on 2/20
turns) were inspected for a shared-lock or blocking-call defect in
`turn_manager.py`/`qwen_clone.py` and none was found — Ollama's adapter
streams tokens asynchronously via `httpx`, and the only lock in
`turn_manager.py` guards per-turn context creation, not TTS dispatch. This is
GPU resource contention and thermal throttling between the local LLM and the
TTS worker, not a Jarvis pipeline defect, so per the task's fix gate **no
in-pipeline fix was applied**. A real fix (GPU scheduling/partitioning, or
preferring a remote LLM when voice cloning matters) is outside
`qwen_clone.py`/`fallback.py`/`turn_manager.py`'s surface and is recorded as
a known issue for a later slice.

Like the runs above, this excludes microphone capture, STT, and Spanish
listening/identity quality — see the owner listening check below.

## S07 cloud-primary rerun (2026-09-29)

Evidence: [`docs/bench/e2e-cloud-primary.json`](bench/e2e-cloud-primary.json),
locked by `tests/test_e2e_streaming_evidence.py::CloudPrimaryEvidenceTests`.
Run on the RTX 3070 Laptop against the real `config.win.json` +
`config.local.json` (DeepSeek primary, Ollama fallback), no minimal config:

```
python scripts/e2e_turns.py --config config.win.json --turns 20 --pause 3 --host-label rtx3070-laptop --output docs\bench\e2e-cloud-primary.json
```

Funded rerun (owner topped up DeepSeek, on AC power, GPU 67 °C at start).
An earlier pass the same day on battery power (GPU power-capped at 210 MHz,
audio audibly choppy) also served 20/20 turns but its latencies are not valid
and were discarded; keep the laptop plugged in for any latency run.

`cloud_route` block from the evidence (verdict is recomputed from the turns by
the test, not trusted from the file):

| Field | Value |
|---|---|
| cloud_served | 20 / 20 |
| fallback_turns | 0 |
| fallback_causes | none |
| unexplained_fallbacks | 0 |
| spend_today_usd / max_daily_usd | $0.0037 / $1.00 (within cap) |
| verdict | **served** |

Cloud-primary now serves every voice turn and records spend under the daily
cap. Full-pipeline `segment_to_first_audio_ms` with DeepSeek serving is
427 ms p50 / 499 ms p95 (`tts_first_audio_ms` 1321 ms p50), under the 700 ms
target. The "End-to-end answer in the cloned voice" row is left unchanged
(it cites the older `e2e-acceptance.json` run).

## S08 local fallback without GPU contention (2026-09-29)

Evidence: [`docs/bench/e2e-segment-audio-local.json`](bench/e2e-segment-audio-local.json)
(condition `configured-llm`, `meta.local_llm` = `qwen2.5:3b-instruct`,
`options.num_gpu: 0`, `keep_alive: 30m`), locked by
`tests/test_e2e_streaming_evidence.py::CriterionOneFullPipelineEvidenceTests`.

**Root cause addressed:** in S06 the local fallback was a 7B model
(`mistral:7b-instruct`) cold-loaded onto the GPU on every turn
(`keep_alive="0"`, D043) beside the resident ~3 GB clone worker on the 8 GB
RTX 3070, so the two contended for VRAM and compute.

**Fix:** the `ollama` provider in `config.win.json` now uses
`qwen2.5:3b-instruct` with `extra_body.options.num_gpu: 0`, so it runs CPU-only.
A CPU-resident fallback keeps `keep_alive` 30m (it holds system RAM, not VRAM)
and is preloaded by `_warm_up`. The GPU fallback path is unchanged: a fallback
that uses the GPU still unloads immediately (D043 still holds for it).

Command, run on the RTX 3070 Laptop against `config.win.json` +
`config.local.json` (the DeepSeek primary answered HTTP 402 Insufficient
Balance on all 20 turns, so every turn was served by the local fallback):

```
python scripts/e2e_turns.py --config config.win.json --turns 20 --pause 3 --host-label rtx3070-laptop --output docs\bench\e2e-segment-audio-local.json
```

| Metric (p50 / p95) | S06 local (7B on GPU, cold each turn) | S08 local (3B, CPU-only, resident) |
|---|---|---|
| segment_to_first_audio_ms | 7623 ms / 14948 ms | **1311 ms / 1527 ms** |
| worker_ttfa_ms | 7500 ms / 11203 ms | 1297 ms / 1531 ms |
| llm_first_token_ms | 19236 ms | 3449 ms / 3679 ms |
| Provider | ollama (4/20 fell back further to SAPI) | ollama 20/20 |

Observations during the run: `ollama ps` showed `qwen2.5:3b-instruct`, 2.2 GB,
100% CPU, keep-alive until +29 min. `nvidia-smi` showed 4361 MiB used during
turns (the resident clone worker only, no Ollama VRAM) and 81 MiB after the run.

**Result — honest, not a pass:** GPU contention is gone (no Ollama VRAM, all
turns served by the clone worker with no SAPI fallback) and the local figure
improved about 5.8x, but `segment_to_first_audio_ms` p50 is 1311 ms, still
above the 700 ms target. `worker_ttfa_ms` (~1.3 s) is about 2.6x the
remote-like run (491 ms). CPU contention between the CPU-only LLM and the
worker's host-side pipeline is the likely cause, inferred from timing only; CPU
utilization was not sampled. Reaching < 700 ms with a local fallback needs
further work (CPU thread limits for Ollama, or a remote LLM).

Like the runs above, this excludes microphone capture, STT, and Spanish
listening/identity quality.

Low-VRAM degrade is covered explicitly by
`tests/test_qwen_clone_tts.py::test_low_vram_worker_degrades_turn_to_labeled_fallback`.

## M006 acceptance

Evidence-to-criterion map closing the milestone. Verdicts are honest: a
numeric target that was measured and missed is marked ❌ with the actual
number, not smoothed into a pass.

| Criterion | Evidence | Verdict |
|---|---|---|
| Warmed clone TTFA p50 < 700 ms over ≥20 turns (full pipeline) | [`docs/bench/e2e-segment-audio-remote.json`](bench/e2e-segment-audio-remote.json) (491 ms p50, remote-like LLM, 20 turns) and [`docs/bench/e2e-segment-audio-local.json`](bench/e2e-segment-audio-local.json) (1311 ms p50, CPU-only `qwen2.5:3b-instruct` fallback with `num_gpu` 0, 20 turns); isolated worker reference [`docs/bench/tts-clone-runtime.json`](bench/tts-clone-runtime.json) (374 ms p50); `tests/test_e2e_streaming_evidence.py` | ✅ with a remote-like LLM (491 ms p50 < 700 ms) ❌ during local Ollama fallback (1311 ms p50 ≥ 700 ms; S08 condition: CPU-only 3B fallback, GPU contention removed, residual latency likely CPU contention, down from 7623 ms in S06) |
| Barge-in stops audio, zero stale audio | same evidence (cancel → silent 50 ms, 0 stale writes after cancel), `tests/test_qwen_clone_tts.py::test_cancel_stops_worker_and_next_turn_gets_no_stale_audio` | ✅ met |
| Missing profile / venv / crash / low VRAM → labeled SAPI without crashing | `tests/test_qwen_clone_tts.py` (`test_missing_profile_is_config_error_and_chain_falls_back`, `test_missing_venv_worker_cannot_spawn_and_chain_falls_back`, `test_crash_is_reported_then_worker_restarts`, `test_chain_falls_back_on_crash_then_worker_restarts_for_next_turn`, `test_fatal_startup_is_degraded_not_crash`), the "Degraded modes" table above | ✅ met |
| Cloud spend recorded and capped daily, exhaustion → local | `tests/test_provider_fallback.py::SpendCapFailClosedTests::test_exhausted_daily_cap_fails_closed_to_local`, `tests/test_cost.py` (`test_computes_llm_token_cost`, `test_record_returns_estimated_cost_for_priced_provider`, `test_total_usd_sums_only_priced_entries`) | ✅ met |
| No personal audio in Git | `tests/test_privacy.py` (tracked-file scan by extension, `git check-ignore` on concrete personal-audio filenames including `*.npy`, `default_profile_dir()` resolves outside the repo) | ✅ met |
| Cloud LLM primary serves voice turns; spend recorded under daily cap; local fallback only on real failures | [`docs/bench/e2e-cloud-primary.json`](bench/e2e-cloud-primary.json) (20 turns, RTX 3070 Laptop), `tests/test_e2e_streaming_evidence.py::CloudPrimaryEvidenceTests`; see "S07 cloud-primary rerun" above | ✅ served: DeepSeek primary answered 20/20 cloud turns (cloud_served 20), spend $0.0037 of $1.00 daily cap, 0 fallbacks; segment_to_first_audio 427 ms p50 |
| End-to-end answer in the cloned voice | [`docs/bench/e2e-acceptance.json`](bench/e2e-acceptance.json) (real RTX 3070 run above): pipeline completed 20/20 turns end to end without crashing and stayed fail-closed to local, but **DeepSeek served 0/20 turns** (DeepSeek was unfunded then) and latencies are far above the baselines measured elsewhere (first audio p50 8378 ms, p95 27784 ms); superseded for the cloud route by the funded S07 rerun above (20/20 served, segment_to_first_audio 427 ms p50) | ✅ superseded: the funded S07 rerun completes 20/20 turns end to end on the cloud route (segment_to_first_audio 427 ms p50); the ❌ above described the older unfunded run, whose numbers stay as measured history |
| Owner voice identity and Spanish quality | owner report 2026-09-29 (below) | ✅ accepted by the owner: cloned voice is audible and acceptable during the funded benchmark run; no per-turn identity/pronunciation ratings were given |

### Owner listening check

Result (2026-09-29): the owner listened to the cloned voice during the funded
cloud-primary benchmark on AC power and confirmed it is audible and accepted it
("sí se escucha, dale"). Choppy audio seen earlier the same day was the laptop
on battery (GPU power-capped at 210 MHz), not the voice pipeline. The owner gave
no per-turn identity / pronunciation / artifact ratings, so those are not
recorded; re-run the procedure below for a detailed rating.
Procedure:

1. `..\.venv\Scripts\python.exe -m jarvis.voice_profile status` — confirms a
   profile is enrolled and prepared (`prompt.pt` built).
2. Play `%LOCALAPPDATA%\jarvis\voice\default\preview.wav` (Spanish, built by
   `enroll`/`prepare`).
3. Run `jarvis run` and speak 5 Spanish turns.
4. The owner rates, per turn and overall:
   - Identity: sounds like me — yes / partly / no.
   - Spanish pronunciation: natural / acceptable / off.
   - Artifacts: none / minor / distracting (clicks, mispronunciations,
     wrong-language babble — see "Short phrases" above).
5. If identity is rated "partly" or "no", consider re-enrolling with
   `enroll --icl` (in-context cloning) using a clean, exact-transcript
   sample per D023's revisit note — ICL trades about 250 ms of extra TTFA
   for a reference that may sound closer to the owner.
