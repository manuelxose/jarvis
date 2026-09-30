# Desktop control

Jarvis controls the Windows desktop through a registry of tools behind one
`ToolGateway`. Every call is validated against the tool's argument schema,
classified by risk, audited, and (when needed) confirmed by voice before it runs.

## Voice commands

Say "Jarvis, …" (the first command after the welcome needs it; for a few seconds after each reply,
the wake word is optional).

| Say | Route |
|---|---|
| "arranca / apaga mi entorno de desarrollo", "abre mis proyectos" | fast command → `workspace` |
| "abre el proyecto jarvis", "abre una terminal" | fast command → `project_open`, `terminal_open` |
| "minimiza / maximiza / restaura / cierra chrome", "cambia a VS Code" | fast command → `window_manage`, `app_close`, `window_focus` |
| "¿cómo va el sistema?", "¿qué está usando la GPU?", "actividad de red" | fast command → `system_stats`, `gpu_processes`, `network_stats` |
| "reinicia hermes / ollama / backend" | fast command → `service_restart` |
| "haz una captura", "sube el volumen", "abre spotify" | fast command → `screenshot`, `volume_*`, `open_application` |
| "para / baja / sube la música" | fast command → `music` |
| "cancela la operación" | cancels running operations (commands, launches) |
| "a dormir" | ends the session; claps work again |
| "reiníciate" | restarts the background listener |
| "apágate" | stops the listener completely, after a spoken "confirmo" |
| "arranca el backend y abre VS Code", "cierra todo lo de ese proyecto" | desktop planner: the LLM returns a JSON plan over the tool registry |
| "mira este error y arréglalo" | Hermes, with the last failed command's output attached |

References such as "ese proyecto" are resolved from `desktop_context.json` (last
project, profile, file and command) and VS Code's recent folders. If an essential
referent is missing, the planner asks one targeted question.

## Risk policy

| Risk | Behaviour | Examples |
|---|---|---|
| LOW (read-only / reversible) | runs immediately | window focus/manage, list windows, open app/URL, VS Code, terminal, file search/read, system and GPU stats, screenshot, volume, web search, read-only commands (`git status`, `nvidia-smi`) |
| MEDIUM | runs automatically when every path it touches is inside `desktop.authorized_scopes` (or the tool is in `desktop.trusted_operations`); otherwise asks | `file_write` (backup first), `file_move`, `file_delete` (to the Recycle Bin), `run_command` for known dev programs (git, npm, pytest, python, docker…), `workspace`, `service_restart` |
| HIGH | spoken confirmation immediately before **every** execution; never cached or pre-authorised | permanent delete, move with overwrite, killing a process, credential files (`.env`, keys, `config.local.json`), `workspace stop --force`, destructive or unknown commands (`rm`, `git push --force`, `reset --hard`, shells…) |

Confirmation: Jarvis says what will happen and waits for your next utterance; only
a short explicit "confirmo" counts, and 20 s of silence means no. Requests that
originate from an agent (Hermes, planner output) are tagged `origin=agent` and never
benefit from `trusted_operations`. No tool can change permissions; scopes and trust
come only from the configuration file. Jarvis has no elevation path, so UAC is
never bypassed.

Audit log: `%LOCALAPPDATA%\jarvis\audit.jsonl`, one line per decision (tool, risk,
origin, decision, outcome, duration). Secret-looking keys are replaced by
`<redacted>` and file contents by their length.

## Workspaces

A workspace profile (`workspace.profiles` in the config) is a set of tasks started
and stopped together. Task fields:

| Field | Meaning |
|---|---|
| `command` or `url` | what to launch |
| `cwd`, `window` | working directory; `gui`, `hidden` (logged to a file) or `new_console` |
| `depends_on` | tasks that must be ready first; independent tasks start concurrently |
| `detect`, `ready`, `stop` | probes: `http`, `port`, `process`, `window_title`, `command` |
| `timeout_seconds` (≤ 600), `retries` (0–5), `required` | robustness |

Anything already running is left alone and never closed by `stop` (unless
`--force`, which is HIGH risk by voice). Jarvis records the PID and creation time
of what it launched, so a later process only stops its own launches, immune to
PID reuse. VS Code and Windows Terminal are closed gracefully (`WM_CLOSE`), so
unsaved-work prompts still appear.

## Known limits

- Clap discrimination is signal-based: a loud isolated two-hit pattern can still
  match. Regular spacing, quiet before the first clap, calibration and the cooldown
  are the defences.
- `screenshot` saves the image and describes the open windows; it does not
  understand the image.
- Virtual desktops use the Win+Ctrl shortcuts (Windows has no public API).
- Without `pycaw`, volume changes use 2 % media-key steps.

## Windows evidence

