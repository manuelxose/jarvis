# Desktop shell bootstrap handoff

M010/S02/T03, 2026-10-02. **Native lifecycle acceptance: PASS** (command-center and
overlay: real window shown, nonzero HWND, exit 0, no survivors). An earlier
BLOCKED result was caused by running `electron.exe` from the UNC worktree path;
see "Root cause" below. Visual/audio acceptance is still not tested.

## Overlay acceptance follow-up (S03/T03, 2026-10-13)

[Desktop overlay acceptance and handoff](desktop-overlay.md) records the current
Windows lifecycle rerun (both entries PASS, no recorded survivors), automated
bridge checks, and **25 required native overlay scenarios BLOCKED** by foreground
acquisition or recipient observation. Click delivery, native drag, topmost and
compositor transparency are not accepted; no screenshots were generated. It
includes exact executed artifact references, bounded commands and cleanup,
explicit hit-region/drag policy and the transient S05 mode contract. Fixture
Core data remains prototype-only, not S06 production connectivity. Historical
bootstrap PASS below is not a substitute for overlay acceptance.

## Boundary and reproducible commands

`desktop/` is the Electron shell, not a new `shell/` project. `ui/` owns
frontend design and builds `ui/dist/index.html` and `ui/dist/overlay.html`.
Python Core is neither launched nor modified by these checks. Existing Phase 2
fixtures are prototype UI, not a proven production Core connection (S06).

Run these from the active repository/worktree root in WSL, using Linux Node
24 (the locked Electron installer requires Node >=22.12). No upgrades, Rust,
CMD, external staged source copy, or other checkout is required:

```sh
npm --prefix ui ci --no-audit --no-fund
npm --prefix desktop ci --no-audit --no-fund
ELECTRON_INSTALL_PLATFORM=win32 ELECTRON_INSTALL_ARCH=x64 \
  electron_config_cache="$PWD/desktop/.electron-cache" \
  node desktop/node_modules/electron/install.js
npm --prefix ui run build
npm --prefix ui run lint
npm --prefix ui run test:transport
npm --prefix desktop test
node --test desktop/scripts/native-smoke.test.cjs
powershell.exe -NoProfile -NonInteractive -Command '$ExecutionContext.SessionState.LanguageMode'
node desktop/scripts/native-smoke.cjs
```

Electron 44.4.5's npm package does not install its runtime automatically with
this lockfile: `npm ci` alone left `electron.exe` absent. Explicitly invoke its
existing installer as above. It downloads the locked Windows x64 artifact
into `desktop/node_modules/electron/dist/`; Linux Node tests remain usable.
The application entry handles Electron's dynamic-import loader as well as a
CommonJS main invocation; ordinary Node imports do not launch Electron.

For retained JSON, choose a new worktree-local filename:

```sh
node desktop/scripts/native-smoke.cjs --evidence desktop-smoke-result.json
```

This harness requires `powershell.exe` and `wslpath`, invokes PowerShell
without CMD, and derives Windows executable/application paths from the active
worktree. Its ordinary CLI tests command-center then overlay sequentially,
stopping after any failure or uncertain cleanup. Both entries now pass in one
CLI invocation using the recovered local runtime. Only the stock Electron
runtime is cached on NTFS; application source, built UI, logs and state remain
in the active worktree. This runtime-only exception was authorized during
T03 recovery; no application source is staged into another checkout.

For ordinary (non-isolated, preference-persisting) launch from **Windows
PowerShell already at the same project root**, avoid npm's CMD shim on UNC:

```powershell
$desktop = (Resolve-Path .\desktop).Path
$version = (Get-Content -Raw .\desktop\node_modules\electron\package.json | ConvertFrom-Json).version
$electron = Join-Path $env:LOCALAPPDATA "jarvis\electron-runtime\$version\electron.exe"
# Run the bounded harness above first to provision this runtime cache.
Start-Process -FilePath $electron -ArgumentList ('"' + $desktop + '"') -WorkingDirectory $desktop
```

Ordinary launch is not a bounded acceptance test. Closing command-center
returns to overlay; closing overlay hides it; neither promises process exit.
The smoke path explicitly closes its selected window and calls application
quit. No renderer quit API or production tray quit UI was added by S02.

## Readiness, diagnostics and recovery

Successful smoke must observe ordered `[jarvis-bootstrap]` JSON stages:
`dist-ready`, `created`, `main-frame-ready`, `renderer-mounted`, `shown`,
`closing-window`, `closing`, `window-closed`, `closed`, `quit` (code 0).
A loaded main frame is not readiness: the main process waits for actual
children inside `#root`, then shows the window. No user-data/token payload is
logged. Startup/renderer budgets are 15 seconds, quit watchdog 2 seconds,
Windows monitor 25 seconds, cleanup 5 seconds, bridge subprocess 45 seconds.
Inventory is capped at 512 identities and log/bridge buffers at 4 MiB.

Missing/incomplete builds fail preflight before native launch with
`missing-or-incomplete-ui-dist: npm --prefix ui run build`; rebuild and rerun.
Unreadable/missing assets, rejected loads, hung mount observations or crashed
renderers produce a bounded nonzero exit. Do not interpret `closed`/`quit`
after a `failure` as success. Native launch failures require the stderr log
and ownership evidence, not a substitute HTML page or mocked Core server.

