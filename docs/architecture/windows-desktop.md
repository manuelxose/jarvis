# Windows desktop shell — architecture

Phase 3 (M010). This document is built up slice by slice as the native shell,
overlay, Command Center and mode manager land. This section covers the
environment ground truth gathered in S01 before any shell code was written.

## Current overlay handoff (S03/T03, 2026-10-13)

See [Desktop overlay acceptance and handoff](../desktop-overlay.md) for the
current bounded Windows commands, 25-scenario BLOCKED matrix, retained failed
runs, identity-safe cleanup, explicit hit-region/drag policy and S05 transient
mode contract. Automated suites and both-entry native lifecycle smoke pass;
foreground acquisition/recipient observation prevent routing, drag, topmost
and compositor acceptance. Zero screenshots were generated; no owned survivor
was recorded. Browser bridge stubs are not Windows proof. Window-mode and
window-state persistence: [window-state-machine.md](window-state-machine.md). The
attach-only Core bridge is described in [desktop-core-bridge.md](../desktop-core-bridge.md):
its states are contract-test proven, but the real Windows Core restart run is BLOCKED
(no Windows venv on this host), so a production Core attach is not yet accepted.

## Bootstrap handoff (S02/T03, 2026-10-02; lifecycle rerun in S03 above)

See [Desktop shell bootstrap handoff](../desktop-shell-bootstrap.md) for
clean lockfile installation, explicit Windows Electron provisioning, build,
launch and bounded smoke commands, trust boundaries, and current evidence.
`desktop/` remains the chosen Electron shell boundary; no `shell/` scaffold
or Rust installation was added. Historical results below are not substitutes
for this worktree's native acceptance.

**Current native lifecycle acceptance is PASS.** Build/lint and transport (26),
desktop (48), and harness (41) contract tests pass; both real Windows entries
show a native window (nonzero HWND), quit with code 0 and leave zero owned
survivors. The authorized recovery caches only the locked stock Electron
runtime on local NTFS, not application source; source, UI build and isolated
state remain in this worktree. Fresh evidence is
`(transcript not retained)`; missing-build and UNC launch-failure
checks also fail safely with no survivors. Visual/audio acceptance is still untested. The handoff records the
Electron dynamic-import entry-guard fix and native process-handle/zero-HWND
observation fixes found during this verification.

Phase 2 fixture data remains prototype UI, not a proven production Core
connection (S06). This does not accept audio, two-clap/welcome/TTS,
click-through, monitor/DPI, tray, fullscreen, pixel equivalence, or any
R001–R009 requirement; styling was not redesigned.

## Environment facts (reverified 2026-10-01, S01/T01)

These facts replace the 2026-09-24 inventory; production-shell observations
below remain historical evidence, not tests rerun by this environment task.
The session runs in WSL2 (`Linux 5.15.153.1-microsoft-standard-WSL2`).
`powershell.exe -NoProfile -Command "Write-Host OK"` returned `OK` (exit 0).
`cmd.exe /d /c ver` returned Windows `10.0.26200.9550` (exit 0), but warned
that it cannot retain the worktree's UNC current directory and defaults to
Windows. Do not use that fallback for project commands; no project files
were read or written there.

The **real Windows host desktop**, not WSLg, was exercised with
`Start-Process "$env:WINDIR\System32\winver.exe" -PassThru`.
`Get-Process` observed PID `29444`, session `1`, native path
`C:\WINDOWS\System32\winver.exe`, title `Acerca de Windows`, and nonzero
`MainWindowHandle=27855568`. A bounded 15 × 500 ms poll required a window;
`finally` stopped only this launched PID, and a second lookup verified its
absence (`NATIVE_WINDOW_PASS`, `CLEANUP_PASS`). This is automated native
window evidence, not a screenshot or a human-observed visual check.