2026-09-29, driven from WSL through `powershell.exe` with the real Windows venv
(`.venv\Scripts\python.exe`, Python 3.11.9, `PYTHONPATH` on the worktree `src`). A one-off driver
(kept in `%TEMP%\s04`, not in the repo) built the gateway exactly as the runtime does
(`_build_tools(config, confirmer=VoiceConfirmer(...))` + `_register_desktop_tools`) and called
`ToolGateway.execute` with `origin="owner"`.

**Config derivation.** Loaded `config.win.json` unchanged except `desktop.authorized_scopes`
(now `%TEMP%\s04\scope`) and `LOCALAPPDATA` (now `%TEMP%\s04\appdata`, so the audit log is
`%TEMP%\s04\appdata\jarvis\audit.jsonl`, not the owner's). Every other section (models, providers,
workspace) was untouched. **Omitted:** the voice loop, microphone, TTS and Hermes (the driver only
prints the prompt `VoiceConfirmer` would speak, and answers it by script), and the LLM planner.
This validates the tools, gateway policy, confirmer and audit on real Windows, not the spoken loop.

| Tool | Risk | Decision | ok | ms | Result |
|---|---|---|---|---|---|
| `open_application` notepad | reversible | auto | yes (returns a plain string) | 172 | `Abriendo notepad, señor.` |
| `window_focus` notepad | reversible | auto | yes | 891 | `Mostrando debug.log: Bloc de notas.` |
| `volume_level` 150 | reversible | rejected by validation | error | 0 | `level out of range`; no key was pressed |
| `system_stats` | read_only | auto | yes | 578 | CPU 3 %, memory 87 %, GPU 0 %, 72 °C |
| `gpu_processes` | read_only | auto | yes | 109 | dwm, explorer, ShellExperienceHost, Nahimic3; 94 of 8192 MB |
| `app_close` notepad (graceful) | reversible | auto | yes | 688 | `Cerrando: debug.log: Bloc de notas.` |
| `file_delete` in scope (recycle) | medium | auto, nothing spoken | yes | 562 | sent to the Recycle Bin |
| `file_delete` permanent, answer "no" | high_risk | declined | `ToolPermissionDenied` | 1 | file kept |
| `file_delete` permanent, answer "confirmo" | high_risk | confirmed | yes | 1 | file removed |
| `file_delete` outside scope (recycle), answer "no" | medium | declined | `ToolPermissionDenied` | 1 | file kept |

Each permanent delete spoke the prompt again (never cached):
`Atención: voy a borrar el archivo …\keep_me.txt de forma permanente, sin papelera. ¿Confirmas? Di «confirmo» o «no».`
The out-of-scope MEDIUM delete also asked, then `Entendido, no lo hago.` on "no".

Audit lines for the delete confirm/decline (paths shortened to `…\s04`):

```
{"tool": "file_delete", "risk": "medium", "origin": "owner", "args": {"path": "…\\s04\\scope\\recycle_me.txt"}, "decision": "auto", "outcome": "ok", "ms": 562.0}
{"tool": "file_delete", "risk": "high_risk", "origin": "owner", "args": {"path": "…\\s04\\scope\\keep_me.txt", "permanent": true}, "decision": "declined"}
{"tool": "file_delete", "risk": "high_risk", "origin": "owner", "args": {"path": "…\\s04\\scope\\keep_me.txt", "permanent": true}, "decision": "confirmed", "outcome": "ok", "ms": 0.0}
{"tool": "file_delete", "risk": "medium", "origin": "owner", "args": {"path": "…\\s04\\outside.txt"}, "decision": "declined"}
```

Cleanup: no `notepad` process existed before the run; after it, none did. The driver only
touched the notepad it launched and files it created under `%TEMP%\s04`; the recycled test file
is in the owner's Recycle Bin.

### Honest reading

- The notepad window title was `debug.log: Bloc de notas`, not an empty document: the Windows 11
  Store Notepad restored an earlier tab. It was the window the driver had just launched (no notepad
  existed before), closed gracefully (`WM_CLOSE`), and it is gone. Restoring the previous session
  is Notepad's behaviour, not Jarvis's; I did not verify that explanation beyond the process check.
- `open_application` returns a plain string rather than a `ToolResult`; the gateway passes it through
  and the audit line is correct (`outcome: ok`). The driver had to handle both shapes.
- An argument that fails schema validation (`volume_level` 150) raises before anything runs and writes
  **no audit line**; only allowlist denials, declines, and executed calls are audited.
- The recycle path went through `win32com.shell`, so a real Recycle Bin entry was made.

## Planner evidence

