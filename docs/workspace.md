# Workspace profiles (`jarvis workspace`)

A workspace profile is a named set of tasks (VS Code, a terminal, services, URLs) that Jarvis
starts together and shuts down safely. It lives in `workspace.profiles` of the config; the generic
`dev` profile in `config.win.json` opens Ollama, VS Code, Windows Terminal and Task Manager.

```
jarvis workspace start [PROFILE]          # default: workspace.default_profile
jarvis workspace status
jarvis workspace stop [PROFILE] [--force]
```

Each command prints JSON. `start` and `stop` print `{profile, total_ms?, tasks: [{name, status,
elapsed_ms, detail, pid}]}`; `status` prints, per profile, the task names and the `managed`
entries (`pid`, `create_time`, `stop`). Exit code 0 unless the command itself failed. Implementation:
`src/jarvis/application/workspace.py` (`WorkspaceManager`).

## Profile format

| Field | Meaning |
|---|---|
| `name` | Unique within the profile. |
| `command` **or** `url` | Exactly one. `command` is an argv list (`%VAR%` expanded); `url` must be `http(s)`. |
| `cwd` | Working directory for `command`. |
| `window` | `gui` (default), `hidden` (output logged to a file) or `new_console`. |
| `depends_on` | Task names that must reach an OK status first. Tasks with no dependency relation start concurrently; a failed dependency makes the dependent `skipped` (`dependency X failed`). Cycles and unknown names are rejected at load. |
| `detect` | Probes deciding "already running". Also the readiness probe when `ready` is empty. |
| `ready` | Probes polled after launch until `timeout_seconds`. |
| `stop` | Graceful close rule, e.g. `{"window_title": "Jarvis dev"}`. Without it a managed PID is terminated. |
| `timeout_seconds` | Readiness wait, `0 < n <= 600` (default 30). |
| `retries` | Relaunches after a failed readiness, `0..5` (default 1). A missing executable is not retried. |
| `required` | Marks the task as essential. |

Probe kinds (`detect`, `ready`, `stop` accept only these): `http` (GET status < 400), `port`
(TCP connect), `process` (executable name), `window_title` (substring of a visible top-level
window), `command` (argv exits 0). Any probe of a set matching counts as a match.

Statuses: `launched`, `opened` (URL), `already_running`, `skipped`, `stopped`, `failed`, `timeout`.
Launchers such as `code` and `wt` exit 0 immediately; only a non-zero exit is treated as a failure.

## No duplicates

`start` checks, per task and in this order:

1. **`detect` probe matches** (also for URL tasks): `already_running`, nothing launched. A
   `window_title` match brings the existing window to the front.
2. **A live managed PID**: the profile state holds a PID Jarvis launched earlier for this task and
   that process still exists with the recorded creation time (`abs(delta) <= 1 s`, so a reused PID
   does not count): `already_running` with `detail: "managed by Jarvis"` and the same `pid`. This
   covers tasks that have no `detect` probe. Without `psutil` on Linux it falls back to
   `os.kill(pid, 0)`; on Windows without `psutil` it reports not alive.
3. Otherwise the task is launched (or the URL opened) and its state entry overwritten.

A second `start` for the same profile waits for the first (per-profile lock) and then finds
everything running.

## Managed-only shutdown

Jarvis records the PID and creation time of every task it launches (state file under the runtime
data directory). `stop` acts only on those entries, in reverse dependency order: windows named by
`stop.window_title` are closed gracefully (`WM_CLOSE`, so unsaved-work prompts still appear),
otherwise the managed process tree is terminated after checking the creation time. Anything that
was already running before Jarvis started it is reported `skipped` / `not managed by Jarvis` and
left alone. `--force` additionally closes the matching windows of tasks Jarvis did not launch; it is
a HIGH-risk operation when requested by voice (see [desktop-control.md](desktop-control.md)) and
was never used in the runs below.

## Linux test coverage

No `psutil` on the dev host, no Windows APIs: the logic is tested with injected probes, spawner,
identity/alive and terminate functions.

