# M005 latency report: before / after

Every number below cites its source. Nothing here is estimated or interpolated;
values not yet measured are listed in [Not measured yet](#not-measured-yet) instead
of being guessed.

## Scope & sources

Reference machine: i7-12700H, RTX 3070 Laptop 8 GB, Windows 11.

Sources:

- [`bench/latest.json`](bench/latest.json) — automated benchmark run, `meta.when`
  2026-09-24 09:25:57.
- Target-machine measurements recorded 2026-09-23 in decision D022 (`Low-latency
  local voice stack on the target Windows laptop`), written inline below rather
  than referenced by path.

## Before / after

| Metric | Before | After (p50 / p95) | Change | Source |
|---|---|---|---|---|
| STT transcription | Whisper small, CPU, ~1.3 s per 8 s audio | faster-whisper turbo, CUDA int8_float16, 0.35 s per 8 s audio (D022); `stt.transcribe_ms` 264.33 / 693.44 ms for a 2.5 s clip (latest.json) | ~3.7x faster on the 8 s/CUDA comparison | D022; `bench/latest.json` `stt.transcribe_ms` |
| LLM first token (local, Ollama) | 2.1 s via `localhost` (IPv6-first resolution) | 0.05-0.35 s via `127.0.0.1` | ~6-40x faster | D022 |
| LLM first token (cloud, DeepSeek) | no before measurement | 769.14 / 929.60 ms | n/a — after only | `bench/latest.json` `llm.ttft_ms` |
| End of utterance → first audible reply (warm) | no before measurement | 1381.76 / 1551.77 ms | n/a — after only | `bench/latest.json` `e2e.end_of_utterance_to_first_audio_ms` |
| Cached acknowledgement → audio device | no before measurement (live synthesis is the only prior path) | 0.18 / 0.7 ms (real output stream, silent); compare clone warm time-to-first-audio 207.94 / 220.01 ms for live synthesis | cached path avoids ~208 ms of live TTFA | `bench/latest.json` `command.cached_ack_to_device_ms`, `tts.warm_ttfa_ms` |
| Turn streaming (call count, not latency) | 2 `model.generate()` calls per conversational turn | exactly 1 call | halved LLM invocations per turn | `tests/test_turn_manager.py::test_generates_the_model_response_exactly_once` (S01) |
| Interruption (playback stopped) | no before measurement | 4.25 / 9.29 ms | n/a — after only | `bench/latest.json` `interrupt.playback_stop_ms` |

Notes:

- The STT "before" (small/CPU) and "after" (turbo/CUDA) rows use different model
  sizes and backends, per D022 — that decision changed both compute device and
  model, not just the device.
- "After" values are copied exactly as rounded in `bench/latest.json`; no
  recomputation or additional rounding was applied.
- The turn-streaming row counts model invocations, not wall-clock time — it is
  included because doubled generation calls were a direct latency multiplier
  before the fix, but it is not itself a timing measurement.

## Not measured yet

- **Cloud vs local STT.** No live comparison has been run. Procedure:
  [STT bake-off](stt-bakeoff.md).
- **TTS provider comparison / routing evidence.** No comparative TTS
  measurements exist in this repository; AUTO/FAST/CHEAP/QUALITY routing
  profiles keep the configured order until an operator supplies complete,
  comparable evidence. See
  [Operator-gated TTS routing (no measured winner yet)](alibaba-qwen.md#operator-gated-tts-routing-no-measured-winner-yet).
  No winner is claimed.
- **Live paid per-turn cost.** `turn.cost` reports `usd: null` for unpriced
  entries; no real-pricing run has been recorded.
- **CircuitBreaker fallback timing on real network failures.** The breaker's
  open/close transitions are exercised in tests but have no recorded timing
  from an actual network outage.

## Reproduce

```
python scripts\perf_bench.py --config config.win.json
```
