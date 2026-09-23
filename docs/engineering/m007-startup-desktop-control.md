# M007 — Iron Man startup & desktop control

Status: implemented and verified on the target laptop (2026-09-23) except for the
owner-only checks listed at the end. Test suite: **562 passed, 3 skipped**
(baseline before M007: 474 passed, 3 skipped).

## What it does

```
sign-in ─► Jarvis Sentinel (pythonw, no window, ~1% of one core)
             │  mic ─► triple-clap detector      (numpy, local)
             │  Ctrl+Alt+J global hotkey          (RegisterHotKey)
             │  optional "hey Jarvis"             (openWakeWord, off by default)
             │  127.0.0.1 control socket          (jarvis activate|sleep|status|quit)
             ▼ gesture
     chime (immediately) ─► music fades in ─┬─► build + start runtime (thread)
                                            ├─► workspace profile (VS Code, terminal, services)
                                            └─► wait for cloned voice
     truthful welcome in the cloned voice, music ducked ─► music fades ─► voice loop
     "Jarvis, a dormir" ─► runtime stopped, back to the sentinel
```

The clap listener is closed while Jarvis is awake, so the startup music and
Jarvis's own voice can never re-trigger it; after a session it resumes with a
cooldown. A second trigger during startup joins the running sequence; a trigger
after it finished is ignored (idempotent). `jarvis sleep`/`quit` cancel cleanly.

## Install / enable

`jarvis` is not an installed command. In PowerShell, from the repo, every
`jarvis …` below means:

```powershell
cd \\wsl.localhost\Ubuntu\home\manuelxose\workspace\jarvis
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m jarvis <command> --config config.win.json
```

| Step | Command |
|---|---|
| Try the clap detector live | `jarvis claps test --seconds 30 --config config.win.json` |
| Calibrate for your mic (4 s silence, then clap 3+3) | `jarvis claps calibrate --config config.win.json` → `%LOCALAPPDATA%\jarvis\clap_calibration.json` |
| Run the sentinel in a console | `jarvis daemon --config config.win.json` |
| Start at every sign-in (no terminal) | `jarvis autostart install --config config.win.json` |
| Disable autostart | `jarvis autostart remove` |
| Wake / sleep / inspect / stop a running sentinel | `jarvis activate` · `jarvis sleep` · `jarvis status` · `jarvis quit` |
| Launch / close a workspace | `jarvis workspace start dev` · `jarvis workspace stop dev [--force]` |
| List tools and their risk class | `jarvis tools --config config.win.json` |

Autostart is a per-user Startup-folder shortcut (`Jarvis Sentinel.lnk` →
`pythonw.exe scripts\jarvis_daemon.pyw`), visible in Task Manager › Startup apps.
No scheduled task, no elevation, no UAC. Paths are stored as `\\wsl.localhost\…`
UNC paths (never a temporary `pushd` drive letter). Logs: `%LOCALAPPDATA%\jarvis\logs\daemon.log`;
last startup timings: `%LOCALAPPDATA%\jarvis\startup-report.json`.

## Configuration (`config.win.json`, secrets stay in `config.local.json`)

```jsonc
"claps":   { "enabled": true, "sensitivity": 0.5, "window_seconds": 2.0, "cooldown_seconds": 5.0,
             // also: min_peak_dbfs, min_hf_ratio, min_gap_seconds, max_gap_seconds, max_gap_ratio,
             // quiet_before_seconds, quiet_after_seconds, max_decay_seconds, confidence_threshold
           },
"welcome": { "owner_name": "Manuel",
             "music_path": "",          // your own, lawfully obtained audio file (mp3/flac/wav)
             "music_url": "https://www.youtube.com/watch?v=BN1WwnEDWAM", // opened in the browser only if no file
             "music_volume": 0.55, "duck_volume": 0.12, "after_welcome": "fade",   // or "restore"
             "welcome": "{greeting}, {name}. Todos los sistemas están operativos. …",
             "welcome_variants": { "morning": "…", "afternoon": "…", "evening": "…" },
             "degraded": "{greeting}, {name}. Estoy en marcha, pero con limitaciones: {issues}. …",
             "activation_sound": "",    // empty = synthesized chime
             "essential": ["configuration","storage path","audio input","audio output","STT","TTS","fast model"] },
"daemon":  { "hotkey": "ctrl+alt+j", "wake_word": false, "control_port": 47811,
             "min_free_vram_mb_for_ollama": 5000 },
"desktop": { "authorized_scopes": ["/home/user/workspace"], "trusted_operations": [], "apps": {} },
"workspace": { "default_profile": "dev", "startup_profile": "dev", "profiles": { "dev": {…}, "projects": {…} } }
```