- `tests/test_workspace.py`: parsing/validation, dependency order and concurrency, probes,
  retries/timeout, detect-based `already_running`, a live managed PID is not respawned and a dead
  or reused PID is relaunched, URL tasks honour `detect` (and still open without one),
  managed-only stop.
- `tests/test_workspace_cli.py`: drives `jarvis workspace start|status|stop` end to end with a
  real subprocess task: first start `launched` with a pid, second start `already_running` /
  `managed by Jarvis` with the same pid, `status` lists it, `stop` exits 0 (skipped on Windows).

Full suite at the time of writing: 781 tests OK (3 skipped).

## Windows run (real venv, real `config.win.json`)

2026-09-29, from WSL via `powershell.exe`, using `.venv\Scripts\python.exe` (Python 3.11.9),
`PYTHONPATH` on the worktree `src` and `--config config.win.json` **unchanged** (the workspace
commands do not open SQLite, so `memory.db_path` did not need moving). Command:
`python -m jarvis --config config.win.json workspace <start dev|start dev|status|stop dev>`. All
four commands exited 0. Before the run VS Code (Code.exe) and Ollama were already running (the
owner's own processes); no Windows Terminal or Task Manager was open. No Jarvis daemon or other
Python process was running. `--force` was not passed.

**First `start dev`** (`total_ms` 3405):

| Task | Status | elapsed_ms | pid |
|---|---|---|---|
| ollama | `already_running` (detect http) | 16.0 | null |
| vscode | `already_running` (detect process) | 1219.0 | null |
| terminal | `launched` (`wt`, ready by window title "Jarvis dev") | 2407.0 | 20440 |
| task_manager | `launched` (`cmd /c start "" taskmgr`, ready by process) | 3391.0 | 2508 |

**Second `start dev`** (`total_ms` 1543): all four tasks `already_running`
(ollama 32.0 ms, vscode 1500.0, terminal 1547.0, task_manager 1532.0), no pid. **The second start
launched nothing.** On Windows this went through rule 1 (every task in `dev` has a `detect` probe);
rule 2 (`managed by Jarvis`) is covered by the Linux tests and CLI test, not by this run.
The 1.2-1.5 s on vscode/terminal is the `window_title`/`process` probe cost, not a launch.

**`status`**: `dev` lists `ollama, vscode, terminal, task_manager`. `managed` held
`terminal` (pid 20440, `create_time` null, stop `window_title` "Jarvis dev") and `task_manager`
(pid 2508, `create_time` recorded, no stop rule), plus two entries from earlier sessions
(`vscode` pid 18664 and `processes` pid 21876, dated about 4.4 days before this run).

**`stop dev`**:

| Task | Status | Detail |
|---|---|---|
| task_manager | `skipped` | `already exited` |
| terminal | `stopped` | `closed 1 window(s)` |
| vscode | `skipped` | `closed 0 window(s)` (stale entry from an earlier session; the owner's running VS Code was not touched) |
| ollama | `skipped` | `not managed by Jarvis` |

### Honest reading

- The guarantee held: the owner's VS Code and Ollama were never launched by Jarvis and were left
  running; `stop` only touched what Jarvis had launched.
- **Task Manager was not closed by `stop`.** It is launched through `cmd /c start`, so the
  recorded PID (2508) is the short-lived `cmd` wrapper, which had already exited; the `dev`
  profile has no `stop` rule for it. The Task Manager window that opened (pid 11692, elevated)
  stayed open, and closing it from the automated shell was denied, so it was left for the owner to
  close. `config.win.json` was not edited; a `stop: {"window_title": "..."}` rule would fix this but
  the title is locale-dependent (`Administrador de tareas` on this machine).
- `terminal` was recorded with `create_time: null` because `wt.exe` is a launcher that exits
  immediately; it is stopped by window title, not by PID, so this is harmless. Such an entry never
  counts as a live managed PID.
- Stale entries from earlier sessions stay in the state file until a `stop` pops them.

### Pending owner observation

An automated run cannot see the screen. **Not verified:** that the VS Code window was brought to
the front on start, and that the Windows Terminal window titled "Jarvis dev" visibly opened and
closed as expected. The evidence is the probe results (`window_title` matched after launch,
`closed 1 window(s)` on stop), not a visual check.
