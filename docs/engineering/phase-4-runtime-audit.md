# Phase 4 runtime and audio event audit

Date: 2026-10-14. Unit: M011/S01/T01. Scope: source-backed audit and existing contract tests; **no hardware, audible playback, latency or native visual acceptance claims**. No runtime code changed.

## Evidence and retrieval

- Authoritative plan: `.wf/phases/11-phase-4-reactive-visual-core-and-synchro/11-01-PLAN.md` (local `.planning/STATE.md` absent).
- Phase 1: `docs/engineering/frontend-skills.md`, `jarvis-ui-phase-1-audit.md`, `docs/architecture/frontend-architecture.md`; Phase 2: `docs/design/phase-2-review.md`, `jarvis-design-system.md`; Phase 3: `docs/phase-3-report.md`, `docs/desktop-core-bridge.md`.
- Context Mode execution lane: scoped `wf_exec` retrieval of `.wf/KNOWLEDGE.md`, especially MEM183, MEM044, MEM047, MEM169 and MEM189. Historical memory is not current-source authority: MEM047's unmapped PortAudio failures and MEM169's unused `Mixer.prime()` are contradicted by live code and tests. No external Context Mode search service was used.
- Graphify availability checked locally: no `graphify-out/graph.json`, no wiki index, no `graphify` executable. Graph retrieval unavailable; no other checkout accessed and no graph refresh needed for this documentation-only audit.
- The two historical evidence names in the plan are absent in this worktree. Fresh evidence below replaces those references, without manufacturing old IDs.
- Bounded independent scouts: `.wf/exec/2738ccef-41bc-43c9-ace7-51d9bacf1d26.stdout` (frontend/bridge), `.wf/exec/416a41c1-aaba-4005-b991-3571c120a795.stdout` (audio/startup), `.wf/exec/b45a62bf-792a-420f-8cb9-af23eac4994c.stdout` (exact lifecycle windows). Phase constraints and retrieval limits: `.wf/exec/25d3805d-c28c-4c34-b5cb-6065cd36e0f0.stdout`.

## Renderer and transport ownership

- `ui/src/main.tsx` -> `app/App.tsx` -> `features/command-center/CommandCenter.tsx` -> `app/useJarvisEvents.ts`. Existing central visual core: `components/jarvis/orb/JarvisOrb.tsx`, raw WebGL2 full-screen triangle/fragment shader, not Three.js or an audio visualizer. `orbModel.ts` is pure event folding and smoothing.
- `JarvisOrb` stops RAF for hidden documents/reduced motion; reduced motion draws on signals. Context/shader failure falls back to CSS. Preserve these paths, status labels, keyboard focus and non-colour-only states.
- Command Center hook chooses `services/desktop-transport.ts` when `window.jarvisDesktop.core` exists; otherwise `services/sse-transport.ts` using fragment-token discovery. Browser events are push-based, but **the hook polls connection state every 500 ms**; historical claims of completely polling-free UI are too broad.
- Electron `desktop/src/core-bridge.js` attaches to Core; never launches or owns Core. Token stays in main process. Validated IPC via `desktop/src/preload.js`, then typed frontend transport. Control surface remains only `activate|sleep`; no microphone/OS access belongs in renderer.
- `ui/src/features/overlay/Overlay.tsx` remains a prototype: static `OverlayCard` modes, `VisualCorePlaceholder`, mocked conversation, and native window-mode subscriptions. It does not subscribe to Core events or use the WebGL orb. Native compact/expanded window mode is not assistant state.
- Event additions must update Python `observability/event_hub.py::SCHEMAS`, `ui/src/protocol/events.ts`, **and** `desktop/src/ui-protocol.js` plus tests/transport allowlists. Existing `speech.*` and `startup.*` carry orchestration state, not PCM/RMS/device status.

## Device-write lifecycle: what is actually authoritative

