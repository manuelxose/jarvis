# M007 verification record: Iron Man startup and desktop control

Date: 2026-09-30. Host: MSI-MANUEL (Windows 11, RTX 3070 Laptop 8 GB), Python 3.11.9 (`.venv-win` of the M007 worktree).
Raw Windows evidence: [`windows-acceptance-2026-09-30.json`](../bench/windows-acceptance-2026-09-30.json). Narrative and replay steps:
[performance.md](../performance.md#windows-startup-and-idle-measurements-2026-09-30) and
[startup.md](../startup.md#windows-daemon-acceptance-2026-09-30).

The scripted Windows run was a sign-in-equivalent launch of the Startup shortcut target (`pythonw.exe`), not a reboot, and had no
human at the microphone or speakers. The owner then ran a live session on the same laptop (microphone claps, speakers, spoken
commands); what was observed there is ticked in the [Owner UAT checklist](#owner-uat-checklist), and what was not is marked
**Owner UAT pending**. Numbers below are copied from the JSON
and the commands named in each row; none are estimated.

## Success criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Clap detection with zero false activations | Met |
| 2 | Activation to first sound under 300 ms | Met |
| 3 | Truthful welcome | Met |
| 4 | Workspace profiles | Partial |
| 5 | Desktop tools with risk tiers | Met |
| 6 | Test baseline | Met |
| 7 | Windows measurements | Partial |

### 1. Clap detection with zero false activations: Met

- `python scripts/clap_eval.py --synthetic`: 14 cases, 0 false activations, 0 missed positives, max latency 510 ms (the
  three-clap case waits for the third clap). Negatives covered: speech-like, music-like 90/120 bpm, TTS-like, typing knocks, quiet room.
- Offline `perf_bench` claps on Windows: 40/40 detected; confirmation after the last clap p50 47.97 ms, p95 54.55 ms; first
  clap candidate 30.0 ms. Detector latency only, no microphone or OS path.
- Shipped default is 2 claps (D045); 3 claps is configurable via `claps_required=3`, which satisfies the roadmap's "triple clap".
- Live (owner, 2026-09-30): real microphone claps activated the sentinel (2 claps, confidence 0.84-0.91, confirmed 30-90 ms after the last clap) and about 15 minutes of room speech and music caused no activation. The literal three-clap gesture was only exercised synthetically.

### 2. Activation to first sound under 300 ms: Met

- Live daemon, controlled `activate`: `first_sound` 39.3 ms since the gesture (the timestamp when `play_sfx` returned, not audible output).
- Bench, primed stream (as the daemon runs): gesture to first non-silent chime callback p50 6.6 ms, max 7.12 ms, n = 3.
- Bench, cold stream: 992.99 ms for the first trial (import plus stream open), then 83.73 and 86.76 ms. The first trial misses the
  target; the daemon primes the output stream before activation and does not take that path.
- Music ducking under the welcome and fade-out are covered by tests (`tests/test_startup.py::test_full_sequence_order_and_ducking`); the owner heard the chime and music start, and the music is cut on the first accepted command.

### 3. Truthful welcome: Met

- Health-gated welcome is covered by `tests/test_startup.py`: `test_failed_essential_never_claims_operational`,
  `test_degraded_welcome_is_never_served_from_cache`, `test_degraded_warning_uses_fallback_voice_after_short_deadline`,
  `test_service_timeout_is_degraded_not_hung`.
- Observed on Windows: the clone was cold, so the phase was `degraded`, `issues` named "mi voz clonada", `welcome_source` was
  `fallback` (SAPI spoke the warning), and `interactive` was reached 15110.4 ms after the gesture.
- Live (owner): after the first-clap race fix, a cold worker gave `issues: []` and the cloned-voice welcome was heard. Degraded wording with a required service stopped was not exercised live.

### 4. Workspace profiles: Partial

- Covered by `tests/test_workspace.py` and `tests/test_workspace_cli.py`: `test_already_running_is_not_duplicated_or_managed`,
  `test_concurrent_starts_of_same_profile_do_not_duplicate`, `test_stop_only_touches_managed_tasks`, `test_force_stop_is_explicit`,
  `test_second_start_launches_nothing_and_stop_succeeds`.
- Observed on Windows: activation found `ollama`, `vscode` and `task_manager` already running and launched nothing;
  `workspace_ready` at 1921.7 ms.
- Not verified: real GUI windows opening once and shutdown closing only managed PIDs (owner UAT).

### 5. Desktop tools with risk tiers: Met

- Covered by `tests/test_desktop_tools.py`, `tests/test_desktop_runtime.py`, `tests/test_desktop_planner_loop.py`:
  `test_high_risk_always_confirms_every_time`, `test_trusted_list_cannot_make_high_risk_automatic`,
  `test_secret_files_are_high_risk`, `test_agent_origin_does_not_inherit_trusted_operations` (planner steps run with origin
  `agent`, D046).
- Live (owner): a spoken delete asked for confirmation, silence declined it (audited `declined`) and "confirmo" ran it (audited `confirmed`, file removed).

### 6. Test baseline: Met

- Baseline at the start of M007: 474 tests. Now: `python3 -m unittest discover -s tests` ran 851 tests, OK (skipped=3).
- The count is quoted at the time of writing and grows as tests are added; it is not asserted by `tests/test_docs.py`.

### 7. Windows measurements: Partial

- Idle daemon (pid 28632 plus one child, 10 s window, 1 s samples): CPU mean 3.1 %, max 9.4 % of one core; RSS 78.9 MB.
  Measured 30 s after the socket came up, so still settling.
- Whole-GPU VRAM (`nvidia-smi`, before launch / idle / active / after `sleep` / after `quit`): 192 / 846 / 4124 / 5368 / 802 MB.
  This includes other applications, so daemon-attributable VRAM is not established.
- Singleton (second launch exits 3), no console window (`pythonw.exe`), control ready 7.1 s after launch, `sleep` returned to
  `sentinel`, `quit` left no residual processes and the port free.
- Not established: per-process VRAM; the measurement is not a reboot.

## Accepted limitations

- With a cold clone worker the welcome is spoken through the SAPI fallback (truthful, degraded); the cloned-voice welcome was not exercised.
- The control `sleep` reply timed out (`TimeoutError`) while the first Whisper load was in progress; the daemon still reached `sentinel` (6.7 s in the acceptance poll).
- No per-process VRAM is available, so no daemon VRAM figure is claimed.
- `first_sound` is the time `play_sfx` returned, not audible output; the primed callback figure is the closest proxy and still excludes the device buffer.
- The cold bench p50 (86.76 ms) is not representative: only the first trial (992.99 ms) is truly cold.
- The S06-T04 recovery placeholder is stale; disregard it in favour of this record.

## Follow-ups

- Repeat the run from a real reboot and sign-in with a microphone and speakers attached.
- Measure VRAM per process (for example `nvidia-smi --query-compute-apps`) to attribute daemon usage.
- Re-measure idle CPU after the settling period (several minutes after the socket comes up).
- Serve the `sleep` reply before the first Whisper load, or raise the control reply timeout.

## Owner UAT checklist

Live session 2026-09-30 on the reference laptop (owner present, real microphone and speakers, real `config.win.json` +
`config.local.json` merge with only the DB path and control port changed). Evidence: `%LOCALAPPDATA%\jarvis\daemon.log` and `audit.jsonl`.

- [ ] Real sign-in autostart from the Startup shortcut: **pending** (needs a reboot; not run).
- [x] Live microphone claps trigger activation (2 claps, confidence 0.84-0.91, confirmed 40-90 ms after the last clap). No activation from
      about 15 minutes of room speech and music.
- [x] Chime, music and the cloned-voice welcome heard (`issues: []` with a cold worker once the first-clap race was fixed); music ducks
      under the welcome and is cut on the first accepted command.
- [x] "Hey Jarvis, abre Spotify" and "a dormir" work; clapping again reactivates.
- [x] Spoken delete: the confirmation is asked, silence declines (audited `declined`), "confirmo" runs it (audited `confirmed`, file to the Recycle Bin).
- [ ] Degraded welcome naming a stopped required service: not exercised live.
- [ ] `jarvis workspace start dev` twice with visible windows: activation found the tasks already running and launched nothing (log); no visual check.
- [ ] VRAM per process: not available (`nvidia-smi` reports N/A under WDDM); daemon-attributable VRAM stays unestablished.

Defects found by this session and fixed: wake word "Hey Harvish" (two-edit slip after a greeting), queued speculation cancel evicting the
voice worker of a real activation, 8 s clone wait (now 20 s), lone spoken delete routed to the chat model (now the planner), planner
Linux paths / lone backslashes / unspoken `permanent`, Whisper loading after the welcome, Whisper caption hallucinations answered as commands,
delete confirmation reading a full path.