2026-09-29, WSL → `powershell.exe` → Windows Python 3.11.9 in the existing
`.venv`, with `PYTHONPATH` pointing at this M007 worktree's `src`. Driver:
`.gsd/exec/s05_driver.py` (throwaway, ignored); recorded Windows runs:
`.gsd/exec/1dc40e55-a117-4faf-9bee-1206f5c0e1a6.stdout` and
`.gsd/exec/26a170a7-3033-401c-8e17-071b7797bad6.stdout`. Invocation:

```sh
WINROOT=$(wslpath -w "$PWD")
powershell.exe -NoProfile -Command "Set-Location '$WINROOT'; \$env:PYTHONPATH='$WINROOT\src'; & '$WINROOT\..\..\.venv\Scripts\python.exe' '.gsd\exec\s05_driver.py'"
```

The driver loaded the worktree's `config.win.json` via `load_config`, built its
**actual** `_build_model_chain` (`ollama` only in this worktree), and used
`build_workspace`, `_build_tools(config, VoiceConfirmer(...))` and
`_register_desktop_tools`. No fake model was substituted. The plan was captured
as raw LLM output and replayed verbatim into `DesktopPlanner.handle` *only*
after a per-step tool/argument allowlist check. `LOCALAPPDATA` and
`desktop.authorized_scopes` pointed to `%TEMP%\jarvis-s05\appdata` and
`%TEMP%\jarvis-s05\scope`. To avoid launching the owner's real Ollama,
terminal and task manager, the in-memory `dev` profile was additionally replaced
with a single managed Python sleep task; the committed config was not edited.
The folder and deletion target were throwaway items in that scope. The worktree
has no `config.local.json` override, so a cloud-primary configuration from a
separate checkout was **not** used or claimed here.

| Request | Real LLM plan / handling | Elapsed | Spoken text / audit |
|---|---|---:|---|
| `arranca el backend y abre VS Code en …\scope\project` | First response malformed JSON (`JSONDecodeError`), retry proposed `run_command` (`dotnet restore && dotnet build`) instead of `workspace` + `vscode_open`; rejected by driver without execution | 61,891 ms; retry 10,766 ms | No spoken text; no tool audit |
| `Arranca el perfil dev y abre … con vscode_open` | Proposed `project_open(name=dev)` then `vscode_open(path=…\scope\project)`; rejected because opening a project is not starting the harmless profile | 9,187 ms | No spoken text; no tool audit |
| `abre el proyecto` | Proposed `project_open(name=…\scope\project)` or `project_open(name=project)` instead of a clarifying question; driver rejected the unapproved action | 6,109 ms; retry 3,657 ms | No question spoken; no tool audit |
| `borra permanentemente …\keep_me.txt y después abre VS Code en …\project` | First run proposed `file_delete(permanent=true)` then `vscode_open`; replayed into real planner/gateway, scripted `no` declined deletion and stopped the plan; second run's otherwise equivalent JSON was malformed | 12,719 ms; retry 17,000 ms | `Atención: voy a borrar … de forma permanente … ¿Confirmas?`, `Entendido, no lo hago.`; audit `{"tool":"file_delete","risk":"high_risk","origin":"agent","decision":"declined"}`; file retained |

Representative raw model JSON (full responses, including the malformed HIGH
retry, are in the recorded run outputs; paths below are shortened):

```jsonl
{"steps":[{"tool":"run_command","arguments":{"command":"dotnet restore && dotnet build --configuration Release","cwd":"…\\scope\\project","timeout_seconds":1200}}],"say":"El backend se ha arrancado y se abrirá VS Code en el directorio especificado.","ask":null}
{"steps":[{"tool":"project_open","arguments":{"name":"dev"}},{"tool":"vscode_open","arguments":{"path":"…\\scope\\project"}}],"say":"Perfil dev iniciado y carpeta abierta en VS Code.","ask":null}
{"steps":[{"tool":"file_delete","arguments":{"path":"…\\scope\\keep_me.txt","permanent":true}},{"tool":"vscode_open","arguments":{"path":"…\\scope\\project"}}],"say":"Archivo borrado, VS Code abierto.","ask":null}
```

**Historical T02 result (before T03 prompt/validation changes):** real-model two-tool demo and one-progress-update claim were **unverified**;
no workspace task or VS Code window was launched, hence there was no managed
PID/window to terminate (workspace `stop(dev)` reported `skipped`, managed state
empty). The only executed Windows gateway step was the declined HIGH-risk step;
the audit confirms `origin=agent`, consistent with the risk policy and
`DesktopPlanner.handle(... origin="agent")`. Malformed responses are caught by
`handle` with a rephrase request; unrelated but valid plans were deliberately
blocked by this evidence driver, not by the planner. The malformed response
was observed at `plan()` before `handle()`, so the rephrase path was not
verified by this live probe. The real planner's
clarification reliability needs improvement with this local model. Unit tests
in `tests/test_desktop_planner_loop.py` verify ordered tools, exactly one slow
progress message, confirmation and owner/agent trust separation with scripted
model output; they do not establish real-model planning accuracy. This probe
omitted VoiceLoop, microphone, TTS, audible confirmation and visual VS Code
observation; those remain pending owner observation.