1. Real `application/runtime.py` builds `AudioOutputQueue(render=make_sounddevice_render(device=...), render_timeout_seconds=60.0)`. Fake runtime has recording render; the queue's default renderer is silent.
2. `adapters/audio/output.py::AudioOutputQueue.play` receives complete WAV chunks, tags each with `TurnContext.trace_id`, checks cancellation, queues FIFO with backpressure, then waits for this turn's queued/current work to finish. A single worker serializes normal render awaits within that queue; executor work surviving a timeout is an exception to physical serialization.
3. `StreamRenderer.__call__` dispatches `_blocking` to an executor. `_blocking` checks remembered aborts, lazily imports sounddevice/numpy, decodes **embedded WAV rate/channels/width** (`decode_wav`, not microphone config), rejects non-16-bit samples, opens/reuses an int16 output stream for the same rate/channel pair, and calls `stream.write` in ~40 ms slices. That slice loop is the available speech device-write seam.
4. `flush(turn_id)` drops queued audio and invokes renderer `abort(turn_id)`; current-turn abort sets a thread event and calls `stream.abort()` to discard buffered output. Stream restart/reopen is format-dependent; abort history is bounded to 64 turn IDs.
5. Device import/open/write failures map to `ProviderUnavailable`. Queue catches per-chunk errors/timeouts and continues. **`played` increments even for failed or timed-out render attempts**; it cannot prove audibility or successful output. End of write is also not a measured final-DAC-drain timestamp. The queue exposes counters but emits no device lifecycle events.
6. Timeout cancels the await, not necessarily the executor's blocking write thread; no explicit renderer abort occurs in the queue timeout handler. Treat timeout/device completion as a distinct failure surface when adding lifecycle signals.
7. Startup music/chime use a **separate** `adapters/audio/mixer.py::Mixer` float32 callback output stream, typically 44100 Hz stereo. `Mixer.level` is last callback RMS of its mix, not TTS or microphone energy, and is not transported to UI. Browser-opened fallback music bypasses this mixer entirely.

## Speech event coverage gaps

`application/turn_manager.py` publishes speech start for cached acknowledgements before `audio.play`, for `_speak_and_cache` on first synthesized chunk, and in `_stream_model` on first TTS chunk. These all precede actual device-write success. `_stream_model` publishes completion in `finally`; cached acknowledgement and `_speak_and_cache` completion are not equivalently finally-protected.

`_speak_text` itself has no `speech.started/completed` publications. Daemon live welcome calls `turn_manager.speak_text`; cached welcome and fallback warning call `audio_output.play` directly. Consequently current orb speaking state does **not** uniformly cover live/cached/fallback welcomes or every spoken reply. PCM levels, playback IDs, device failures, chunk/sample positions and replay/snapshot state are absent from the visual contract. `orbModel` uses a single boolean, not trace correlation; stale completion/connection-loss handling must be addressed if playback events can overlap or be dropped.

## Activation, welcome timing and microphone ownership

- `apps/daemon.py::Sentinel`: first clap enters `candidate`, publishes `activation.started` and speculatively warms the shared voice; it does not chime/start music/welcome. Default second clap confirms activation; hotkey/wake/control can also activate. Config can require three claps, so do not hard-code two into UI truth.
- `_listen` owns the sentinel callback `sd.InputStream` (`daemon.input_device` override or `audio.input_device`); it closes it and suspends the detector before `_session`. Mic open failure logs and leaves hotkey/control usable. Callback failures are contained.
- `run` starts `_prime_output` before each listening period. `_session` awaits the one in-flight prime before `activation.confirmed`; prime failures log unavailable and allow silent activation. Neither silent priming nor its wait is evidence of the first audible sample.
- `_session` builds `StartupSequence`: chime -> concurrent services/workspace -> local music or browser fallback -> health-based welcome -> runtime voice loop. Shared model acquisition is gated by startup phase; media/runtime are prepared while idle where possible. Duplicate/queued activations are not new concurrent sessions.
- `application/startup.py::StartupOptions.welcome_delay_seconds` sets the **cached welcome** target to **2 seconds after `music_started`**, not after first clap or frontend startup. Live/fallback welcomes do not use this delay setting. Healthy cached welcome waits until services/health are known and starts ducking early by `duck_seconds`; slow services can make it later than the configured mark. Live/degraded welcome waits for voice readiness up to its deadline, with fallback voice when unavailable. Preserve greeting/name/health truth and cache identity.
- `first_sound`, `music_started`, `welcome_spoken` and `interactive` are orchestration `perf_counter` marks. `first_sound` is marked even when no mixer/successful chime exists; `welcome_spoken` is marked after `_announce` even when speech failed/timed out. They are **not** device/audibility measurements. Daemon adds gesture-to-sequence offset in `timings_ms_since_gesture` and publishes healthy `startup.completed` only with no report issues.
- Sentinel microphone is closed during startup welcome. `VoiceLoop.run` then owns `MicCapture.capture`, which uses `sd.InputStream` in `adapters/audio/input.py` with 100 ms reads. Voice loop also owns VAD, STT, wake-window, barge-in and echo-tail handling; finalization closes capture. First accepted utterance can fade out remaining startup music. Frontend must not open another microphone to infer this state.

