# Desktop overlay acceptance and handoff

M010/S03/T03, 2026-10-13. **Native overlay acceptance: PASS_WITH_GAPS** (exit 0).
10 of 25 native routing scenarios PASS on the real Windows desktop; the 15
Explorer, VS Code and terminal scenarios are explicit, owner-accepted
environment gaps (below). They are reported as gaps, never as PASS.

## Reproduce safely

Run from the active worktree root in WSL with Node >=22.12, `powershell.exe`,
`wslpath`, and executable Windows `python.exe` with stdlib ctypes/Win32/
UIAutomation access. Edge (or Chrome) is required for the browser scenarios.

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
node --test desktop/scripts/native-overlay.test.cjs desktop/scripts/native-smoke.test.cjs
npm --prefix ui exec -- playwright test src/testing/overlay-shell.spec.ts
node desktop/scripts/native-smoke.cjs
node desktop/scripts/native-overlay.cjs --accept-gaps=explorer,vscode,terminal
```

`desktop/node_modules` must be a real directory in the worktree (a symlink is
rejected with `electron-symlink-rejected`). Source, `ui/dist`, isolated state
and logs stay in this worktree; no WSLg, staged checkout, sandbox disablement
or renderer input API is used. The CLI is opt-in; importing it has no launch
effects. Without `--accept-gaps` any FAIL or BLOCKED returns 1. Each run writes
`report.json` into a fresh `desktop-native-overlay-*` directory.

### Host prerequisites found and used

- Windows `ForegroundLockTimeout` was set to 0 (was 2147483647) so the harness
  can acquire foreground for its owned recipient; without it every foreground
  request was denied (`foreground-denied:win32-18`).
- Probe-launched Edge and Electron use `--force-device-scale-factor=1`; the
  host is 4K at 150% scaling and client-to-screen conversion must be exact.
- Process start identities are UTC FILETIME ticks.

### Product changes behind the pass

- Main-process cursor tracking decides overlay interactivity (the forwarded
  mouse events alone were not reliable over a transparent window).
- Compact/expanded changes resize the native window with `setBounds`.

## Measured matrix (current run)

Final verification run (PASS_WITH_GAPS, exit 0): `desktop-native-overlay-JvGgSQ/report.json` and `probe.json`. Earlier run: `desktop-native-overlay-JE0Two/report.json` and `probe.json` (previous, after one earlier run in this verification, `desktop-native-overlay-OojCMz`, failed with `overlay-exited-before-probe` and was not reproduced on rerun); previous passing run `desktop-native-overlay-JqcKuR`; execution
output `(transcript not retained)` (status
PASS_WITH_GAPS, exit 0). Electron exit 0, no host error, cleanup errors none,
remaining owned processes none. Real input (`realInput: true`) via ctypes
SendInput; screenshots `screenshot-01.bmp` to `screenshot-09.bmp` in that
directory.

| Scenario | Result | Notes |
|---|---|---|
| Browser (Edge, controlled local page): `empty-root`, `transparent-margin`, `exit-reentry`, `leave-reset`, `hide-reset` | 5 PASS | Click passes through to the page; trusted pointer receipt observed |
| `overlay.control`, `overlay.mode-roundtrip` | 2 PASS | Interactive control responds; compact -> expanded -> compact |
| `overlay.drag` | PASS | Measured native window displacement |
| `overlay.native-surface` | PASS | Borderless, topmost, z-order facts observed |
| `overlay.compositor` | PASS | Native screenshots captured over the real desktop |
| Explorer x5 (`explorer.*`) | 5 accepted gaps | `explorer-launch-exited-before-window`: `explorer /separate` hands off to the running shell and exits, so no owned window can be observed |
| VS Code x5 (`vscode.*`) | 5 accepted gaps | Originally `observation-timeout`; re-diagnosed 2026-10-14: a pending VS Code update held the `vscode-updating` mutex. With the update applied, the window opens but no reliable UIA receipt exists: runs report `vscode-visible-safe-uia-control-unavailable` or `vscode-focus-reset-not-observed` |
| Terminal x5 (`terminal.*`) | 5 accepted gaps | Originally `observation-timeout`; re-diagnosed 2026-10-14: cmd.exe exited on DEVNULL stdin, and Windows 11 hands bare consoles to Windows Terminal. Now launched as `conhost.exe cmd.exe` with an owned window, but the console's single text area keeps UIA keyboard focus (`terminal-focus-reset-not-observed`) |
| Owned cleanup | PASS | `cleanup.stopped/remaining` empty; recovery empty |
| Automated suites | PASS | build, lint, transport, desktop, harness contracts (103 node tests, 21 probe tests), Playwright stub |
| Lifecycle (`native-smoke.cjs`) | PASS | Command center and overlay entries, explicit quit; `(transcript not retained)`, `desktop-native-smoke-s0jEuJ/` |

The gaps are an owner decision (2026-10-13): the overlay was verified visually
by the owner and by this run, and the three third-party recipients cannot be
owned by this host. They remain visible in `acceptedGaps` of the report. Any
FAIL, or any BLOCKED outside the accepted list, still fails acceptance. The
Playwright native-bridge stub is a contract test only and is not Windows proof.

Earlier attempts (all BLOCKED/FAIL, `observation-timeout`, foreground denied,
or controller exit) are retained as history in
`desktop-native-overlay-VP52LO`, `-xkT5mD`, `-eJppJx`, `-lIsHOr`, `-gDprBb`
where present; they are not current evidence.

## Interaction and transient mode contract for S05

- Native overlay starts pass-through: `setIgnoreMouseEvents(true, { forward: true })`.
  Only visible, connected `data-overlay-interactive` surfaces and their nested
  controls capture input; empty root/canvas, hidden or disconnected areas do not.
- `data-overlay-drag` marks the explicit Electron CSS drag handle. Buttons and
  scrollable controls are `no-drag`.
- Desktop-only html/body/root backgrounds are transparent and prototype review
  controls hidden. Browser-only review retains all eight fixture states.
- Main-process mode notifications drive compact/expanded renderer content and
  native bounds via the existing typed bridge. S05 owns the state machine and
  mode persistence; monitor restoration is not accepted here.
- Interaction IPC accepts only a Boolean from the owned overlay main frame;
  subframes, other windows, malformed payloads and stale hosts are rejected.
  Leave, blur, hidden/pagehide/unmount and host crash/hide reset capture. Only
  changed values are sent; moves are never logged individually.
- Sandbox, context isolation, CSP and navigation restrictions remain intact.
  Harness-only main-process operations are fixed and allowlisted.

## Diagnostics, limits and safe recovery

The CLI validates PID/executable/start identity, HWND, visibility, foreground,
client bounds and screen conversion before every input, then independently
observes the recipient (UIAutomation focus or controlled-page trusted pointer
acknowledgement). Missing observations, screenshots, invalid coordinates,
unobserved exit and cleanup uncertainty cannot produce PASS (see
`native-overlay.test.cjs`).

Each run retains `apps.json`, `owned.json`, `recipients.json`,
`probe-owned.json`, `state/`, host/probe stdout/stderr, `probe.json`,
`report.json` and `screenshot-NN.bmp`. These are local evidence, not source
fixtures; do not publish desktop images indiscriminately.

Execution is sequential and bounded: Python session budget 85 s, at most 32
inputs and 32 screenshots, probe monitor 105 s, bridge timeout 130 s. No
resident watcher remains. Do not parallelize native sessions.

On failure inspect the report and cleanup fields before retrying. Never stop
user applications or Core, click arbitrary coordinates for focus, use
image-wide taskkill or weaken sandbox/policies. Cleanup revalidates recorded
path and start time; PID reuse is not authorization. If a bridge is
interrupted, use the exported `recoveryScript` with that run's journals, then
recheck remaining identities. Delete a run directory only once survivors and
cleanup errors are empty.

Phase 2 Core data is **prototype fixture data**. This report accepts no
production Core connection (S06), audio, global shortcuts, monitor/DPI
restoration or mode persistence. Existing visual baselines were not updated.