| Fact | Value | Source |
|---|---|---|
| Windows version | Windows 11 Home, `10.0.26200`, build `26200`, 64-bit; `ver` reports revision `9550` | `Get-CimInstance Win32_OperatingSystem`; `cmd.exe /d /c ver` |
| GPU / display adapters | NVIDIA GeForce RTX 3070 Laptop GPU: 3840×2160 @ 60 Hz; Intel Iris Xe Graphics: 1920×1080 @ 60 Hz | `Get-CimInstance Win32_VideoController` |
| Monitor topology | **Dual adapters/resolutions verified; dual active displays and mixed DPI NOT confirmed.** WMI exposes one active `DISPLAY\SHP155D` monitor; `Win32_DesktopMonitor` returns one generic 3840×2160 entry and one entry with empty dimensions | `root/wmi:WmiMonitorID`, `WmiMonitorConnectionParams`, `Win32_DesktopMonitor`; adapter dimensions alone do not prove monitor layout, DPI, or extension mode |
| PowerShell language mode | `ConstrainedLanguage`; `Add-Type -AssemblyName System.Windows.Forms` rejected with `CannotDefineNewType` (exit 1, expected negative check) | `$ExecutionContext.SessionState.LanguageMode`; actual `Add-Type` probe |
| Node.js (Windows side) | `v24.18.0`, `C:\Program Files\nodejs\node.exe` | `where.exe node`; `node.exe --version` |
| npm (Windows side) | present, Node installation and user shims; version not checked | `where.exe npm` |
| cargo / rustc / rustup (Windows side) | **unavailable on PATH**, and no executable at the current user's default `.cargo\bin` location; arbitrary nonstandard installations not searched | independent `where.exe` checks (exit 1); `Test-Path` checks (False) |
| winget | present, `v1.29.380` | `where.exe winget`; `winget.exe --version` |
| dotnet runtimes | `Microsoft.NETCore.App` and `Microsoft.WindowsDesktop.App`: `6.0.36`, `8.0.31` | `dotnet.exe --list-runtimes` |
| dotnet SDK | **none reported**, empty list (exit 0) | `dotnet.exe --list-sdks` |

### Evidence and limits of this revalidation

Raw command output and individual exit codes/durations are retained under
execution transcripts that are not retained (not application runtime dependencies).

**Current re-run (2026-10-14, S10/T02; files present in this worktree).** Five
separate probes, each a bounded `powershell.exe -NoProfile -NonInteractive`
call (exit 0, 0.6-1.9 s):

- `018a9ddc-e23e-4121-9bdd-0ba60bf0c009.stdout`: PowerShell smoke (5.1.26100.9549, ConstrainedLanguage) and CMD `ver` (10.0.26200.9550).
- `129e9c2e-3eea-49e9-ad05-7e83d2dfdebb.stdout`: independent OS, video controllers, WMI monitor inventory and .NET runtimes/SDKs.
- `f0577cae-3da8-4979-b199-2a8343a0bc8e.stdout`: Rust/cargo/rustup locations, winget and Node versions.
- `36b48f27-8968-49ff-8b8f-2eddf3f1e840.stdout`: native window probe (`winver.exe`) with `NATIVE_WINDOW_PASS` and `CLEANUP_PASS` markers.
- `4b8f98e4-a073-41d5-8f38-3b6c833e71a9.stdout`: ConstrainedLanguage `Add-Type` negative check (`CannotDefineNewType`).

Measured differences from the 2026-10-01 facts above: WMI now reports **three**
active monitors (`SHP155D`, `GSM59C6`, `GSM7706`; scale/DPI not measured), four
video controllers (adds two DisplayLink USB devices; Intel Iris Xe now reports
240 Hz), `Microsoft.NETCore.App`/`WindowsDesktop.App` 8.0.32 (was 8.0.31), and
no .NET SDK (unchanged). Rust/cargo/rustup remain absent, winget `v1.29.380`
and Node `v24.18.0` are unchanged.

**Historical (2026-10-01; not present in this worktree, retained for provenance only):**

- `35ef2549-7a21-4393-a349-7a3e81e6a90d.stdout`: PowerShell smoke and CMD
  host version; initial aggregate inventory failed on missing `query.exe`.
- `05d3e817-cc59-4e3b-9a9c-1e3e71427e5f.stdout`: independent OS, GPU,
  monitor, language-mode, tool-location and .NET probes.
- `77f44d22-936f-4c05-a4d8-7211af55a981.stdout`: native window and cleanup.
- `63839281-4fb1-4fb4-b039-c4442f8c81bb.stdout`: active-monitor WMI,
  default Rust locations, winget and Node versions.
- `12712e76-78f8-4b0e-a4e3-cfe5b49d9e4e.stdout`: native smoke run carrying
  `NATIVE_WINDOW_PASS` / `CLEANUP_PASS` (previously referenced only by UAT).

Each PowerShell subprocess had a 30-second timeout (initial aggregate: 60
seconds). The missing `query.exe` was not retried; isolated probes recovered
all required inventory fields without that utility. Registry display-settings
enumeration returned no rows, so it adds no topology proof. Before claiming
multi-monitor/DPI acceptance, the shell must enumerate active displays,
coordinates, primary display and scale factors through its native display
API. No toolchain installation, policy bypass, shell build, or user-data
modification was performed.

### ConstrainedLanguage mode implication

`Add-Type` and other .NET-reflection-based scripting are blocked in this
PowerShell session (`Add-Type : No se puede agregar un tipo... modo de
lenguaje` / `PermissionDenied`). This blocks the obvious
`System.Drawing.Bitmap`-based ad-hoc screenshot script for visual-regression
evidence (§15/§17). Constrained Language Mode restricts the *scripting host*,
not compiled binaries — so a compiled helper (a small Tauri/Rust or dotnet-SDK
tool) avoids this scripting restriction. Separate executable/application
control policies may still reject it; helper execution must be tested.
Installing a build toolchain alone does not verify a screenshot path.