## Downstream integration contract

| Boundary | Verified reuse | Gap to address in S02-S05 |
|---|---|---|
| Audio -> EventHub | Persistent speech stream/write/abort seam; independent mixer callback | Successful device writes, cancellation, terminal failure, bounded energy telemetry; no raw audio/private transcript |
| Startup -> events | Existing phase callback, report and truthful welcome selection | Device-confirmed welcome start/end covering cache/live/fallback without changing sequence or timing |
| EventHub -> desktop -> UI | Authenticated SSE/IPC, three schema mirrors, connection states | Correlation, additive validation, dropped-event/reconnect recovery; thread-safe bounded publication |
| UI -> visual core | Pure orb model and low-power WebGL2 fallback/reduced-motion behavior | Actual playback energy/state rather than synthesized-chunk timing; live overlay wiring |
| Input -> visual state | Existing Core mic handoff, VAD/barge-in ownership | Report existing capture state without adding competing microphone consumers |

These are expected Phase 4 work, not a plan-invalidating blocker. Do not replace the existing renderer, introduce a second transport, or add an audio library solely for visualization.

## Failure Modes

- Audio drivers/imports/unplug: `StreamRenderer` maps PortAudio import/open/write errors to `ProviderUnavailable`; queue records `render_errors`/`render_timeouts` and continues. Executor thread timeout/physical drain and generic swallowed failures remain limits, not successful-playback proof.
- Filesystem/media/cache: mixer missing/undecodable track errors produce missing/error report, optionally browser URL fallback; startup keeps welcoming. Cue failures use synthesized cue then silent continuation. Cache persistence failures are optional optimizations; report/event JSONL filesystem issues are diagnostics, not UI availability guarantees.
- Services/TTS/model: service and voice readiness deadlines default 20 s each, whole announcement 30 s; degraded/failed report and fallback preserve health truth. Workspace failures captured without blocking welcome. Music file decode/browser launch await has no separate sequence timeout; initial output prime also has no explicit deadline.
- Mic/wake: unavailable sentinel input leaves hotkey/socket active, wake detector exceptions disable wake only; voice-loop STT provider failures skip utterance instead of dispatching a tool. Cancellation closes capture/flushes turn output.
- Network/IPC: loopback token/version/schema rejection, connection loss and bounded reconnect; desktop backoff 500 ms to 10 s. Observer exceptions are isolated by hub/transport. Browser hook resets speaking on disconnected state; no replay means lost events are not restored as authoritative state.
- Audit tooling: Graphify unavailable locally; current UI hardware test cannot reach its own guard because top-level `@playwright/test` import is missing. No dependency/secret setup or external process launch needed for this audit.

## Load Profile

Expected runtime: one sentinel/session, one ordered speech consumer, a few desktop/browser subscribers. At 10x synthesis production the output device's real-time rate saturates first; queue caps pending chunks at 32 and applies 1 ms cooperative producer backpressure. Chunk sizes are not byte-capped, so 32 arbitrary WAV blobs can still consume substantial memory. Activation queue is unbounded but drained between sessions; input floods are not explicitly rate-limited. Mixer decodes a track into memory and invokes callbacks under its lock.

SSE limits 64 clients, 8192-byte requests and 128-byte control bodies; each subscriber has a bounded queue. EventHub invokes subscribers synchronously, including optional JSONL sink, so publishing every audio slice/sample would put observer/IO cost on the audio path. Use a bounded, coalesced off-audio-thread level update seam for later work; do not claim a measured safe telemetry rate. Preserve RAF pause/reduced motion and avoid React state updates for every PCM sample. No 10x hardware performance test was run.

