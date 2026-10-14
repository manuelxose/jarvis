# Desktop Command Center (M010 / S04)

The existing Command Center runs inside the native Electron shell with an ordinary framed, resizable window, a labeled return-to-compact-overlay control, and enter/exit fullscreen (button, command palette, Escape). Core data shown is still the Phase 2 prototype fixtures: there is **no** production Core connection, audio, global shortcut or window-persistence claim here.

## Evidence classes

- Contract: `npm --prefix desktop test` (Electron fakes, event-order races, hostile/subframe mode requests).
- Synthetic CSS layout (not native/DPI proof): `npm --prefix ui run test:desktop-command-center` (1080p/1440p/4K CSS viewports, 960x600 minimum).
- Observed on real Windows hardware: `node desktop/scripts/native-command-center.cjs` (this document's matrix).
- Human-only visual judgement is not claimed.

## Reproduce (bounded, ~30 s)

```
npm --prefix ui run build
npm --prefix desktop test
node --test desktop/scripts/native-command-center.test.cjs desktop/scripts/native-overlay.test.cjs desktop/scripts/native-smoke.test.cjs
node desktop/scripts/native-smoke.cjs
node desktop/scripts/native-command-center.cjs
```

Prerequisites: WSL with `powershell.exe`, Windows Python, Electron runtime cached on local NTFS (`%LOCALAPPDATA%\jarvis\electron-runtime`), interactive desktop session, and registry `HKCU\Control Panel\Desktop\ForegroundLockTimeout=0` (observed value on the host). The probe forces `--force-device-scale-factor=1`, records UTC process start ticks, and only terminates processes whose PID, start ticks and image path match what it launched. `--accept-gaps` is opt-in and only for third-party limits; it was not used. Unobservable scenarios are BLOCKED, never PASS.

## Executed run (host: Windows NT 10.0.26200, PowerShell 5.1; displays all scale factor 1)

Run directory `desktop-native-command-center-GqP8oj/` (git-ignored via `desktop-native-command-center-*/`; screenshots `screenshot-*.bmp`). Overall status PASS, Electron exit 0, no surviving owned processes (4 owned, 0 remaining).

| Scenario | Result | Evidence |
|---|---|---|
| windowed | PASS | native HWND/process identity, resize geometry, mounted UI, screenshot |
| maximize-restore | PASS | maximize then restore returns to the prior windowed bounds |
| fullscreen-enter-exit | PASS | 3840x2160 fullscreen; button exit and Escape exit return to 1100x760 windowed |
| return-to-overlay | PASS | compact overlay visible and usable; Command Center not intercepting input |
| repeated-roundtrips | PASS | three roundtrips, singleton windows reused |

Note: the framed client area includes a 26 px strip above Electron web content; the harness maps coordinates through the measured content origin.

## Recovery / cleanup

The harness cleans up its own processes on every exit path and reports `recovery.remaining`. If a run is killed, delete stray `desktop-native-command-center-*` directories and end only `electron.exe` under `%LOCALAPPDATA%\jarvis\electron-runtime` that you started. Never stop user applications or Core.

## Handoff

- Window modes, display-aware bounds restore and display-change recovery: see [Window state machine](architecture/window-state-machine.md).
- S06: real Core connectivity/telemetry; current Command Center data is prototype fixtures.
