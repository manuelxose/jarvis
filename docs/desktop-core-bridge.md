# Desktop Core bridge (M010 / S06)

The Electron desktop attaches to an already-running Jarvis Core. It never starts,
restarts or stops Core (D053). Core keeps the Phase 1 contract unchanged apart
from an additive protocol advertisement.

## Architecture

```
Core (python -m jarvis daemon)           Electron main process               Renderer
  control socket 127.0.0.1:47811  <--ui--  desktop/src/core-bridge.js
  /events (SSE, token)            ------>  state machine, token held here  --IPC-->  jarvisDesktop.core
  /control (POST, token header)   <------  control('activate'|'sleep')     <--IPC---  Command Center
```

- Discovery: the bridge sends `ui\n` to the loopback control socket
  (`JARVIS_CONTROL_PORT`, default 47811). The reply must be `ok`, a
  `http://127.0.0.1:<port>/events` URL, a token (<= 256 chars) and `protocol: 1`.
- Stream: `GET /events?token=...`; a 200 with `X-Jarvis-Protocol: 1` is `connected`.
  Frames are SSE `data:` JSON with `v === 1` and a string `name`.
- Control: `POST /control` with `X-Jarvis-Token`, only `activate` and `sleep`.
- Code: `desktop/src/core-bridge.js` (contract suite `core-bridge.test.js`),
  IPC wiring in `desktop/src/main.js`, validated preload `jarvisDesktop.core`,
  renderer transport `ui/src/services/desktop-transport.ts`.

## States and transitions

| State | Entered when |
|---|---|
| `connecting` | initial, and each discovery/stream attempt before first success |
| `connected` | stream returned 200 with matching protocol header |
| `reconnecting` | stream lost after having been connected |
| `disconnected` | Core absent (control socket refused/timeout) |
| `auth-failure` | 401 twice in a row (one re-discovery in between handles token rotation after a Core restart) |
| `incompatible-version` | missing/mismatched `protocol`, header, or event `v` |

Retries use bounded exponential backoff (500 ms doubling to 10 s); `auth-failure`
and `incompatible-version` retry at the slow interval. Transitions are emitted
only on change and logged as `[jarvis-core] {"state","reason","attempt"}`
without the token.

## Attach-not-launch and exit semantics

The bridge uses only `net` and `http` (a test asserts it does not require
`child_process`) and sends only `ui` to the control socket, never
`quit`/`restart`/`activate`. Quitting the desktop calls `bridge.stop()`
(sockets/timers released) and leaves Core running.

## Security

The token exists only in the main process. The renderer receives state names,
validated events and `{ok}` / `{ok:false,error}` control results. IPC is
allowlisted (`core:get-state`, `core:get-replay`, `core:control` with `activate|sleep`) and answers
only owned, live windows. `bootstrap.test.js` asserts the token never appears in
any IPC payload.

## Retained replay (S06 / D059)

The bridge retains the latest `core.snapshot` and up to 512 contiguous `core.signal` events so a window mounted
after the connection can resync immediately. `core:get-replay` / `jarvisDesktop.core.getReplay()` returns them
with `age_ms` advanced by elapsed time; it returns `[]` when not connected, on a gap/overflow/other session, and
for non-owner senders. The set is cleared when the stream is lost or stopped.

## Verification

Automated (all passing): `npm --prefix desktop test` (bridge contract suite
covering Core restart with token rotation, malformed/oversized/non-loopback
replies, header/`v` mismatch, control allowlist, 401/timeout, handle leaks),
`tests/test_core_handshake.py`, `ui/node-tests/desktop-transport.test.ts`,
`npm --prefix ui run build`.

**`auth-failure` and `incompatible-version` are contract-test proof only.** They
were not provoked against a real Windows Core.

### Real Windows lifecycle harness

```
node desktop/scripts/native-core-lifecycle.cjs [--control-port N] [--events-port N] [--evidence file.json]
node --test desktop/scripts/native-core-lifecycle.test.cjs
```

It locates a Windows venv (`.venv-win\Scripts\python.exe` or `.venv\Scripts\python.exe`
in the worktree, then the enclosing checkout; never substitutes another
interpreter), derives a config from `config.win.json` changing only
`daemon.control_port`, `daemon.ui_events_port` and `memory.db_path`, refuses to
run if 47811/47812 or its own ports (default 47921/47922) have any listener,
then runs: (a) harness Core up and `ui` reply, (b) desktop attaches
(`connected`), (c) `quit` to the harness Core only, desktop alive and
`reconnecting`/`disconnected`, (d) Core relaunched, desktop `connected` again
with a rotated token (compared by hash prefix), (e) desktop terminated, Core
PID alive and answering `status` (the desktop has no external quit channel, so
the harness terminates the identity-checked owned tree), (f) Core quit, no owned
survivors, plus a lineage check that the desktop owns no python process.
Exit codes: 0 PASS, 1 FAIL, 0 BLOCKED (the JSON report still says BLOCKED; set `JARVIS_LIFECYCLE_STRICT=1` to get 3 for BLOCKED). Cleanup stops only processes whose PID
and start time match what the harness recorded.

### Captured evidence (2026-10-14): BLOCKED

Evidence: [`bench/desktop-core-lifecycle-2026-10-14.json`](bench/desktop-core-lifecycle-2026-10-14.json).

All seven scenarios are **BLOCKED** (`windows-venv-missing`). No Windows Jarvis
venv exists on this host at any checked path:

- `<worktree>/.venv-win/Scripts/python.exe`
- `<worktree>/.venv/Scripts/python.exe` (the worktree `.venv` is a Linux venv)
- `<checkout>/.venv-win/Scripts/python.exe`
- `<checkout>/.venv/Scripts/python.exe` (Linux venv)

A system `Python311` exists under the Windows user profile but has not been
shown to carry Jarvis dependencies and was deliberately not substituted. No
process was started and nothing was touched on the Windows host.

**Therefore the real-Core restart/token-rotation demo is NOT evidenced on
Windows.** What is evidenced: the bridge state machine against loopback fake
Cores (including restart with token rotation), the Core handshake on the Python
side, the Electron wiring with a fake bridge, and the harness's pure logic and
refusal paths. To close the gap, provision `.venv-win` (see
[startup.md](startup.md)) and rerun the harness command above.
