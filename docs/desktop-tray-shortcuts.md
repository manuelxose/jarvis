# Desktop tray, global shortcuts and launch at sign-in

All of this lives in the Electron main process (`desktop/src/main.js`, `tray.js`, `shortcuts.js`). The renderer can only read shortcut status and toggle launch-at-sign-in (strict boolean, command-center main frame only).

## Tray menu

| Item | Behavior |
|---|---|
| Open Command Center | Requests `COMMAND_CENTER_WINDOWED`. |
| Show overlay / Hide overlay | Toggles `OVERLAY_COMPACT` / `HIDDEN`. Clicking the tray icon does the same. |
| Settings | Opens Command Center and navigates to Settings. |
| Launch at sign-in (checkbox) | Persists to `window-state.json`, then applies the HKCU Run entry. |
| Shortcut lines (disabled) | Show each accelerator, suffixed `(unavailable)` when registration failed. |
| Exit desktop (Jarvis keeps running) | Stops the desktop UI and its bridge only. Jarvis Core is untouched. |
| Stop Jarvis Core… | Enabled only while connected. A confirmation dialog (Cancel is the default) precedes the loopback control `quit`. The renderer has no path to this action. |

## Shortcuts

Defaults: `Control+Alt+Shift+J` toggles the overlay, `Control+Alt+Shift+C` toggles Command Center.

Reserved (rejected, falling back to the default for that action): `Control+Alt+J` (Core's daemon hotkey), and `Control+Shift+` `P`, `E`, `F`, `G`, `D`, `X`, `N`, `T`. Super/Meta, single-modifier combos and named keys (Alt+Tab, Alt+F4, arrows, backtick...) are invalid. Duplicates across actions are rejected.

Configure by editing `window-state.json` in the Electron userData folder while the desktop is closed:

```json
{ "shortcuts": { "toggleOverlay": "Control+Alt+Shift+J", "toggleCommandCenter": "Control+Alt+Shift+C" } }
```

Rejected values are logged as `shortcut-rejected`. A registration collision with another app logs `shortcut-unavailable` (action, accelerator, reason); the desktop keeps running and the tray/Settings show the shortcut as unavailable.

## Launch at sign-in

Default off. When enabled, the HKCU `...\Run` entry launches the desktop with `--launched-at-login`, which starts hidden (tray only). The stored preference is re-read from the OS after every apply, so it never claims a state the OS does not have. Electron compares registered args literally, so the check uses `executableWillLaunchAtLogin` as well; before this fix, a dev-mode Run entry was written but read back as off and could not be disabled.

## Live Windows verification

Command: `npm --prefix desktop run smoke:tray -- --evidence <file>` (runs `powershell.exe` from WSL, isolated userData under the worktree, Run key backed up and restored, only identity-matched probe processes are stopped). Raw output: `docs/desktop-tray-evidence.json`. Unit tests: `npm --prefix desktop run test:native-tray`.

Host: Windows with PowerShell in ConstrainedLanguage mode (`Add-Type`, `New-Object` of .NET types and .NET method calls are blocked), so keystroke injection uses Python ctypes `SendInput`. Electron runs from the local runtime cache (`%LOCALAPPDATA%\jarvis\electron-runtime`); app code stays in the worktree. Run 2026-10-14 19:22Z with a Jarvis Core on 127.0.0.1:47811.

| Scenario | Verdict | Evidence |
|---|---|---|
| tray-created | PASS | `tray-ready` logged and the identity-matched process stayed alive (not a pixel check of the notification area). |
| shortcut-registered | PASS | `shortcut-registered` for `Control+Alt+Shift+J` and `Control+Alt+Shift+C`. |
| shortcut-injection-toggles-overlay | PASS | Eight real `SendInput` key events (Ctrl+Alt+Shift+J) toggled the overlay mode, and the renderer was shown. |
| shortcut-collision-reported | PASS | RegisterHotKey helper unavailable (same reason), so a second probe instance held the accelerator: `shortcut-unavailable {action: toggleOverlay, accelerator: Control+Alt+Shift+J, reason: registration-failed}` and the app stayed alive. |
| login-item-roundtrip | PASS | Enable wrote `jarvis-desktop REG_SZ "<electron.exe>" "<app>" --launched-at-login`; disable removed it; original state (absent) restored and re-read equal. |
| exit-desktop-leaves-core | PASS | Core answered on 47811 before and after the desktop exited. "Stop Jarvis Core" is not exercised against a real Core. |
| cleanup | PASS | 0 electron processes remained; Run key verified restored. |

Overall verdict: PASS (7/7). `exit-desktop-leaves-core` needs a Core on the default port; without one it reports BLOCKED.
