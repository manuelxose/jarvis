# Desktop control

Jarvis controls the Windows desktop through a registry of tools behind one
`ToolGateway`. Every call is validated against the tool's argument schema,
classified by risk, audited, and (when needed) confirmed by voice before it runs.

## Voice commands

Say "Jarvis, …" (right after the welcome, and for a few seconds after each reply,
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