Each harness run owns a fresh `desktop-native-smoke-*` directory with isolated
userData/sessionData/crashDumps, `stdout.log`, `stderr.log`, and `owned.json`.
State cannot overwrite existing preferences or use a symlink escape. Native
ownership includes PID, parent lineage, exact executable, session and string
start ticks; cleanup revalidates identity immediately before stopping any PID.
The process handle is retained at launch so its exit code survives exit.
Native `IntPtr.Zero` handles are explicitly excluded from window observations.
The monitor independently checks recorded descendants after parent exit and
recovery checks again on failure. Never use image-wide taskkill, kill unowned
processes, or disable sandboxing to obtain a PASS. Logs/state directories may
be removed only after owned-process absence is verified. No Core process is
included in ownership or cleanup.

## Trust boundary

Both windows retain `sandbox: true`, `contextIsolation: true`, and
`nodeIntegration: false`. Contained `app://command-center/` and
`app://overlay/` serve the real Vite root-relative assets with MIME, nosniff
and CSP headers, rejecting invalid hosts, encoded traversal/separators,
Windows alternate streams and symlink escapes. No raw `loadFile` substitution.
CSP restricts scripts to self, forbids objects/frames/base overrides, and
limits connections to self and loopback HTTP/WebSocket (future transport).
Inline styles remain allowed for the existing design.

Navigation/redirects are restricted to the selected entry; subframes, webviews
and popups are denied. IPC requires an owned main-frame sender, allowed origin,
and bounded typed payloads. Preload remains unchanged: mode get/request/change,
overlay interaction and move only. No renderer filesystem, shell, arbitrary
IPC, eval or smoke shutdown capability. DOM mount observation is a fixed
main-process expression, not a renderer-controlled code surface.

## Actual verification and native observations

All evidence references below are relative to this worktree under `.wf/exec/`;
workflow artifacts are not runtime dependencies and tests never read them.

| Scenario | Result | Observation / evidence |
|---|---|---|
| Lockfile provisioning and bridge | PASS | `1c2c41b0-dcd0-48dc-a90e-bf47ea301df1`: both locked installs; `3fb793d1-8248-40f8-8887-557f4f7dd34a`: ConstrainedLanguage; `b7622e97-66c4-4966-b743-549a3c3a1503`: Windows installer exit 0 |
| TypeScript/Vite build, lint, transport, desktop and harness contracts | PASS | `7ba4cb90-2b07-4401-98bb-7f1dead60313`: build succeeds; lint 0 errors, 2 warnings; transport 26/26, desktop 48/48, harness 41/41 (including actual constrained-PowerShell cmdlet-double checks) |
| Missing real build | PASS (negative) | `c89fa38d-3da7-4b77-9395-410ad16d51b6`, `T03-missing-build-current.json`: temporarily moved overlay entry, CLI returned 1 with actionable build diagnostic before launch; restored entry in finally |
| Windows command-center lifecycle | PASS | `T03-native-recovery-current.json`: PID 23412, title `ui`, HWND 13567976, session 1, exit 0; ordered stages; three owned identities, no survivors/cleanup errors |
| Windows overlay lifecycle | PASS | same evidence file: PID 15060, title `Jarvis Overlay`, HWND 2165040, session 1, exit 0; three owned identities, no survivors/cleanup errors |
| Owned failure shutdown | PASS (negative only; reproduced from the UNC executable) | `c89fa38d-3da7-4b77-9395-410ad16d51b6`, `T03-unc-failure-current.json`: command-center reports `failure(renderer-gone, launch-failed)`, quits 1, recovery remaining empty; no second launch |
| Renderer mount/show and nonzero HWND | PASS | both entries reach main-frame-ready, renderer-mounted and shown |
| Human visual/audio acceptance | NOT TESTED | No screenshots, pixel equivalence or human audio observations |

Both successful root executables resolve to
`%LOCALAPPDATA%\jarvis\electron-runtime\44.4.5\electron.exe`; full identities
are retained in the JSON, not reusable machine-specific absolute commands.
The worktree's original UNC executable is retained for provisioning and the
expected-failure diagnostic, not successful ordinary launch. Installed
Electron's loader uses `await import(...)`, exposing the old `require.main`
guard defect. Regression tests execute the corrected production module with
browser/Node/renderer import semantics. UNC-state sandbox/network warnings
do not replace the required renderer-mount, HWND, exit and cleanup checks.

### Root cause (resolved)

Chromium cannot launch its sandboxed renderer from an executable on a UNC path
(`\\wsl.localhost\...`): 0/3 runs reached `shown` from UNC, 3/3 from local NTFS.
`localRuntime()` in `desktop/scripts/native-smoke.cjs` therefore caches the stock
Electron runtime under `%LOCALAPPDATA%\jarvis\electron-runtime\<version>` (one
copy per locked version). App code, `ui/dist` and smoke state stay in the
worktree. UNC-state sandbox warnings were nonfatal in these observed lifecycle
runs; this does not establish security or visual equivalence for other hosts. The old
stage order in `STAGES` (`window-closed` before `closing`) never matched
`main.js` and was fixed at the same time. Do not use `--no-sandbox`.

No styling changes were made; renderer/pixel discrepancy evaluation is deferred
to visual acceptance. Audio, two-clap/welcome/TTS, click-through, monitor/DPI,
tray, fullscreen and production Core connectivity are not validated here. No
R001–R009 status change or validation is implied.
