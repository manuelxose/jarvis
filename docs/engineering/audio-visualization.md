# Audio visualization telemetry (M011/S05)

Measured playback and microphone facts flow Python -> SSE -> `ui/src/services/core-signal-normalizer.ts` -> `createVisualCoreService`.
See [visual-core-contract.md](./visual-core-contract.md) for the state machine and [phase-4-runtime-audit.md](./phase-4-runtime-audit.md)
for the pre-S05 audit; this page only documents the S05 telemetry layer. Startup phases and app outcomes are in [cinematic-startup.md](./cinematic-startup.md).

## Wire shapes

Producer: `src/jarvis/observability/core_signals.py` (`CoreSignals`). Both events travel on the existing `/events` SSE stream.

- `core.signal`: `{session, generation, seq, age_ms, kind, ...kind fields}`. `seq` is allocated under the same lock as
  `hub.publish`, so delivery order equals `seq` order.
- `core.snapshot`: `{session, replaces_session, generation, watermark, age_ms, facts{turn,thinking,listening,playback,failed_turn,...},
  fences{cancelled,turn_ended,overflow,playbacks[],...}, output, microphone, ...}`. `watermark` is the last `seq` it covers.

| kind | extra fields |
|---|---|
| `turn-start` | `turn` |
| `turn-end` | `turn`, `ok` |
| `cancel-turn` | `turn` |
| `playback-start` | `turn`, `playback`, `confirmed=true`, `latency_ms` |
| `output` | `turn`, `playback`, `level`, `peak` (0..1, `age_ms` set) |
| `playback-end` | `turn`, `playback`, `outcome` = `ended` / `cancelled` / `failed` |
| `capture` | `active` |
| `microphone` | `level`, `peak` (`age_ms` set) |

## Where events originate

- Output / playback-start / failed: `adapters/audio/output.py:StreamRenderer._blocking` (after each `stream.write` slice).
- Cancelled: `StreamRenderer.abort`; ended / failed / flushed: `AudioOutputQueue.play`, `AudioOutputQueue.flush`.
- Turn start / cancel / end: `application/turn_manager.py` (`turn_start`, `cancel_turn`, `turn_end`).
- Capture / microphone: `application/voice_loop.py` (`capture(True/False)`, `microphone(*pcm_envelope(frame))`).
- Snapshot + generation: `apps/daemon.py` (one process-wide `CoreSignals`, `advance_generation()` per runtime start) and
  `observability/event_stream.py` (snapshot on connect, keepalive, overflow).

## Playback identity

- `playback-start` is emitted by the first `output()` call, i.e. **after the first slice was accepted by `stream.write`**. Nothing is
  claimed before that; a silent or fake renderer (`_silent_render`, test doubles) never calls `output()` and so never yields SPEAKING.
- Id is `"<turn>:1"`. Outcomes are `ended`, `cancelled` or `failed`; each fences the id, and later `output` for a fenced id is dropped
  (`dropped_late`). `abort()` fences before stopping the stream so no output can follow the cancel.
- Welcome / acknowledgement audio has no turn: the first `output()` creates an implicit turn (`turn-start`), and `playback_end` ends it.
- A new turn after an ended / cancelled one advances `generation`; `advance_generation()` on runtime restart fences live playback.

## Clock domains

Producers report `age_ms` only (age of the measurement at emit time). Snapshots add elapsed producer time to the stored age.
No cross-process timestamp subtraction is done anywhere; the UI never compares Python and renderer clocks.

## Throttles

- Output: one event per 40 ms slice (`StreamRenderer(slice_seconds=0.04)`), i.e. at most ~25 Hz, bound to real writes.
- Microphone: at most 20 Hz (`MIC_MIN_INTERVAL_S = 0.05`); extra frames increment `throttled`.
- Non-finite / non-numeric levels are rejected (`rejected`), levels are clamped to 0..1.

## Measured transport latency

`PYTHONPATH=src python -m unittest tests.test_core_signal_latency -v` (loopback SSE, n=200, run 2026-10-15):
**p50 0.81 ms, p95 1.61 ms, max 2.59 ms** from `publish` to receive. This is transport only on loopback; it excludes device latency
and UI render time.

## Device latency vs audibility

`latency_ms` in `playback-start` is the `latency` the PortAudio stream reports (device-reported output latency). "Device accepted the
slice" (`stream.write` returned) is not the same as "a human heard it": driver buffering, Bluetooth stacks and amplifiers add delay
not visible to PortAudio. S05 therefore claims *accepted-by-device* timing only.

## Motion and announcement behavior (M011/S08)

- Visible label/detail update immediately; one polite live region per core announces state, coalesced at 500 ms (latest value wins, terminal error/disconnected/startup outcomes preserved, pending work cancelled on replacement/unmount). Envelope updates never write to it.
- Disabled and reduced motion run no renderer rAF and no CSS motion; semantics are unchanged. OS `prefers-reduced-motion` changes apply at runtime.
- Measured fixture rendering cost and accessibility evidence: [m011-performance-accessibility-report.md](./m011-performance-accessibility-report.md). Native CPU/RAM remain UNMEASURED and real-device audio BLOCKED after S09 ([phase-4-windows-acceptance.md](./phase-4-windows-acceptance.md)); final status in [phase-4-report.md](./phase-4-report.md).

## Evidence status

Windows real-device evidence is recorded in [../testing/s05-playback-evidence.json](../testing/s05-playback-evidence.json). On the
recording host no Windows Python had `sounddevice`/`numpy`, so the real-device run, audible confirmation, the two-clap welcome path
and G435 microphone level are **BLOCKED** (not fabricated). Automated Python/TS tests cover the logic with fakes.

## Phase 4 closure

S09 attempted Windows acceptance: microphone envelope, playback envelope, cancellation, two-clap entry and native CPU/RAM are BLOCKED/UNMEASURED ([phase-4-windows-acceptance.md](./phase-4-windows-acceptance.md)). Architecture: [visual-core-architecture.md](./visual-core-architecture.md).