## Negative Tests

Existing tracked tests reused (no new runtime behavior or test fixtures needed for an audit):

- `tests/test_audio_renderer.py`: missing sounddevice, PortAudio import OSError, device-write failure mapping, queue containment, aborted/dequeued chunks, mid-chunk barge-in, stream reuse and WAV metadata.
- `tests/test_audio_queue.py`: non-positive limits, renderer failure/timeout not wedging worker, cancellation during production/drain, flush isolation, waits for active chunk and fast-producer backpressure.
- `tests/test_startup.py`: missing/undecodable music, non-operational unhealthy welcome, service/voice/speech deadlines, duplicate triggers, concurrent workspace, cancel at service completion, cached-welcome timing, ducking and restoration.
- `tests/test_daemon.py`: first-clap expiry, second/default vs third/configured clap, activation during prime, prime failure/cleanup, suspended sentinel mic, duplicate activation, sleep during welcome, failed session recovery and unavailable output.
- `tests/test_core_handshake.py`, `tests/test_ui_protocol_parity.py`, `desktop/src/core-bridge.test.js`: protocol/token/loopback/allowlist rejection and bridge lifecycle.
- `ui/node-tests/{command-client,desktop-transport,sse-transport,endpoint,orb-model,overlay-interop}.test.ts`: unauthorized/malformed controls, bad token/version/non-JSON events, throwing listeners, unknown states, NaN/negative/huge dt, unsubscribes, hidden/inert/stale overlay ownership.
- `ui/node-tests/hardware-probe.test.ts` remains unverified here: missing Playwright import produces exit 1, not expected guarded exit 2. This is an environment prerequisite failure, not an audit-created regression.

## Verification Evidence

| Command | Result | Duration | Durable evidence |
|---|---|---|---|
| `npm --prefix desktop test` | 233 passed, exit 0 | 3338 ms | `.wf/exec/0531a457-7b9c-4777-9aba-9620f0488eed.meta.json` |
| `.venv/bin/python -m unittest tests.test_audio_renderer tests.test_audio_queue tests.test_startup tests.test_daemon tests.test_core_handshake tests.test_ui_protocol_parity` | 112 passed, exit 0 | 23482 ms | `.wf/exec/fe5a37f8-054e-463c-ba90-e073fb87aa1f.meta.json` and stderr |
| `npm --prefix ui run test:transport` | 35 passed, 1 dependency-bound failure, exit 1 | 3997 ms | `.wf/exec/c5ae5f5b-979f-46b7-8d13-c9f6172b0b6a.meta.json` |
| `node --experimental-eventsource --test ui/node-tests/command-client.test.ts ui/node-tests/desktop-transport.test.ts ui/node-tests/endpoint.test.ts ui/node-tests/orb-model.test.ts ui/node-tests/overlay-interop.test.ts ui/node-tests/sse-transport.test.ts` | 35 passed, exit 0 | 3022 ms | `.wf/exec/b3080109-8403-4393-ab5b-4819490d21af.meta.json` |
| Scoped dependency reproduction (Python subprocess) | Confirmed `ERR_MODULE_NOT_FOUND` for `@playwright/test` before any hardware launch, exit 0 | 224 ms | `.wf/exec/5eecdb4a-7406-4bae-9a7a-919bfd659fe2.meta.json` |

## T02 lifecycle verification follow-up

The later gate `.wf/exec/01f510ab-0c64-4fab-91e7-db545529692e.stderr` ran 112 tests in 26.838 s and failed only the lifecycle test's return-to-sentinel assertion. T01's `.wf/exec/fe5a37f8-054e-463c-ba90-e073fb87aa1f.meta.json` instead records exit 0 (23482 ms). Fresh pre-change isolated and scoped runs also passed (evidence `b982b58f-ba58-453b-811b-a0f842d88c34` and `2fd9d9e9-d353-4b92-8e9c-b4bdc2806a7f`), demonstrating intermittent behavior rather than a consistently broken lifecycle.

