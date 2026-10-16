# Cinematic startup integration (M011/S07)

The two-clap startup lifecycle reaches the shared VisualCore (overlay and Command Center) through the same
`CoreSignals` -> SSE -> `core-signal-normalizer.ts` -> `createVisualCoreService` path as S05 telemetry. See
[audio-visualization.md](./audio-visualization.md) for the wire layer and [visual-core-contract.md](./visual-core-contract.md) for the state machine.
There is no startup takeover or modal: the core simply shows STARTING in its existing slot.

## Lifecycle -> signal mapping

| Daemon hook (`src/jarvis/apps/daemon.py`) | CoreSignals call | Wire kind | Visual state / text |
|---|---|---|---|
| first clap accepted | `candidate(id, True, window_ms)` | `candidate` | STARTING, "Confirming activation", 1 tick lit |
| candidate cancelled | `candidate(id, False, 0)` | `candidate` | back to prior state |
| activation confirmed | `begin_activation()` | new generation + `core.snapshot` | clean startup/apps/stop fences, UI re-establishes |
| `_on_startup_phase(acknowledged)` | `startup("acknowledged")` | `startup` | STARTING, "Activating", 2 ticks |
| `initializing` | `startup("initializing")` | `startup` | STARTING, "Preparing services and music", 3 ticks |
| `announcing` | `startup("announcing")` | `startup` | STARTING, "Welcome", 4 ticks, level rings follow measured output |
| `ready` | `startup("ready")` | `startup` | Idle state labelled "Ready" |
| `degraded` / issues present | `startup("degraded", partial=True)` | `startup` | "Ready with limitations" |
| `failed` | `startup("failed")` | `startup` | ERROR |
| workspace app outcomes (`_publish_apps`) | `apps([(id, outcome)])` | `core.snapshot` | "Apps: n ready, m failed" or "Apps: status unavailable" |
| owner interrupt / shutdown | `startup("cancelled")` / `stop()` | `startup` / `stop` | STOPPING |
| cleanup finished | `cleaned_up()` | `core.snapshot` | Idle |

App outcomes map `launched`/`opened`/`already_running` -> ready, `failed`/`timeout` -> failed, `pending` -> pending (`_APP_STATUS`).
The apps line is shown whenever the startup phase is not `idle`; a null list reads "status unavailable", never an assumed success.
Ticks are static per-phase glyph geometry (`PHASE_TICK_PATHS`); there are no timers in the renderer.

## Timing ownership

`welcome_delay_seconds`, `duck_seconds`, `fade_in_seconds`, `fade_out_seconds` and the prerecorded cloned welcome are owned by
`src/jarvis/application/startup.py` and config. The UI adds no delay and no timers; it only reflects the phase events it receives. Music,
the app allowlists and the clap detector are unchanged.

## Variants

- **Full**: phase ticks fade in (320 ms); STARTING level rings follow measured output.
- **Reduced** (OS or setting): static ticks, no transitions.
- **Fast**: fixture-only `data-motion="fast"` ancestor clamps decorative transitions to 120 ms. It is not a MotionMode and has no setting (S08).
- **Muted**: announcing with output 0; ticks and text identical, no level rings.
- **Interrupted**: `startup("cancelled")` / `stop()` shows STOPPING until `cleaned_up()`.

Fixtures: `/visual-core-fixtures.html?scenario=startup-full|startup-reduced|startup-fast|startup-muted|startup-interrupted[&variant=compact-overlay]`
(`fixtureSource.ts:startupSource`, test-only).

## Reconnect and no replay

A reloaded frontend receives the D058/D059 connect snapshot (`facts.startup`, `facts.apps`, `fences`) and restores the current phase.
Nothing re-triggers startup: `live-core.spec.ts` asserts the fixture server's `triggers` counter (incremented only by step routes) is
unchanged across `page.reload()`.

## Evidence limits

Evidence is automated only: Python unit tests for the producer/daemon hooks, normalizer/machine tests, Playwright fixture screenshots + axe,
and live Playwright against the real Python `CoreSignals` via `ui/src/testing/fixtures/core_signal_server.py`.
Real Windows two-clap activation, audible music/welcome and real app launches are **BLOCKED/deferred to S09** and are not claimed here.