### Toolchain / shell decision follow-up

The Phase 1/2 ADR (`docs/architecture/desktop-shell-adr.md`) targets Tauri 2
long-term but only PoC'd Electron, because `cargo`/`rustc` was absent in the
WSL dev session. Their unavailability on PATH and at the current user's
default Rust install location is now confirmed on the **Windows host** too
(not just WSL). No new Tauri PoC was run by this task. `winget` is
available on the Windows host, so `rustup` can be installed there with one
explicit, user-confirmed step (matches the ADR's own condition: "install Rust
... before Phase 2 desktop-shell work starts" — that step was deferred past
Phase 2 into Phase 3, since Phase 2 was prototype-only). This decision gates
S02 (native application shell bootstrap) and is tracked as a project decision.

**Decision (D041, 2026-09-24):** ship Phase 3 on Electron; do not install a
Rust toolchain unilaterally. User was asked to choose between installing
Rust now vs. proceeding with the already-proven Electron path and answered
"you choose" — Ponytail (no new dependency/toolchain if avoidable) plus the
ADR's own shell-agnostic `ui/` + `shell/` boundary (a later Tauri migration
is a `shell/` + build-config swap, not a rewrite) made Electron the smaller,
reversible choice. Revisable.

## Production shell (P3.2/P3.3) — `desktop/`

`desktop/` is the real Electron host (distinct from `desktop-shell-poc/`,
which stays as the Phase 1 lifecycle-only evidence artifact). Structure:

- `desktop/src/mode-manager.js` — pure state machine, no Electron import,
  see `docs/architecture/window-state-machine.md`.
- `desktop/src/window-state-store.js` — pure bounds persistence/validation
  (monitor-disconnect recovery), no Electron import.
- `desktop/src/main.js` — Electron main process: single-instance lock,
  mode-manager-driven window lifecycle, `app://` protocol host (see below),
  CSP, window-open/navigation hardening, window-state persistence.
- `desktop/src/preload.js` — the entire native surface exposed to the
  renderer via `contextBridge` (`getMode`, `requestMode`, `onModeChanged`,
  `setOverlayInteractive`, `moveOverlayTo`). `contextIsolation: true`,
  `nodeIntegration: false`, `sandbox: true` on every window. No `require`,
  no arbitrary IPC channel, no filesystem/shell access reaches the renderer.
- `ui/src/shell/overlay-interop.ts` + `jarvis-desktop.d.ts` — renderer-side
  click-through hit-testing (see below) and the `window.jarvisDesktop` type.
  No-op outside Electron (`window.jarvisDesktop` is undefined in the
  Playwright/browser design-review harness — verified, see Testing below).

### Why `app://` instead of `loadFile`

