# Cinematic startup (`jarvis startup`)

`jarvis startup` runs the startup sequence once in the foreground, without the daemon or the
voice loop: chime, then the owner's music, concurrent service init, a truthful welcome in the
cloned voice with the music ducked, and finally the printed `StartupReport` (JSON). It reuses the
same `StartupSequence` and `Mixer` as the daemon (`Sentinel._session`).

```
jarvis startup [--config config.win.json] [--use-fakes]
```

Exit code is `0` only when `phase` is `ready`; `degraded`, `failed` and `cancelled` return `1`.
`--use-fakes` builds the fake runtime with a temporary database and no audio device (no music, no
chime, no cached welcome). The daemon's `startup.progress` / `startup.degraded` hub events are unchanged.

## Report fields

All timings are milliseconds since the sequence started (`trigger`), not since the process started.

| Field | Meaning |
|---|---|
| `first_sound` | Chime submitted to the mixer. Target: under 300 ms. |
| `music_started` | Track decoded and playing (equals `first_sound` when no music is configured). |
| `services_ready` | Runtime built and `start()` finished (or timed out / failed). |
| `voice_ready` | Cloned voice reported ready or the wait timed out (`voice_ready_timeout_seconds`). |
| `welcome_spoken` | Welcome playback finished. |
| `interactive` | Final phase reached. |
| `phase` | `ready` (all essential components OK), `degraded` (welcome named the issues), `failed` (a required service failed). |
| `music` | `file`, `url`, `none`, `missing` or `error`. |
| `welcome_source` | `cache` (recorded cloned-voice welcome), `live` (cloned voice), `fallback` (SAPI spoke a truthful warning because the clone was not ready). |

The welcome only says "operativos" when there are no issues; a failed or timed-out required
service always lists the issue instead.

## Linux (`--use-fakes`)

`PYTHONPATH=src python3 -m jarvis startup --use-fakes --config config.json` (WSL2, python3 3.12, exit 0):

```
phase: ready   music: none   welcome_source: live   issues: []
first_sound 0.0   music_started 0.0   services_ready 28.2   voice_ready 28.3   welcome_spoken 29.5   (ms)
```

`first_sound` is 0.0 because there is no mixer with fakes; this run only proves the wiring and the
report shape. Regression tests: `tests/test_startup.py` (first sound under 300 ms and before
services even when init takes 1 s and the track load 0.5 s; required failure and services timeout
never say "operativos") and `tests/test_startup_cli.py`.

## Windows (real venv, real `config.win.json`)

Run from WSL through `powershell.exe` with the project's Windows venv
(`.venv\Scripts\python.exe`, Python 3.11.9) and `PYTHONPATH` pointing at the worktree `src`.
The real config was used unchanged except `memory.db_path`, moved to a Windows-local temp path:
SQLite over the `\\wsl.localhost` share fails with `database is locked` (the first attempt ended
`phase: failed`, `issues: ["la configuración", ...]` for that reason, and is not counted as a
timing run). No Jarvis daemon or other python process was running.

Captured report (2026-09-29, exit code 1):

| Field | Value |
|---|---|
| `phase` | `degraded` |
| `issues` | `["mi voz clonada"]` |
| `music` | `none` (`welcome.music_path` is empty in `config.win.json`; no track was invented) |
| `welcome_source` | `fallback` |
| `first_sound` | 1589.1 ms |
| `music_started` | 1589.2 ms |
| `services_ready` | 4545.1 ms |
| `voice_ready` | 12545.4 ms (the 8 s `voice_ready_timeout_seconds` wait expired) |
| `welcome_spoken` | 25473.1 ms |
| `interactive` | 25473.2 ms |

Welcome text: "Buenas noches, señor. Estoy en marcha, pero con limitaciones: mi voz clonada no está
disponible. Preparando tu entorno de trabajo. ¿En qué puedo ayudarte?"

Honest reading:

