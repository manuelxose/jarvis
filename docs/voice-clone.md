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
