# Desktop shell security review (M010/S08)

Scope: `desktop/src/main.js`, `preload.js`, `ui-protocol.js`, `core-bridge.js`. Executable contract: `desktop/src/security.test.js` (part of `npm --prefix desktop test`).

## Threat model

| Actor | Capability assumed | Goal |
|---|---|---|
| Untrusted renderer content (XSS in the UI, compromised dependency, injected remote page) | Runs JS in a Jarvis window | Reach Node/OS, read files, spawn processes, open external pages, exfiltrate |
| Local process on loopback | Can connect to 127.0.0.1 ports and the Core control port | Impersonate Core, replay a token, inject events into the UI |
| Malicious/incompatible Core | Replies on the control port | Feed malformed/oversized frames, wrong protocol, drive the shell |

Out of scope: a local admin/same-user attacker able to modify the install or process memory.

## Controls

| # | Control | Location |
|---|---|---|
| C1 | `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true` on the only `BrowserWindow` | `main.js:248` |
| C2 | DevTools off unless `JARVIS_DESKTOP_DEVTOOLS=1` | `main.js:248` |
| C3 | Window-open denied per window and for every webContents (`web-contents-created`) | `main.js:270`, `main.js:551-552` |
| C4 | `will-navigate`/`will-redirect`/`will-frame-navigate` restricted to the exact `app://<host>/` entry; baseline for all webContents | `main.js:271-276`, `main.js:554-556` |
| C5 | `will-attach-webview` denied (per window and global) | `main.js:277`, `main.js:553` |
| C6 | Session permission request denied and permission check false for everything | `main.js:558-563`, installed before IPC/windows at `main.js:613` |
| C7 | IPC sender authentication: `owned()` checks sender webContents, main frame (not subframes) and URL against the host entry; every `ipcMain.handle/on` calls it | `main.js:489`, `main.js:500-540` |
| C8 | IPC payload validation (booleans, mode allow-list, finite bounded numbers, `CORE_ACTIONS = activate|sleep`) | `main.js:24`, `main.js:504-531` |
| C9 | Preload exposes a single `jarvisDesktop` object with fixed methods; no `ipcRenderer`, `require`, or generic channel; inbound events re-validated | `preload.js:28` |
| C10 | `app://` protocol: strict host regex, rejects `\`, whitespace, encoded `.`/`/`/`\`/`%`, dot segments, drive/control chars, non-allow-listed files; `realpath` containment inside `ui/dist` | `ui-protocol.js:9-35` |
| C11 | CSP on every response: `script-src 'self'`, no `unsafe-eval`, `object-src/frame-src/base-uri 'none'`, `connect-src` self + `127.0.0.1` only; `nosniff` | `ui-protocol.js:6`, `:41` |
| C12 | Core bridge is attach-only: never spawns Core, loopback `HOST`, discovery URL regex pinned to `127.0.0.1`, token max 256 chars, 4 KiB discovery / 64 KiB frame limits, protocol version checked | `core-bridge.js:3-15` |
| C13 | Only the main-process tray (confirmed dialog) can call `stopCore()`; not reachable from renderer IPC | `core-bridge.js` (`stopCore`), `main.js` tray handler |
| C14 | Sources import no `child_process`/`shell`; no `eval`/`new Function` | verified by `security.test.js` |

## Findings

| ID | Severity | Finding | Status |
|---|---|---|---|
| F1 | Medium | No session permission handlers; a page could be granted media/notifications/etc. | FIXED (C6) |
| F2 | Medium | No app-level guard for webContents not created by `createWindow` (popups, webviews, navigation) | FIXED (C3-C5), diagnostics `window-open-denied`, `webview-denied`, `navigation-denied`, `permission-denied` |
| F3 | Low | DevTools reachable in production windows | FIXED (C2); opt-in via env var for development |
| F4 | Low | CSP `style-src 'unsafe-inline'` | ACCEPTED: required by the React/Vite UI inline styles; scripts remain `'self'` only |
| F5 | Low | `connect-src` allows any loopback port | ACCEPTED: Core UI port is dynamic (discovered at runtime); loopback-only |
| F6 | Low | Core SSE token passed in the query string (`/events?token=`) | ACCEPTED: loopback, token rotated on Core restart, never logged by the bridge; Core protocol is fixed by D053 |
| F7 | Info | Control-port `quit` line is unauthenticated | ACCEPTED: Core-side, loopback; renderer cannot reach it (C13) |

## Residual risks

- Build is unsigned and Electron fuses (e.g. `RunAsNode`, `EnableNodeCliInspectArguments`) are not flipped; a same-user attacker who can launch the binary with flags is out of scope but fuses are recommended before distribution.
- `'unsafe-inline'` styles (F4) allow CSS injection if HTML injection were ever possible.
- Any local process can bind a loopback port and be reached by `connect-src`; the renderer only ever connects through the main-process bridge, so exposure is limited to UI-originated fetches.