**Music:** Jarvis never downloads anything. Put your own file somewhere (e.g.
`C:\Users\Admin\Music\jarvis-startup.mp3`) and set `welcome.music_path`. Without a
usable file it opens `music_url` in the browser (that cannot be ducked).

**Workspace task fields:** `command` or `url`, `cwd`, `depends_on`, `detect` / `ready` /
`stop` probes (`http`, `port`, `process`, `window_title`, `command`), `timeout_seconds`
(≤600), `retries` (0–5), `required`, `window` (`gui` | `hidden` | `new_console`).
Independent tasks start concurrently; dependents wait. Anything detected as
already running is left alone and never closed by `stop` (unless `--force`, which is
HIGH risk by voice). Jarvis records the PID + creation time of what it launched
(`workspace-state.json`) so a later process shuts down only its own launches,
immune to PID reuse. VS Code and Windows Terminal are closed with `WM_CLOSE`
(unsaved-work prompts still appear). The `dev` profile opens VS Code through
the WSL remote exactly as you do (`code --remote wsl+Ubuntu …`).

## Voice commands (examples)

| Say | Path |
|---|---|
| "Jarvis, arranca mi entorno de desarrollo" / "apaga mi entorno de desarrollo" | fast command → `workspace` |
| "Jarvis, abre mis proyectos" | fast command → profile `projects` |
| "Jarvis, reinicia Hermes" | fast command → `service_restart` |
| "Jarvis, ¿qué está usando la memoria de la GPU?" / "¿cómo va el sistema?" | fast command → `gpu_processes` / `system_stats` |
| "Jarvis, cancela la operación" | cancels running *operations* (see below) |
| "Jarvis, a dormir" | back to the clap sentinel |
| "Arranca el backend y abre VS Code", "abre el proyecto en el que estaba trabajando", "cierra todo lo relacionado con ese proyecto" | desktop planner (DeepSeek → JSON plan over the tool registry, Ollama fallback) |
| "Mira este error y arréglalo" | Hermes, with the last failed command's output and recent VS Code folders attached |

References are resolved from `desktop_context.json` (last project/profile/file,
last command + output tail) and VS Code's own recent-folders state. If an essential
referent is missing, the planner asks one targeted question. Long plans give one
short spoken progress update (after 4 s), not one per step.

**Narration vs execution:** barge-in / "para" stops the *speech*. Mutating tools run
on their own execution token, shielded from the turn, so an interrupted reply never
leaves a half-written file (writes are atomic, `os.replace`). Only "cancela la
operación" (`cancel_operations`) stops running operations.

## Desktop tools and risk policy

| Risk | Behaviour | Tools (per-call risk can escalate) |
|---|---|---|
| LOW | runs immediately | `window_focus`, `window_manage` (min/max/snap/graceful close), `virtual_desktop`, `windows_list`, `app_close` (graceful), `open_application` (+Start Menu), `vscode_open`, `terminal_open`, `file_search`, `file_read`, `system_stats`, `process_list`, `gpu_processes`, `screenshot`, `volume_level`, media keys, `web_search`, `open_url`, read-only commands (`git status`, `nvidia-smi`…) |
| MEDIUM | runs automatically when every path it touches is inside `desktop.authorized_scopes` (or the tool is in `trusted_operations`); otherwise asks | `file_write` (backup first), `file_move`, `file_delete` (→ Recycle Bin), `run_command` for known dev programs (git, npm, pytest, python, docker…), `workspace start/stop`, `service_restart` |
| HIGH | spoken confirmation **immediately before every execution**; never cached, never pre-authorisable | permanent delete, move with overwrite, force-close/kill, credential files (`.env`, keys, `config.local.json`…), `workspace stop --force`, destructive/unknown commands (`rm`, `git push --force`, `reset --hard`, `format`, `reg`, `netsh`, shells…) |