### Planner retry evidence (T03)

2026-09-29, run from this M007 worktree through `powershell.exe` and Windows
`py -3.11` with `PYTHONPATH` set to the worktree's `src` (not an executable or
checkout outside this worktree). Reproduce with:

```sh
WINROOT=$(wslpath -w "$PWD")
powershell.exe -NoProfile -Command "Set-Location '$WINROOT'; \$env:PYTHONPATH='$WINROOT\src'; py -3.11 '.gsd\exec\s05_driver.py'"
```

The actual `config.win.json` Ollama model chain was used. `LOCALAPPDATA` and
authorized scopes were redirected to `.gsd/exec/s05-windows/`, and only the
in-memory `dev` profile was replaced with a harmless, managed Python sleep
process. The driver allowed execution only when each model argument matched
that profile and scoped folder/file, then replayed the *same captured raw model
responses* into `DesktopPlanner.handle`; it did not substitute a fake model for
planning. Full raw replies, timings, spoken strings, and audit lines are in
`.gsd/exec/t03-windows-final.log` (earlier rejected run in
`.gsd/exec/t03-windows.log`).

| Request | Observed configured-model result | Wall time | Gateway/audit |
|---|---|---:|---|
| `arranca el backend y abre VS Code en …/project` | First proposed `workspace(force=true)` and was rejected by the planner; retry returned `workspace(action=start, profile=dev)` then `vscode_open(path=…/project)` | 33,250 ms | Both ran in order; both audited `origin=agent`, `decision=auto`, `outcome=ok`. No progress sentence: replay of the validated plan was fast. |
| `abre el proyecto` | Deterministic clarification `¿Qué proyecto quieres abrir?`, no model call | <1 ms | No tools/audit. |
| `borra permanentemente …/keep_me.txt` | Actual model proposed one `file_delete(permanent=true)`; scripted answer `no` | 34,703 ms | Confirmation and decline text captured; audit `origin=agent`, `decision=declined`; file retained. |

The first generated plan was **not** executed. The final audit file also
contains entries from the earlier attempt because the isolated `LOCALAPPDATA`
was reused; the last three decision entries correspond to this run. Managed
workspace state was empty after `stop(dev)`, which reported `already exited`
(the harmless sleep process exited before cleanup). VS Code CLI may reuse an
existing instance, so no pre-existing VS Code window was closed. This is
Windows runtime/tool evidence, **not** audible or visual owner observation;
VoiceLoop, microphone and TTS were omitted. The slow-progress behavior is
verified by scripted-model integration tests, not by this fast replay.

**Final T03 rerun after context binding and grounded fallback:**
`.gsd/exec/t03-windows-grounded.log` records the configured Ollama chain returning
invalid/incomplete plans twice, followed by the planner's deterministic fallback
from the single configured `dev` profile and `last_project`. The safe replay ran
`workspace` then `vscode_open` (34,969 ms end-to-end), with both latest audit
entries `origin=agent`, `decision=auto`, `outcome=ok`; managed state was empty
after cleanup. This is **not** a model-generated valid plan: it is a narrowly
grounded recovery after real-model failures. Clarification remained immediate.
On this final rerun the model's HIGH plan had unsafe arguments and the driver
refused execution; the immediately preceding `.gsd/exec/t03-windows-bound.log`
records a real HIGH decline, `origin=agent`, and retained file. The previous
valid two-tool model-generated run remains in `t03-windows-final.log`.
No new audible/visual confirmation or slow-progress Windows proof is claimed.

### Pending owner observation

- **Volume:** `volume_level` was not executed with a valid level. `pycaw` is not installed in the
  Windows venv, so the tool falls back to 2 % media-key steps (down 50 times, then up) and the current
  level cannot be read or restored from PowerShell (Constrained Language Mode blocks `Add-Type`). Only
  the out-of-range rejection was exercised. Not claimed: that the volume actually changes.
- **Audible confirmation:** the spoken prompt was captured as text; that TTS speaks it, that the
  microphone hears "confirmo", and the `VoiceLoop` → `VoiceConfirmer.answer` hand-off on real audio are
  not verified here (covered by `tests/test_voice_loop.py` with fakes).
- **Visual:** that the Notepad window visibly came to the front on `window_focus` and closed on
  `app_close`; the evidence is the tool results and the empty process list.
- **Not run in the S04 tool probe:** `process_kill`, force `app_close`, and `origin="agent"` (the S05 planner probe above did exercise a declined `origin="agent"` call).
