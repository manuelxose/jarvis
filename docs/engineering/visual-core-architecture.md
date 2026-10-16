# Visual core architecture (Phase 4)

Maps the shipped design to source. Pair with [visual-core-contract.md](visual-core-contract.md) (API and fences) and [audio-visualization.md](audio-visualization.md) (wire layer).

## Data flow

Python producers -> `/events` SSE (or desktop bridge) -> normalizer -> shared service -> renderer.

| Layer | Source | Role |
|---|---|---|
| Producers (Python-owned) | `src/jarvis/observability/core_signals.py`, `src/jarvis/adapters/audio/output.py`, `src/jarvis/application/voice_loop.py`, `src/jarvis/application/turn_manager.py`, `src/jarvis/apps/daemon.py` | Measured playback/mic facts, turn lifecycle, startup phases; seq allocated under the publish lock |
| Stream | `src/jarvis/observability/event_stream.py` | `core.signal` and `core.snapshot` (snapshot on connect, keepalive, overflow) |
| Transport | `ui/src/services/sse-transport.ts`, `ui/src/services/desktop-transport.ts`, `ui/src/services/visual-core-feed.ts` | Browser SSE and Electron bridge feed the same service |
| Normalizer | `ui/src/services/core-signal-normalizer.ts` | Validates wire shapes; unknown input is rejected, not guessed |
| State machine and store | `ui/src/components/jarvis/visual-core/visualCoreMachine.ts`, `ui/src/services/visual-core-service.ts` | Ten states, priority selection, session/generation/watermark fences, one pending deadline |
| Renderer | `ui/src/components/jarvis/visual-core/VisualCore.tsx` | Pulls levels imperatively (rAF); semantic changes via subscribe |

## Ten states

DISCONNECTED > STOPPING > ERROR > STARTING > SPEAKING > EXECUTING > THINKING > LISTENING > SUCCESS > IDLE (selection priority). SPEAKING needs confirmed device playback plus a fresh output level (<= 250 ms); otherwise THINKING with an "output unconfirmed" detail.

## Presentations

Overlay (`ui/src/features/overlay`) and Command Center (`ui/src/features/command-center`) each own a service instance and render the same `VisualCore`. Both are verified as fixtures; the native windows are not (BLOCKED in [phase-4-windows-acceptance.md](phase-4-windows-acceptance.md)).

## Motion

Disabled and reduced motion run no rAF and no CSS motion; label, detail and announcements are unchanged. One polite live region per core, coalesced at 500 ms ([m011-performance-accessibility-report.md](m011-performance-accessibility-report.md)).

## Audio, stop and cancel

Audio levels are measured frames only: output after `stream.write` accepts a 40 ms slice, microphone at most 20 Hz. No synthetic envelopes. Cancel, stop, playback end and disconnect clear signals synchronously; fenced ids cannot resurrect. Accepted-by-device is not audibility.

## Offline and error behavior

`connected` alone is "awaiting snapshot" (DISCONNECTED, zero signals); after 2000 ms the detail reads "State unavailable". Auth or incompatible transport states stay textual. A missing producer never yields success.

## Evidence

Fixture and test-server evidence: [phase-4-test-report.md](phase-4-test-report.md). Native evidence: none (BLOCKED).
