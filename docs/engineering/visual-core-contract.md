# Visual core contract (S03)

Renderer-neutral machine plus store for the ten-state Adaptive Command core. Authored in
`ui/src/components/jarvis/visual-core/visualCoreMachine.ts` (pure) and
`ui/src/services/visual-core-service.ts` (store). Design oracle: `docs/design/reactive-core-spec.{md,json}`.

**Status:** not installed in any live window. No browser, audio or hardware acceptance is claimed;
evidence is in-process Node tests only (`ui/node-tests/visual-core-{machine,service}.test.ts`).

## API

```ts
const svc = createVisualCoreService({ clock, scheduler })   // both optional; defaults performance.now / setTimeout
svc.getSnapshot()      // frozen { state, label, detail, connection, awaitingSnapshot, reason, partial }
svc.subscribe(fn)      // semantic changes only; returns unsubscribe
svc.readSignals(now?)  // { output, peak, microphone } in 0..1, pulled imperatively (rAF); never notifies
svc.ingest(unknown)    // normalized event or snapshot; false if rejected/disposed
svc.setConnection(s)   // 'connected' | 'connecting' | 'disconnected' | 'auth-error' | 'incompatible'
svc.tick()             // advance to the injected clock (the single pending deadline calls this)
svc.getDiagnostics()   // { rejected, listenerErrors, pendingTimers, disposed } - counters only
svc.dispose()          // idempotent; clears the timer and listeners
```

`VisualCoreSource` (getSnapshot/subscribe/readSignals) is the structural contract a renderer needs.
The store is process-local: each window would own its own instance.

Design choice: a reducer-only interface pushes clocks/timers onto each renderer; a per-component hook ties
semantics to React and re-renders per sample. A React-free external store gives semantic subscriptions while
high-rate levels bypass React.

## Behaviour

- Selection by active facts: DISCONNECTED > STOPPING > ERROR > STARTING > SPEAKING > EXECUTING > THINKING > LISTENING > SUCCESS > IDLE.
- SPEAKING requires a confirmed matching device-output playback plus a finite output level at most 250 ms old
  (core + transit age, injected local clock). Generated speech alone yields THINKING with detail
  `Speech prepared; output unconfirmed`; levels are zero.
- `connected` alone is awaiting-snapshot (DISCONNECTED, zero signals). Only a validated atomic snapshot installs
  state. After 2000 ms the detail is `State unavailable`.
- Cancel, stop, playback end and disconnect clear signals synchronously, before listeners run.
- Fences (cancelled turn/playback/command/candidate, session stop) reject resurrection by new IDs. Retention is
  bounded (128 shared); overflow fails closed rather than evicting.
- Snapshot merge: same session needs `watermark` and `generation` not lower than current; replacement needs
  `replacesSession` equal to the current session; fences are unioned; equal-watermark snapshots cannot refresh
  levels or holds. Event sequence is compared only in the session watermark domain, never wall-clock `ts`.
- Rejections expose a bounded `reason` (invalid-input, stale-sequence, stale-generation/session, terminal-fence,
  awaiting-snapshot, snapshot-timeout, stale/unavailable-output, ...). Subscribers are notified only when a
  semantic field (including `reason`) changes, bounded by distinct values, not by sample count.
- One pending deadline covers freshness, snapshot timeout, liveness and hold expiry.

## Clock domains

Only local monotonic `now` / `receivedAt` are compared. Remote absolute clocks are never subtracted. Producers
supply `ageMs` (capture-to-send, plus transit for events). A snapshot adds its own outer `ageMs` to level ages.

## Minimal normalized inputs

Event envelope: `{ session, generation, seq, ageMs, kind, ... }`, e.g.

```json
{"session":"s1","generation":1,"seq":1,"ageMs":0,"kind":"turn-start","turn":"t1"}
{"session":"s1","generation":1,"seq":2,"ageMs":0,"kind":"playback-start","turn":"t1","playback":"p1","confirmed":true}
{"session":"s1","generation":1,"seq":3,"ageMs":5,"kind":"output","turn":"t1","playback":"p1","level":0.4,"peak":0.5}
```

Snapshot (all required): `kind:'snapshot', session, replacesSession, generation, watermark, ageMs, facts, fences,
output, microphone, candidate, successRemainingMs, errorRemainingMs`.

These are conceptual normalized inputs, not production wire events. Legacy v1 events (`speech.started`,
`voice.state` = speaking) lack session/generation/seq/ageMs and are rejected; the service never synthesizes them.

## Live integration points (not wired)

- `ui/src/app/useJarvisEvents.ts` - owns React-local state today.
- `ui/src/services/desktop-transport.ts` - bridge exposes connection status only; `getState` is connection-only.
- `ui/src/services/sse-transport.ts`
- `ui/src/components/jarvis/orb/JarvisOrb.tsx` - current consumer of the legacy eight-state model.

v1 speech events describe generated-audio orchestration, not audible output. No snapshot request endpoint exists.

## S04 renderer (2D SVG)

- `ui/src/components/jarvis/visual-core/visualCoreGeometry.ts` - pure, React-free tables derived from
  `docs/design/reactive-core-spec.json` (ten states x four variants), `arcPath`, `resolveMotionMode` and
  `frameFor({ state, variant, mode, t, level })`. Time and level are inputs; there is no clock inside.
