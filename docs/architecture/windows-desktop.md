# Windows desktop shell — architecture

Phase 3 (M010). This document is built up slice by slice as the native shell,
overlay, Command Center and mode manager land. This section covers the
environment ground truth gathered in S01 before any shell code was written.

## Environment facts (verified 2026-09-24, S01)

The agent session runs in WSL2 (`Linux 5.15.153.1-microsoft-standard-WSL2`).
`powershell.exe`/`cmd.exe` invoked from that WSL shell reach the **real Windows
host desktop**, not a WSLg-nested display — confirmed by launching
`notepad.exe` via `powershell.exe -Command Start-Process` and reading back a
live PID (`17436`) and window title from the Windows side, then terminating it
with `Stop-Process`. This is a real, verifiable execution path for building
and running the native shell and for driving Windows-specific acceptance
scenarios (§18 of the phase brief).

| Fact | Value | Source |
|---|---|---|
| Windows version | `10.0.26200.0`, 64-bit | `[Environment]::OSVersion` |
| GPU / display adapters | NVIDIA GeForce RTX 3070 Laptop GPU (3840×2160), Intel Iris Xe Graphics (1920×1080) | `Get-CimInstance Win32_VideoController` |
| Monitor topology | Dual-monitor: one 4K panel, one 1080p panel (mixed-DPI scenario confirmed available for §8 testing) | video controller enum; `System.Windows.Forms.Screen` unavailable, see below |
| PowerShell language mode | `ConstrainedLanguage` | `$ExecutionContext.SessionState.LanguageMode` |
| Node.js (Windows side) | present, `C:\Program Files\nodejs\node.exe` | `where.exe node` |
| npm (Windows side) | present | `where.exe npm` |
| cargo / rustc (Windows side) | **absent** | `where.exe cargo` → not found |
| rustup | **absent** | `where.exe rustup` → not found |
| winget | present, `...\WindowsApps\winget.exe` | `where.exe winget` |
| dotnet | runtime present, **no SDK** | `dotnet --version` → "No .NET SDKs were found" |

### ConstrainedLanguage mode implication

`Add-Type` and other .NET-reflection-based scripting are blocked in this
PowerShell session (`Add-Type : No se puede agregar un tipo... modo de
lenguaje` / `PermissionDenied`). This blocks the obvious
`System.Drawing.Bitmap`-based ad-hoc screenshot script for visual-regression
evidence (§15/§17). Constrained Language Mode restricts the *scripting host*,
not compiled binaries — so a compiled helper (a small Tauri/Rust or dotnet-SDK
tool) run as its own process is unaffected. This is why the Rust-toolchain
decision below also unblocks screenshot tooling, not just the shell.

### Toolchain / shell decision follow-up

The Phase 1/2 ADR (`docs/architecture/desktop-shell-adr.md`) targets Tauri 2
long-term but only PoC'd Electron, because `cargo`/`rustc` was absent in the
WSL dev session. That absence is now confirmed on the **Windows host** too
(not just WSL) — Tauri has still never been proof-of-concept'd. `winget` is
available on the Windows host, so `rustup` can be installed there with one
explicit, user-confirmed step (matches the ADR's own condition: "install Rust
... before Phase 2 desktop-shell work starts" — that step was deferred past
Phase 2 into Phase 3, since Phase 2 was prototype-only). This decision gates
S02 (native application shell bootstrap) and is tracked as a GSD decision.

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
through to whatever is underneath. The renderer (`overlay-interop.ts`) asks
`document.elementFromPoint(x, y)` on every forwarded mousemove: if the hit
element carries `data-overlay-root` (added to `Overlay.tsx`'s single
background div — the only frontend markup change made) or is `null`, that
point is empty space and the window keeps passing clicks through; any other
hit (a button, the conversation panel, ...) flips `setIgnoreMouseEvents(false)`
so that element is actually clickable. This is generic — it needs no further
plumbing as overlay content grows, since anything nested inside the root div
is automatically a capture region.

`Overlay.module.css` gained one additive rule set, scoped to
`html[data-desktop-shell='electron']` (set by `overlay-main.tsx` only when
`window.jarvisDesktop` exists): it makes the `.desktop` background actually
transparent and hides the Phase-2 demo mode-switcher strip (`.controls`,
explicitly commented in `Overlay.tsx` as "a demo control for this design
review"). The browser/Playwright design-review harness is unaffected —
`data-desktop-shell` is never set there, so both the solid background and
the mode buttons render exactly as before (confirmed: all 22
`overlay.spec.ts` cases, screenshots included, pass unchanged).

### Verified this session, on the real Windows host

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
