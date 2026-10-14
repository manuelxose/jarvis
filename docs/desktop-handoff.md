# Desktop handoff: operator and developer runbook

Status and verdicts: [phase-3-report.md](phase-3-report.md). Design: [architecture/windows-desktop.md](architecture/windows-desktop.md).

## Prerequisites

- A Windows host (evidence was gathered on Windows 11) and WSL2 with `powershell.exe` reachable; native harnesses launch Electron through that bridge from the active worktree.
- `npm --prefix desktop ci` (Electron 44.x) and a built UI: `npm --prefix ui run build` must produce `ui/dist/index.html` and `ui/dist/overlay.html`. A missing build fails preflight with an actionable message before launch.
- Electron runtime cache: Chromium cannot sandbox from a UNC path, so the harness caches the stock runtime at `%LOCALAPPDATA%\jarvis\electron-runtime\<version>`. The first harness run provisions it. Do not use `--no-sandbox`. See [desktop-shell-bootstrap.md](desktop-shell-bootstrap.md).
- For Core features, a running Jarvis Core with its control port (default 47811, `JARVIS_CONTROL_PORT`). The desktop works without Core and shows a disconnected state.
- `JARVIS_WIN_PYTHON` for the Windows-side probes used by `qa:matrix`.

## Start and stop

Ordinary launch from Windows PowerShell at the project root: see the snippet in [desktop-shell-bootstrap.md](desktop-shell-bootstrap.md) (it avoids npm's CMD shim on UNC paths). `npm --prefix desktop start` runs `electron .` and works from a local (non-UNC) checkout.

Closing Command Center returns to the overlay; closing the overlay hides it. The process keeps running in the tray.

| Tray action | Effect |
|---|---|
| Exit desktop (Jarvis keeps running) | Stops the desktop UI and its bridge only. Core is untouched. |
| Stop Jarvis Core... | Enabled only while connected; confirmation dialog (Cancel default), then the loopback control `quit`. |

Details: [desktop-tray-shortcuts.md](desktop-tray-shortcuts.md).

## Shortcuts

Defaults: `Control+Alt+Shift+J` toggles the overlay, `Control+Alt+Shift+C` toggles Command Center. To change them, close the desktop and edit `shortcuts.toggleOverlay` / `shortcuts.toggleCommandCenter` in `window-state.json`. Reserved or invalid combinations fall back to the default and log `shortcut-rejected`; a collision logs `shortcut-unavailable` and the tray shows the shortcut as unavailable.

## Launch at sign-in

Default off. Toggle it from the tray ("Launch at sign-in") or Settings. It writes an HKCU `Run` entry with `--launched-at-login`, which starts hidden in the tray. The stored value is re-read from the OS after each change.

## Persisted state

`window-state.json` lives in the Electron userData folder (under `%APPDATA%` for an installed or ordinary launch; harnesses use an isolated directory under the worktree). It holds window bounds, mode, shortcuts and the launch preference. A corrupt or unreadable file is replaced in memory by defaults (logged as `corrupt-file` or `unreadable-file`); an invalid stored mode is logged as `invalid-initial-mode`. Bounds that no longer fit any monitor are re-centered. To reset manually, close the desktop and delete the file.

## Running tests and harnesses

| Command | Purpose |
|---|---|
| `npm --prefix desktop test` | Unit and contract tests for the main process. |
| `npm --prefix desktop run test:all-native` | Every `desktop/scripts/*.test.cjs` contract, including the visual evidence, acceptance ledger and report checks. |
| `npm --prefix desktop run test:native-smoke` | Smoke harness contract tests. |
| `npm --prefix desktop run smoke:native` | Live lifecycle smoke on the Windows host. |
| `npm --prefix desktop run test:native-core-lifecycle` | Core lifecycle harness contract tests. |
| `npm --prefix desktop run smoke:core-lifecycle` | Live Core attach/restart/token rotation. |
| `npm --prefix desktop run test:native-tray` | Tray harness contract tests. |
| `npm --prefix desktop run smoke:tray` | Live tray, shortcut and login-item check. |
| `npm --prefix desktop run test:native-qa-matrix` | Matrix orchestrator contract tests. |
| `npm --prefix desktop run qa:matrix -- --evidence docs/desktop-qa-evidence.json` | Full live matrix; exit 1 means at least one BLOCKED or FAIL row. |
| `npm --prefix desktop run test:native-visual` | Visual harness contract tests. |
| `npm --prefix desktop run visual:native` | Live capture and comparison against Phase 2 references. |
| `npm --prefix desktop run visual:baselines` | Bless a completed native capture run (`-- --from <run-dir>`) as Windows baselines; compare with `visual:native -- --baseline win32`. |
| `npm --prefix ui exec playwright test` | Phase 2 Chromium visual and behavior suite. Never pass `--update-snapshots` for acceptance runs. |

UI checks: `npm --prefix ui run build`, `npm --prefix ui run lint`, `npm --prefix ui run test:transport`. Python and voice-path commands are listed in [desktop-qa-automated.md](desktop-qa-automated.md).

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Preflight reports a missing build | `ui/dist` not built | `npm --prefix ui run build` |
| Window never reaches shown, from a `\\wsl.localhost` path | Chromium sandbox cannot start from a UNC executable | Use the cached local runtime (run a harness once); do not add `--no-sandbox` |
| Core shows disconnected | Core not running or wrong port | Start Core; check `JARVIS_CONTROL_PORT` (default 47811) |
| Core shows auth failure or incompatible version | Token mismatch or protocol other than 1 | Restart Core and desktop; see [desktop-core-bridge.md](desktop-core-bridge.md) |
| Shortcut does nothing | Collision or rejected combination | Look for `shortcut-unavailable` / `shortcut-rejected` in the logs; edit `window-state.json` |
| Window appears at defaults | Corrupt or unreadable state file | Expected recovery; delete the file to reset |
| Launch at sign-in will not turn off | Stale Run entry | Toggle via the tray; the state is re-read from the OS |
| Harness is BLOCKED on SendInput | PowerShell ConstrainedLanguage | Use an unrestricted PowerShell; see the report's BLOCKED table |
| Leftover `electron.exe` after a harness | Interrupted run | Harnesses stop only identity-matched owned PIDs; check `owned.json` in the run directory before killing anything |