- `VisualCore.tsx` props: `source?: VisualCoreSource`, `variant`, `motion?: 'full'|'reduced'|'disabled'`
  (default `full`), `className?`. State comes from `useSyncExternalStore(source.subscribe, source.getSnapshot)`.
  In full motion one `requestAnimationFrame` loop reads `source.readSignals(now)` and writes transforms through
  refs (no React state per frame); it stops when the document is hidden, on unmount or on a mode change and
  resumes from the current snapshot without catch-up.
- Root data attributes: `data-state`, `data-variant`, `data-motion` (`full|reduced|disabled`),
  `data-renderer` (`svg|static`). One `role="status" aria-live="polite"` label carries the state text.
- Motion precedence: user `disabled` > OS `prefers-reduced-motion` > user `reduced` > user `full`.
  Reduced/disabled draw a single static frame (t=0, level 0).
- Fallback: without a `source` the core renders static DISCONNECTED; a render error inside the SVG subtree is
  caught by an error boundary that renders the same static glyph and label (`data-renderer="static"`).
- Fixtures: `ui/visual-core-fixtures.html` (third Vite input, no transport/Core connection) renders all ten
  states in all four variants plus reduced/disabled rows. Query params `?state=&variant=&motion=&nosource=`
  render a single cell.
- Tests: `cd ui && npm run build && npx playwright test src/testing/visual-core.spec.ts`
  (baselines via `--update-snapshots`); `node --test node-tests/visual-core-geometry.test.ts`.

Evidence claimed: Chromium fixture screenshots on Linux, axe, no canvas/WebGL, data attributes. Not claimed:
live Electron window wiring (S06), Windows DPI rendering, audio-driven behaviour, telemetry (S05).

## Remaining work

- S06: done (see below).
- S07: Python startup ownership. The service never sends activation commands and never replays welcome on reconnect.

## S05 telemetry

Measured `core.signal`/`core.snapshot` telemetry (producer, wire shapes, identity rules, throttles, latency, Windows evidence status) is documented in
[audio-visualization.md](./audio-visualization.md); real-device evidence is in `docs/testing/s05-playback-evidence.json` (device items BLOCKED).

## S06 window integration

Wiring per window: Electron bridge (`core:get-replay` + live `core.*` events) or browser SSE ->
`useJarvisEvents` (one transport subscription) -> `ui/src/services/visual-core-feed.ts` (strict
`normalizeCoreEvent`, dedupe, replay resync) -> per-window `VisualCoreService` (exposed as `core`) -> `VisualCore`.
Overlay (`compact-overlay` / `expanded-overlay`) and Command Center Overview (`command-center`, replaces only the
AssistantStatus slot) render the same reusable core. The WebGL `JarvisOrb` is removed.

D059 replay (main process, `desktop/src/core-bridge.js`):
- Retains the latest `core.snapshot` plus contiguous `core.signal` (seq === last + 1, same session), bound `RETAIN_MAX = 512`.
- A seq gap, other session or overflow clears the retained set (overflow logs `core-replay-dropped`) until the next snapshot.
- Cleared whenever the stream is lost, closed, stopped or leaves `connected`.
- `getReplay()` returns fresh copies with `age_ms` (and nested snapshot output/microphone `age_ms`) increased by the elapsed injected-clock time; stored events are never mutated. Not connected -> `[]`.
- IPC `core:get-replay` (owner-gated, stranger -> `[]`) and preload `jarvisDesktop.core.getReplay()`.
- The feed buffers live events (bound 512) during resync and skips ones already covered by the replay (no false rejections).
  Replay failure degrades to waiting for the next live snapshot (SSE sends one on connect, every 15 s and on overflow).

Status mapping (`toMachineConnection`): connected->connected; connecting/reconnecting->connecting;
disconnected->disconnected; auth-failure->auth-error; incompatible-version->incompatible; other->disconnected.
Non-connected clears dedupe state and buffer.

Teardown: the hook disposes feed and service on unmount (StrictMode safe: a disposed service is never reused);
`getDiagnostics()` shows `pendingTimers 0`, `disposed true`.

Preserved: drag regions, `data-overlay-interactive`, pass-through, mode controls, captions, eight-workspace nav,
command palette. VisualCore's `role="status"` label is the single state announcer.
Legacy `speech.*` / `voice.state` never drive the core.

S07 consumes: `core` from `useJarvisEvents`, the feed/service diagnostics, and `core:get-replay`.
Live proof: `ui/src/testing/live-core.spec.ts` (real `CoreSignals` over SSE).

### Windows native evidence

**Status: BLOCKED (2026-10-15).** `powershell.exe` is available from WSL, but `npm --prefix desktop run visual:native`
(`native-visual.cjs`) reports `status: BLOCKED`, reason `missing-windows-electron: provision desktop lockfile win32 binary in
this worktree` (`desktop/node_modules` is absent in the M011 worktree; the main checkout has one, but this run did not
provision or borrow it). No native capture was taken, so there is no claim that compact overlay, expanded overlay or Overview
render the new core in a real Windows window, and `desktop/visual-baselines` (win32) were NOT touched. They show the old
orb/placeholder and must be refreshed from a real Windows capture once Electron is provisioned. No audible-playback claim.

Automated evidence that did pass: `npm --prefix desktop test`, `test:all-native`, `ui test:transport`, lint, build,
`test:desktop-command-center`, default Playwright (94 passed incl. visual-core and live-core), and Python `pytest -q tests`.
