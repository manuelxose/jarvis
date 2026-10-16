# Startup cinematic (Phase 4 summary)

The detailed lifecycle-to-signal table, timing ownership and fixtures are in [cinematic-startup.md](cinematic-startup.md) (kept in place so existing links keep working). This page is the Phase 4 closure view.

## What exists

- Two-clap lifecycle is published by `src/jarvis/apps/daemon.py` through `CoreSignals` (`candidate`, `startup`, `apps`, `stop`) and shown in the shared core as STARTING with static per-phase ticks. No modal, no takeover, no UI timers.
- Timing and music (`welcome_delay_seconds`, ducking, fades, cloned welcome) remain owned by `src/jarvis/application/startup.py` and config. The UI only reflects received phases.
- Reconnect restores the phase from the connect snapshot and never replays startup (`ui/src/testing/live-core.spec.ts`, test-server).
- Variants: full, reduced, muted, interrupted; fixture screenshots: [full](phase-4-evidence/startup-full.png), [reduced](phase-4-evidence/startup-reduced.png), [interrupted](phase-4-evidence/startup-interrupted.png).

## What is not proven

Real two-clap activation, audible welcome/music, and real app launches are BLOCKED ([phase-4-windows-acceptance.md](phase-4-windows-acceptance.md)): no welcome asset is configured, Windows Python lacks audio dependencies, and launching user apps was deliberately not attempted. User-configured startup behavior was not altered by Phase 4 code (timing, music, allowlists unchanged), but this is a source-level statement, not a runtime observation.