Confirmation: Jarvis says what will happen ("Atención: voy a borrar … de forma
permanente. ¿Confirmas? Di «confirmo» o «no»") and takes your next utterance; only a
short explicit yes counts; silence (20 s) = no. Requests that originate from Hermes /
agent output are tagged `origin=agent` and never benefit from `trusted_operations`;
no tool can change permissions; scopes and trust come only from the config file.
UAC is never bypassed (Jarvis has no elevation path at all).

Audit log: `%LOCALAPPDATA%\jarvis\audit.jsonl`, one line per decision (tool, risk,
origin, decision auto/confirmed/declined/denied, outcome, ms). Arguments are
redacted: secret-looking keys → `<redacted>`, file contents → `<N chars>`.

## Measurements on the target laptop (i7-12700H, RTX 3070 Laptop 8 GB, 2026-09-23)

| Metric | Result | How |
|---|---|---|
| Sentinel idle CPU / RAM | **0.9 % of one core (0.045 % of the machine), 54 MB** | psutil over 30 s, headless pythonw daemon |
| Detector cost | 0.04–0.06 % of a core (offline), 0.31 % live incl. PortAudio callback | `clap_eval.py`, `jarvis claps test` |
| Detection latency (last clap → gesture) | **≈ 510 ms** (waits 1.25× your clap spacing for a 4th clap) | acoustic loopback |
| Output stream open → first callback | ≈ 130 ms | probe; the chime now plays before the runtime is built |
| Runtime build (imports, models setup) | 3.0–4.6 s, now in a worker thread, overlapped with music | probe |
| Services started (supervisor) | **9 ms** (was 12 s: the supervisor killed and respawned the warming clone worker twice — fixed) | probe |
| Cloned voice ready (worker load) | **18.4 s** after build (36.5 s before the fix) | probe |
| Full sequence (before the two fixes above) | chime 0.5 s*, music 0.7 s, workspace ready 2.6 s, welcome spoken 45 s, listening 48.7 s | real daemon run via `jarvis activate` |
| VS Code (WSL) + WT terminal ready | 1.5–2.1 s; second start 155 ms, launches nothing | `jarvis workspace start dev` ×2 |
| VRAM | desktop 1.1 GB idle; clone worker **≈ 4.2 GB** once loaded (5.3 GB total) | nvidia-smi |

\* relative to the sequence start; the runtime build (≈3 s) used to run before it.
That ordering is fixed; the new end-to-end timings from gesture are written to
`startup-report.json` (`timings_ms_since_gesture`) but were **not re-measured with
audio** (see pending).

With ~4.2 GB held by the voice clone, a 7B Ollama model does not fit beside it:
the runtime skips the Ollama preload when free VRAM < `min_free_vram_mb_for_ollama`
(default 5000) and the local model still loads on demand if DeepSeek fails.

### False activations (real audio)

| Input | Duration | Activations |
|---|---|---|
| Owner speech recordings (offline, max sensitivity) | 54 s | 0 |
| Music track in `voice_samples/sample3.wav` (offline, max sensitivity) | 190 s | 0 |
| Synthetic rock beat (snare every beat) through the speakers → mic | 60 s | 0 |
| Music file through the speakers → mic | 120 s | 0 |
| Owner speech through the speakers → mic | 54 s | 0 |

The mic kept capturing during playback (median −27 to −38 dBFS). Speech-to-text
*while music is playing* was not tested; the live voice loop was exercised right
after the startup sequence (music already faded) and transcribed spoken commands.

### Detection with the real mic

Synthetic claps played through the laptop's own speakers into its own mic array:
5/5 and 3/5 detected in two duplex runs; partial through the separate-process
path. The Intel Smart Sound array applies echo cancellation and transient
suppression designed to remove the laptop's own speaker output, and it reduced
the claps' high-frequency share to 0.24–0.47 (hence the default
`min_hf_ratio` 0.2, and calibration now tunes it; speech measured 0.005–0.08).
**This proxy is not the acceptance test — your real claps are.**

## Pending owner validation (not claimed as done)

1. **Real claps:** run `jarvis claps calibrate`, then `jarvis claps test` and clap
   three times from your usual position, at least 10 times; note detections.
2. **Full audiovisual startup with speakers:** set `welcome.music_path` to your
   track, run `jarvis daemon` (or `activate`), and listen for: chime within ~0.3 s,
   music ducking under the welcome, fade after it, and the welcome in your cloned
   voice. Check `startup-report.json`.
3. **Sign-in autostart:** `jarvis autostart install`, sign out/in, then
   `jarvis status` (WSL must boot for the `\\wsl.localhost` path; not yet tested
   across a real sign-in).
4. **Confirmation by voice:** ask for something HIGH risk (e.g. "borra
   definitivamente X") and answer "confirmo"/"no".
5. Volume: `volume_level` uses 2 % media-key steps without `pycaw`; install `pycaw`
   in the venv for silent exact levels.

## Known limits

- Clap discrimination is signal-based; an isolated three-hit drum fill in otherwise
  quiet music could still match. Mitigations: regular spacing, quiet before/after,
  exactly three, cooldown.
- A confirmation that times out while the loop is still waiting for speech leaves
  the next utterance to be read as an (ignored) answer.
- `screenshot` saves the image and describes open windows; image understanding
  needs a vision model (not in this milestone).
- Virtual desktops use the documented Win+Ctrl shortcuts (Windows has no public
  switch API).
