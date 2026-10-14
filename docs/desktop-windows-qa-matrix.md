# Windows desktop QA matrix (M010/S08)

Date: 2026-10-14 (S09/T03 matrix run at 18:41Z plus a tray re-run at 19:22Z; supersedes the S08 run). Evidence: [`desktop-qa-evidence.json`](desktop-qa-evidence.json), aggregate **BLOCKED**: 7 PASS, 2 BLOCKED, 0 FAIL.
Every status below comes from parsed harness evidence scored by `desktop/scripts/native-qa-matrix.cjs`. None was edited by hand.

## Host

| Fact | Value |
|---|---|
| Windows | 10.0.26200 (Windows 11), WDAC code-integrity enforced, so PowerShell runs in ConstrainedLanguage mode |
| Monitors | 3: `DISPLAY5` 3840x2160 @144 dpi (150 %, primary); `DISPLAY1` 1920x1080 @96 dpi at (-1920, 1173); `DISPLAY10` 1920x1080 @96 dpi at (3840, 0) |
| Electron | 44.4.5, cached on local NTFS (`%LOCALAPPDATA%\jarvis\electron-runtime\44.4.5`); app code runs from the WSL worktree |
| Windows Python | `C:\Python312\python.exe` for native probes; Core runs on `%LOCALAPPDATA%\jarvis-m002-venv` (`JARVIS_WIN_PYTHON`) |
| Core config | `config.win.json` (core-lifecycle derives only the control/events ports and the db path) |

## Scenarios

| Scenario | Status | Evidence | Notes |
|---|---|---|---|
| shell-launch | PASS | smoke | Command Center and overlay entries launch and quit |
| overlay-transparent-click-through | BLOCKED | overlay | Overlay itself PASS (control, mode round trip, drag, native surface, compositor). Edge recipient 5/5 PASS. Explorer, VS Code and terminal recipients are the owner-accepted gaps from 2026-10-13 (see [desktop-overlay.md](desktop-overlay.md)); re-diagnosed reasons in the evidence |
| command-center-modes | PASS | command-center | |
| fullscreen-escape | PASS | command-center | |
| window-state-restore-multi-monitor | BLOCKED | window-state | 5/5 automated rows PASS; `physical-display-change` needs the owner to unplug a monitor |
| dpi-scaling-topology | PASS | topology | 3 monitors, mixed DPI (100/100/150 %) read through Python ctypes |
| core-attach-restart | PASS | core-lifecycle | 7/7: start, attach, quit with the desktop surviving, relaunch with token rotation, desktop exit with Core surviving, cleanup, no Python under the desktop |
| tray-shortcuts-login | PASS | tray | 7/7 in [desktop-tray-evidence.json](desktop-tray-evidence.json): tray created, shortcuts registered, real `SendInput` toggles the overlay, collision reported, login item round-trips and the registry is restored, and exiting the desktop leaves Core running. Re-run alone with a Core on 47811 |
| no-orphan-processes | PASS | process-scan | No harness `electron.exe`/`jarvis-desktop.exe` left (scan after the last run) |

All results are automated native evidence. Nothing in this table is a human observation.

## How it was run

The two Core-dependent harnesses need opposite host states, so the result comes from two runs:

1. `node desktop/scripts/native-qa-matrix.cjs --evidence docs-qa-evidence.json` with a Jarvis Core listening on the default port 47811, as the tray's exit-desktop check requires.
2. `native-core-lifecycle.cjs` alone, with the default ports free. Its harness refuses to start while a non-harness Core owns 47811/47812.

The committed evidence JSON re-scores the 18:41Z `qa:matrix` run (no Core running, so core-lifecycle could use its ports) with the 19:22Z tray run (Core on 47811), using the matrix's own `scoreScenarios`/`aggregate` (`reaggregation` field). No status was edited by hand.

## Harness fixes made during this run

- Topology probe and tray `SendInput`: moved from PowerShell `Add-Type` to Python ctypes, because ConstrainedLanguage forbids `Add-Type`.
- core-lifecycle:
  - Core and the desktop launch through WMI `Win32_Process.Create`. A Core started by `Start-Process` under the bridge kept the WSL interop call open (`bridge-ETIMEDOUT`).
  - The control-socket client moved to Python, since ConstrainedLanguage blocks `TcpClient`.
  - `step.tree` output is flattened. Before, stop received null PIDs and left survivors.
  - Every root's tree is re-snapshotted before cleanup.
  - The `memory` section of the Jarvis config is optional.
  - The Windows venv can be set with `JARVIS_WIN_PYTHON`.
- tray: uses the local Electron runtime like the other harnesses. From UNC the sandboxed renderer never started.
- Overlay probe:
  - The terminal recipient launches as `conhost.exe cmd.exe` in its own console, so the window is owned by the run.
  - A bare `cmd.exe` went to Windows Terminal, or exited on DEVNULL stdin.
  - A launch that times out now reports `*-launch-exited-before-window` or `*-window-not-observed` instead of a generic timeout.

## Environment notes

- The project `.venv` is a Linux venv, so no Windows `.venv\Scripts` exists. `jarvis-m002-venv` lacks `dashscope` and the CUDA wheels; Core still started, answered and rotated tokens.
- A pending VS Code update (the installer holding the `vscode-updating` mutex) stopped new VS Code windows from opening until VS Code was restarted.

## Final rerun note (2026-10-14)

Run with `JARVIS_WIN_PYTHON` pointing at the Windows venv (without it core-lifecycle reports `windows-venv-missing`). One intermediate run reported window-state `FAIL` (`window-state-exited-before-probe`); the harness run alone and a full rerun both passed with the same code, so it is recorded here as a transient host flake, not hidden. Overlay recipients Explorer, VS Code and terminal remain the owner-accepted BLOCKED gaps.
