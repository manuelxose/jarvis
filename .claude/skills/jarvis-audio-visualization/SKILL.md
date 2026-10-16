---
name: jarvis-audio-visualization
description: Guardrails for audio-reactive visuals in Jarvis (visual core, overlay, command center). Use when touching playback/microphone telemetry, the visual-core state machine, envelope smoothing, cancellation/freshness handling, reduced-motion fallbacks or fixtures that fake audio.
---

# Jarvis audio visualization

Specs: `docs/design/reactive-core-spec.md` (+ JSON twin), `docs/engineering/visual-core-contract.md`, `docs/engineering/audio-visualization.md`, `docs/engineering/phase-4-runtime-audit.md`. This skill adds guardrails only; it adds no runtime capability, dependency or API.

## Measured output, not intent
- SPEAKING means the audio device accepted a slice (`StreamRenderer._blocking` after `stream.write`), never that TTS generated chunks or the queue reports `played`. A silent or fake renderer never yields SPEAKING.
- Do not claim audibility: `latency_ms` is the PortAudio-reported device latency; Bluetooth and drivers add more.

## Python owns capture
- One microphone stream, in Python (`voice_loop`). The renderer, Electron and the browser never open a mic or read raw audio; they only receive normalized `core.signal` / `core.snapshot` levels (0..1).

## Bounded and fresh
- Clamp levels to 0..1 and reject non-finite input. Throttle: output <= ~25 Hz, microphone <= 20 Hz. Smoothing uses bounded attack/release; no unbounded history.
- Producers send `age_ms` only; never subtract timestamps across Python and renderer clocks. Stale levels expire to zero.

## Monotonic identity, cancellation, sessions
- Order by `seq` within a session; reject stale, duplicate and out-of-order events. Cancel, stop, playback end and disconnect clear levels synchronously.
- Fence cancelled turn/playback/command/candidate ids so late amplitude cannot resurrect them. A snapshot installs state; a reconnect never replays startup. A restarted Core announces a new session with `replaces_session: null`, accepted only while rejoining after a (re)connect.

## Fallbacks
- Reduced or disabled motion keeps identical semantics, label and glyph (never colour alone) with no rAF loop. No telemetry means static or DISCONNECTED, never simulated motion. Frame pacing uses timers, not free-running rAF (the shell has no GPU vsync).

## Startup
- Python keeps ownership of two-clap activation, configured music, welcome speech and apps; the UI only reflects real lifecycle events and partial outcomes stay explicit.

## Fixtures
- Synthetic audio exists only in test fixtures (`visual-core-fixtures-main.tsx`, `fixtureSource.ts`) and must be labelled as fixtures; it is never evidence of real playback, CPU or RAM.
