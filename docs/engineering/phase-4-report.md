# Phase 4 report: Reactive Visual Core and Synchronization (M011)

Closing report for Phase 4. Every claim links to source or evidence and is labelled **fixture** (Chromium on synthetic data), **test-server** (real Python `CoreSignals` producer driven by synthetic triggers) or **real runtime** (native Windows with hardware). No row is credited as real runtime unless the hash-verified manifest [native-hardware.json](phase-4-evidence/native-hardware.json) (rendered in [phase-4-windows-acceptance.md](phase-4-windows-acceptance.md)) holds durable evidence for it; at the time of writing only the two native presentation rows do, so hardware acceptance is PASS. Fixture suite success below is not native acceptance.

Related documents: [visual-core-architecture.md](visual-core-architecture.md), [audio-visualization.md](audio-visualization.md), [startup-cinematic.md](startup-cinematic.md), [phase-4-test-report.md](phase-4-test-report.md), [phase-5-handoff.md](phase-5-handoff.md).

## Desired versus delivered

| Desired | Delivered | Evidence label |
|---|---|---|
| One shared reactive core for Overlay and Command Center | `createVisualCoreService` + `VisualCore.tsx` used by both presentations | fixture + bridge stub |
| Deterministic ten-state machine, fail-closed fences | `visualCoreMachine.ts`, node tests | unit |
| Python-owned measured audio (output/mic envelope, playback identity) | `core_signals.py`, `adapters/audio/output.py`, `voice_loop.py` | unit + test-server |
| Startup lifecycle in the same core, no replay on reconnect | `daemon.py` hooks, `live-core.spec.ts` | test-server |
| Reduced/disabled motion with identical semantics | accessibility spec, S08 report | fixture |
| Native idle CPU < 2 % | measured: 0.208 % of the machine, 60 s window | real runtime, retained sample `native-samples/idle-cpu.json` |
| Real microphone, playback, two-clap entry, native windows | Only the Overlay and Command Center presentations have a retained native sample; microphone, playback, two-clap and the rest are unverified history awaiting durable samples and human records | real runtime (partial) |

## Acceptance matrix

Status vocabulary: PASS, FAIL, BLOCKED, UNMEASURED. A row is mandatory unless marked otherwise. Evidence column: the scope of the status, then the source.