- **`first_sound` is NOT under 300 ms on Windows (1.59 s, consistently 1.59-1.79 s over three runs).**
  A separate probe on the same machine attributes it to a cold audio start: `import sounddevice`
  414 ms, importing the mixer 208 ms, opening the output stream 946 ms; `play_sfx` after
  `Mixer.prime()` takes 2.3 ms. `Mixer.prime()` existed but nothing called it.
  **Superseded:** the sentinel now primes the output stream before any activation (S06-T01); see
  [Windows daemon acceptance](#windows-daemon-acceptance-2026-09-30) for the warm daemon result.
  `jarvis startup` (this foreground command) still starts cold, so its `first_sound` stays high.
- **The welcome was degraded**: the cloned-voice worker was still warming up (`voice clone worker
  not ready (warming up)`), so the sequence spoke the truthful warning through SAPI instead of the
  cloned voice. `phase` is `degraded`, not `ready`. `services_ready` at 4.5 s is time-to-voice for
  the runtime; the clone itself did not become ready within 8 s.
- `welcome_spoken` includes the ~13 s of SAPI playback of the warning.

### Pending owner observation

An automated run cannot hear audio. These are **not verified** and need the owner to listen:
whether the chime is audible and pleasant, whether the music starts and sounds right (there was
no music to test: `music_path` is empty), and whether ducking under the welcome sounds right.
The cached cloned-voice welcome path (`welcome_source: cache`) was also not exercised because the
run had issues.

## Windows daemon acceptance (2026-09-30)

Sign-in-equivalent run of the real sentinel on the reference machine (MSI-MANUEL, RTX 3070 Laptop 8 GB),
without a terminal. It is **not** a reboot and not the owner's Startup folder. Raw evidence:
[`bench/windows-acceptance-2026-09-30.json`](bench/windows-acceptance-2026-09-30.json).

### Setup (what differs from the real configuration)

- Interpreter: the worktree-local `.venv-win` (Python 3.11.9: sounddevice, psutil, torch 2.8, faster-whisper,
  pywin32); the original checkout's venv was not used.
- Config: `config.win.json` with **only** three changes, written outside the repository
  (`%TEMP%\jarvis-t04\config.t04.json`):
  `memory.db_path` moved to a Windows-local path (SQLite over `\\wsl.localhost` reports `database is locked`),
  `daemon.control_port` 47911 (the owner's real shortcut targets 47811), and the `terminal` task removed from
  `workspace.profiles.dev` so no new window is opened. Providers, models, audio, STT, TTS, voice and the other
  workspace tasks (`ollama`, `vscode`, `task_manager`) are unchanged. `music_path` is empty, so **no music was played and
  nothing is claimed about music**.
- Startup folder: `APPDATA` pointed at `%TEMP%\jarvis-t04\appdata`; the real `jarvis autostart install`
  wrote the shortcut there and the driver launched exactly the shortcut's target and arguments. The owner's real
  Startup folder was only read (its `Jarvis Sentinel.lnk`, dated 2026-09-24, points at the original checkout and was not modified).
- Only the PIDs started by the run were managed; other Python/Code/Ollama processes were left alone.

### Replay

```powershell
$root = '\\wsl.localhost\Ubuntu\...\.gsd-worktrees\M007'          # the worktree
$env:PYTHONPATH = "$root\src"; $env:APPDATA = "$env:TEMP\jarvis-t04\appdata"
$py = "$root\.venv-win\Scripts\python.exe"; $cfg = "$env:TEMP\jarvis-t04\config.t04.json"
& $py -m jarvis autostart install --config $cfg     # shortcut -> pythonw.exe scripts\jarvis_daemon.pyw --config ...
& $py -m jarvis autostart status  --config $cfg
Start-Process "$root\.venv-win\Scripts\pythonw.exe" -ArgumentList "`"$root\scripts\jarvis_daemon.pyw`" --config `"$cfg`"" -PassThru
# in the daemon's config, control_port is 47911:
& $py -m jarvis status; & $py -m jarvis activate; & $py -m jarvis sleep; & $py -m jarvis quit
& $py -m jarvis autostart remove
```

`jarvis` reads `daemon.control_port` from `--config`; pass the same `--config` to `status`, `activate`, `sleep` and `quit`.
A second launch of the same command must exit with code 3 ("another Jarvis daemon owns port ...").

### Results

| Check | Result |
|---|---|
| Shortcut (temp APPDATA) | target `.venv-win\Scripts\pythonw.exe`, arguments `"…\scripts\jarvis_daemon.pyw" --config "…\config.t04.json"`, working directory the worktree; second `install` overwrites (one entry); `remove` returns true; status reflects each state |
| Launch without a terminal | `pythonw.exe` (PID 28632 shim + child 11088); no console; control socket reachable after **7.1 s** in state `sentinel`, `output_prime` `warming` → `ready` (log: "sentinel output primed" ~0.4 s after listening) |
| Singleton | second launch exit code **3** after ~3 s, first daemon still `sentinel` |
| Controlled activation | `activate` → `starting` → `active` in **15.6 s**; duplicate `activate` while active answered `ok` and did not start a second session |
| Report (`timings_ms_since_gesture`) | `first_sound` **39.3 ms**, `services_ready` 1199 ms, `voice_ready` 1240 ms, `workspace_ready` 1922 ms, `welcome_spoken` 15109 ms (SAPI warning), `interactive` 15110 ms |
| `phase` / issues | **`degraded`**, issue `["mi voz clonada"]`, `welcome_source: fallback`, `music: none` |
| Workspace | `ollama`, `vscode`, `task_manager` `already_running` (no new processes) |
| Voice loop | entered ("voice loop running — listening"); the first transcription of 1 s of silence took 15.7 s (Whisper first load on CUDA) |
| `sleep` → sentinel | back to `sentinel` and listening again 6.7 s after the command |
| `quit` and cleanup | daemon exited, control port free, no residual processes from this run, shortcut removed |

`first_sound` here is the report timestamp when `play_sfx` returned (not proof of audible output); the
callback-level measurement is in [performance.md](performance.md#windows-startup-and-idle-measurements-2026-09-30).
The warm daemon path is well under 300 ms; the previous 1.59 s cold path is closed for the daemon by S06-T01.

### Gaps found

- **Degraded welcome remains.** A control-socket activation has no first clap, so no speculative clone load
  started; the clone worker was cold when the sequence checked it (`voice cold -> warm` is logged after the welcome
  decision) and the truthful SAPI warning was spoken. The cached cloned-voice welcome (`welcome_source: cache`) was
  not exercised. Real clap activations start the speculative load and were not tested (see UAT).
- **Control reply stall during first STT load.** The `sleep` reply timed out (5 s client timeout) because the control
  socket did not answer while Whisper loaded; the stop only took effect when that transcription finished. Not fixed here.
- `audit.jsonl` gained 0 lines (no audited tool action ran: the welcome and the empty transcript execute none);
  no transcript text was retained in the evidence.

### Owner UAT (cannot be automated)

Not verified by this run and pending the owner: real sign-in with the actual Startup shortcut; microphone
triple/double clap while idle; audible chime, music (needs `welcome.music_path`) and ducking; the visual windows
opened by the full `dev` profile (including the `terminal` task removed here); the cloned voice welcome
(`welcome_source: cache` or `live`) instead of the SAPI fallback.