Source diagnosis: `_session` stops the runtime, then awaits the gated voice-acquire task and releases its holder before returning to `_listen`, which sets `sentinel` and reopens the Core microphone. Even with `FakeRuntime`, `VoiceModelManager.acquire` performs real off-loop process/VRAM probes. The old test allowed only 100 ten-millisecond polls for this cleanup. A controlled acquire delay reproduced `active` after runtime stop for 1.1 s, then restored `sentinel` and the microphone as soon as acquisition was released (`.wf/exec/82da3029-a8a5-40e0-92ec-92a932e4fe30.stdout`). This establishes a test isolation/timing failure mechanism consistent with the gate; the historical trace alone cannot identify exactly which host probe was slow. No production deadlock was reproduced, and runtime stop is not a complete session-cleanup signal.

Smallest fix: `DaemonTests` retains the real voice manager with injected deterministic process/VRAM callbacks and a no-clone factory, uses bounded condition waits instead of fixed sleeps, and guarantees daemon shutdown on assertions. A new event-gated acquire regression checks that the detector stays suspended and the mic is not reopened prematurely, then that holders/runtime are cleared and listening returns after acquisition. No production code, startup assets, welcome timings or Python microphone ownership changed. Test fixtures are inline, not sourced from ignored audit files.

| Command | Exit code | Verdict | Duration | Durable evidence (`.wf/exec/`) |
|---|---|---|---|---|
| `.venv/bin/python -m unittest tests.test_audio_renderer tests.test_audio_queue tests.test_startup tests.test_daemon tests.test_core_handshake tests.test_ui_protocol_parity` | 0 | 113 passed | 19819 ms | `0ebd84c2-5697-45be-b518-37b490ff929b.meta.json` and stderr |
| `npm --prefix desktop test` | 0 | 233 passed | 5670 ms | `2210ed74-a561-49ce-84d3-231af30c4261.meta.json` and stdout |
| `node --experimental-eventsource --test ui/node-tests/command-client.test.ts ui/node-tests/desktop-transport.test.ts ui/node-tests/endpoint.test.ts ui/node-tests/orb-model.test.ts ui/node-tests/overlay-interop.test.ts ui/node-tests/sse-transport.test.ts` | 0 | 35 passed | 5964 ms | `88cc418d-4c67-4543-b164-f5f36b0823d8.meta.json` and stdout |
| Inline unittest runner: 30 repetitions each of normal lifecycle and delayed acquisition | 0 | 60 passed | 5839 ms | `142502cb-a7e8-48a2-8487-d722110d5367.meta.json` and stderr |

Graphify refresh was attempted with `graphify update .`; exit 127, command unavailable (`.wf/exec/e0d82f2e-7431-4baa-b716-f1a88b8bb343.stderr`). No graph or wiki exists in this worktree; no other checkout was accessed.

## Operational Readiness

This audit-only slice adds no runtime monitoring, alerts or operational endpoints. Existing queue counters, output-prime logs, startup reports and optional JSONL remain the diagnostics, not proof of audible playback. Device-confirmed playback/startup telemetry, correlated terminal events and reconnect recovery remain downstream work. Session cleanup still waits on pending voice acquisition; bounded cleanup under slow host probes is not a new runtime guarantee. Contract checks above do not establish audible playback or native Windows hardware acceptance, and no hardware-dependent items are promoted.

## Diagnostics and availability limits

Inspect scoped stdout/meta/stderr artifacts above, `AudioOutputQueue.state()` counters, daemon output-prime logs, phase logs, startup report `timings_ms_since_gesture`, and optional hub JSONL events. Do not treat queue `played` or orchestration marks as audible-output proof.

PowerShell bridge executable is available, but this task did not exercise Windows audio/GUI or start/stop any Windows process. Phase 3 docs disagree in age: bridge-specific document records a blocked Windows venv run; later Phase 3 final ledger claims attach/restart passes. Treat final ledger as historical source context only, not fresh evidence for this audit. Its owner-accepted visual-parity failures, physical-monitor-unplug and some click-through gaps remain unpromoted. UI/desktop `node_modules` are absent here; build/lint/browser/native acceptance was not rerun. Audit supports contract integration, not hardware acceptance.