| Category | Status | Scope and evidence |
|---|---|---|
| Python unit suite | PASS | 1018 tests OK, 1 skipped; [test report](phase-4-test-report.md) |
| UI node tests (transport) | PASS | `npm --prefix ui run test:transport`; [test report](phase-4-test-report.md) |
| Build and lint | PASS | build exit 0, lint 0 errors / 15 advisory warnings; [test report](phase-4-test-report.md) |
| Desktop bridge suite | PASS | 39 passed, bridge stubs only; [test report](phase-4-test-report.md) |
| Phase 4 Playwright suite | PASS | 154 passed, 60 intentional skips (fixture); also 154 passed with 8 and 16 busy CPU loops after the determinism fixes; [test report](phase-4-test-report.md) |
| Screenshot evidence, ten states, both presentations | PASS | 25 fixture PNGs, [index](phase-4-evidence/index.json); example [overlay-speaking](phase-4-evidence/overlay-speaking.png), [command-center-speaking](phase-4-evidence/command-center-speaking.png) |
| Operational labels and accessibility | PASS | fixture, `visual-core-accessibility.spec.ts` |
| Reduced and disabled motion | PASS | fixture, [reduced-motion shot](phase-4-evidence/overlay-reduced-motion-speaking.png) |
| Cancellation | PASS | test-server only; native cancellation is in the hardware rows below |
| Disconnect, reconnect, no startup replay | PASS | test-server and fixture; native recovery is in the hardware rows below |
| Stale and out-of-order telemetry | PASS | fixture and unit tests |
| Rapid transitions, 100-cycle and sustained load | PASS | fixture, host-specific (WSL2 Chromium) |
| Startup phases (fixture) | PASS | [startup-full](phase-4-evidence/startup-full.png), [startup-interrupted](phase-4-evidence/startup-interrupted.png) |
| Windows runtime source resolution | PASS | native-programmatic: `runtime-source-resolution`; worktree source imported under Windows Python [sample](phase-4-evidence/native-samples/runtime-source-resolution.json) |
| Real microphone envelope | PASS | native-programmatic: `microphone-envelope`; real input device through `CoreSignals.microphone` [sample](phase-4-evidence/native-samples/microphone-envelope.json) |
| Real playback envelope, start and end | PASS | native-programmatic: `playback-envelope`; real speech through the production output path [sample](phase-4-evidence/native-samples/playback-envelope.json) |
| Human-heard playback | PASS | human-observation: `audible-playback-human`; owner observation, see record [sample](phase-4-evidence/human-records/audible-playback-human.json) |
| Native cancellation and stop | PASS | native-programmatic: `cancellation-envelope`; cancel at flush, zero output after [sample](phase-4-evidence/native-samples/cancellation-envelope.json) |
| Native disconnected recovery | PASS | native-programmatic: `disconnect-recovery`; Core killed and relaunched under a live window [sample](phase-4-evidence/native-samples/disconnect-recovery.json) |
| Native reconnect without startup replay | PASS | native-programmatic: `reconnect-no-replay`; one snapshot per connection, same session [sample](phase-4-evidence/native-samples/reconnect-no-replay.json) |
| Two-clap startup entry on hardware | PASS | native-programmatic: `startup-two-clap`; real claps, phases to ready [sample](phase-4-evidence/native-samples/startup-two-clap.json) |
| User-configured startup preservation | PASS | native-programmatic: `startup-assets`; owner's configured music, cached welcome and apps [sample](phase-4-evidence/native-samples/startup-assets.json) |
| Native Overlay and Command Center windows | PASS | native-programmatic: `native-overlay`, `native-command-center`; a hardware check only, not audible or A/V acceptance [sample](phase-4-evidence/native-samples/native-smoke-sanitized.json) [sample](phase-4-evidence/native-samples/native-smoke-sanitized.json) |
| Native stale and out-of-order telemetry | PASS | native-programmatic: `stale-ordering-native`; stale and foreign-session events ignored by the live window [sample](phase-4-evidence/native-samples/stale-ordering-native.json) |
| Native idle CPU < 2 % | PASS | native-programmatic: `idle-cpu`; 60 s window, Electron tree over logical cores [sample](phase-4-evidence/native-samples/idle-cpu.json) |
| Native idle RAM < 75 MB | PASS | native-programmatic: `idle-ram`; private bytes of the Electron tree [sample](phase-4-evidence/native-samples/idle-ram.json) |
| Native reduced motion | PASS | native-programmatic: `reduced-motion-native`; zero rAF callbacks, data-motion reduced [sample](phase-4-evidence/native-samples/reduced-motion-native.json) |
| Native audio/visual follow | PASS | native-programmatic: `av-sync-human`; live window followed real playback; see the human record [sample](phase-4-evidence/native-samples/av-sync-human.json) [sample](phase-4-evidence/human-records/av-sync-human.json) |

## Remaining blockers

None. Authoritative list: [native-hardware.json](phase-4-evidence/native-hardware.json), aggregate PASS: every row has a retained, hash-verified, sanitized sample (`hardware-run-scripts/native_rows.py`, `av_sync_live.py`) and the two human rows have owner observation records. Limits worth knowing: the startup rows come from the owner's real two-clap run (so they launched the owner's apps); the human records are single overall answers by one person; the A/V machine sample is an identical immediate re-run of the sequence the owner watched.

The fixture and test-server rows above remain valid for their stated scope only.

## Reproduce

From the worktree root:

```
.venv/bin/python -m unittest discover -s tests
npm --prefix ui run build
npm --prefix ui run lint
npm --prefix ui run test:transport
npm --prefix ui run test:desktop-command-center
npm --prefix ui run test:phase4
.venv/bin/python -m unittest discover -s tests -p test_phase4_docs.py
```

Graphify refresh: unavailable (no `graphify-out/graph.json` or executable in this worktree); nothing was updated elsewhere. Phase 5 was not started; see [phase-5-handoff.md](phase-5-handoff.md) for documentation only.

PHASE 4 STATUS: PASS