`ui/dist/{index,overlay}.html` use Vite's default root-absolute asset paths
(`/assets/...`). Those resolve correctly over HTTP but break under `file://`
(they'd resolve against the filesystem root, e.g. `C:\assets\...`). Rather
than reconfigure Vite's `base` (which would touch the frontend build, out of
scope per the brief's "do not redesign the interface"), the main process
registers a privileged `app://` scheme (`protocol.handle`) that serves
`ui/dist` directly — `app://overlay/` → `overlay.html`, `app://command-center/`
→ `index.html` — with path-traversal rejection and its own CSP header. This
also satisfies §14: no window in this app ever loads or navigates to
`http(s)://`; `setWindowOpenHandler` denies all `window.open`, and
`will-navigate` is blocked for anything not `app://`.

### Click-through (the critical requirement, §5)

The overlay window starts in `setIgnoreMouseEvents(true, { forward: true })`
— Electron's documented recipe for a transparent window that still forwards
mouse-move events to its renderer for hit-testing while passing clicks
through to whatever is underneath. The renderer (`overlay-interop.ts`) uses
`document.elementFromPoint(x, y)` and only visible, connected, explicitly marked
`data-overlay-interactive` surfaces (including nested controls) capture input.
Descendants are not automatically interactive: empty canvas, null/hidden hits
and lifecycle leave/blur/hidden/unmount reset to pass-through. Duplicate Boolean
states emit no repeated IPC or movement logs. A `data-overlay-drag` handle uses
Electron drag CSS; buttons use no-drag. Native mode notifications synchronize
compact/expanded content and bounds. Only the owned overlay main frame may send
interaction IPC. These contracts pass automated tests but **real Windows input
receipt remains BLOCKED**; see the current overlay handoff above.

`Overlay.module.css` gained one additive rule set, scoped to
`html[data-desktop-shell='electron']` (set by `overlay-main.tsx` only when
`window.jarvisDesktop` exists): it makes the `.desktop` background actually
transparent and hides the Phase-2 demo mode-switcher strip (`.controls`,
explicitly commented in `Overlay.tsx` as "a demo control for this design
review"). The browser/Playwright design-review harness is unaffected —
`data-desktop-shell` is never set there, so both the solid background and
the mode buttons render exactly as before (confirmed: all 22
`overlay.spec.ts` cases, screenshots included, pass unchanged).

### Historical production-shell verification (not rerun in S01/T01 on 2026-10-01)

The earlier implementation reported the following. These results are not
current-session acceptance evidence; only the environment probes above
were rerun by this task.

Ran via `electron.exe` built for `win32` (installed through a `subst`-mapped
drive letter — `npm install` over the WSL UNC path silently failed to
download `electron.exe`, and separately `cmd.exe`-based shims refuse a UNC
current directory outright: "No se permiten rutas UNC"; a `subst J: \\wsl.localhost\Ubuntu\...`
drive-letter alias sidesteps both):

- Process launches, survives, reports a live PID and a native `MainWindowTitle`
  (`Get-Process` from the Windows side) — a real Win32 window exists.
- `[jarvis-desktop] mode: HIDDEN -> OVERLAY_COMPACT` logged — the mode
  manager's first transition ran end-to-end (state machine → `applyMode` →
  real `BrowserWindow` creation → `app://overlay/` load) with no errors.
- **Gotcha, cost real debugging time:** Chromium's GPU process failed to
  launch on this host (`GPU process launch failed: error_code=18`, then
  `FATAL: GPU process isn't usable. Goodbye.` — kills the whole app). Neither
  `app.disableHardwareAcceleration()` alone nor CLI flags placed *after* the
  app path (`electron.exe . --disable-gpu`, silently ignored — Electron
  treats args after the app path as app argv, not Chromium switches) fixed
  it. Root cause worked around, not diagnosed (plausibly a sandboxed/restricted
  child-process-launch policy on this host — PowerShell on the same machine
  runs in `ConstrainedLanguage` mode, suggesting WDAC/AppLocker-style
  controls that could also gate GPU-helper process creation). Fix:
  `app.commandLine.appendSwitch('in-process-gpu')` (plus `disable-gpu`,
  `disable-gpu-compositing`) called before `app.whenReady()`, forcing GPU
  work into the browser process instead of spawning a child — `desktop/src/main.js`,
  marked with a `ponytail:` comment naming the ceiling (software-rendering
  only) and the upgrade path (drop it once native hardware-accelerated
  launch is confirmed stable on target machines).
- **Not verified — still blocked:** actual visual transparency and
  click-through, pixel-for-pixel. `ConstrainedLanguage` mode blocks
  `Add-Type`, so there is no scriptable Win32/.NET screenshot path this
  session (see above); `SnippingTool.exe` exists but is interactive-only.
  This is the same screenshot blocker already recorded in S01 — still open,
  now confirmed to also block Phase 3's own visual-verification requirement
  (§15/§17), not just the earlier design-review one. Report as **BLOCKED**,
  not PASS, until a compiled screenshot tool (post-Rust-toolchain, or a
  `.NET` SDK install) or direct user verification is available.

### Testing

- `node --test desktop/src` — 11/11 pass (`mode-manager.test.js`,
  `window-state-store.test.js`): transition validity, overlapping-transition
  rejection, every mode reaching `HIDDEN`/`OVERLAY_COMPACT` directly, bounds
  visibility math, monitor-disconnect re-centering.
- `npm run build` (ui/) — `tsc -b && vite build` clean.
- `npx playwright test overlay.spec.ts` — 22/22 pass, including all 8
  screenshot states, unaffected by the `data-overlay-root` attribute and the
  Electron-only CSS branch.

## Phase 3 status (final, 2026-10-14)

Not a clean pass: see [phase-3-report.md](../phase-3-report.md) for every verdict.

- Acceptance ledger (reconstructed; the brief was not found): [phase-3-acceptance.json](../phase-3-acceptance.json)
- Live Windows QA matrix: [desktop-windows-qa-matrix.md](../desktop-windows-qa-matrix.md), [desktop-qa-evidence.json](../desktop-qa-evidence.json)
- Visual verification (6 FAIL, 26 BLOCKED): [desktop-visual-verification.md](../desktop-visual-verification.md), [desktop-visual-evidence.json](../desktop-visual-evidence.json)
- Automated results: [desktop-qa-automated.md](../desktop-qa-automated.md)
- Operator runbook: [desktop-handoff.md](../desktop-handoff.md)
