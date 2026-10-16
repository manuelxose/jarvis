# Phase 4 Windows runtime and hardware acceptance (S09/T02, reconciled in S11)

Date: 2026-10-16. Host: WSL2 Ubuntu driving Windows through `powershell.exe`. Scope rule: nothing in another checkout was run or modified; no config values or secrets were printed; no user apps or sessions were started, stopped or restarted.

## Current status

**Current aggregate status: PASS**

The authoritative, machine-checked inventory is [phase-4-evidence/native-hardware.json](phase-4-evidence/native-hardware.json) (tests enforce that this table, the [report](phase-4-report.md) and [index.json](phase-4-evidence/index.json) agree with it). A row is PASS only with a retained, hash-verified, sanitized native sample; rows that also need a human observation need a durable human-observation record. Every row now carries both as required (samples from `hardware-run-scripts/native_rows.py` and `av_sync_live.py`; records under `phase-4-evidence/human-records/`), so the aggregate follows from the manifest. Fixture and Chromium suites ([test report](phase-4-test-report.md)) are not native evidence, and `native-programmatic` evidence is a hardware/operational check, not an audible-output or A/V acceptance.

Durable native sample: [native-samples/native-smoke-sanitized.json](phase-4-evidence/native-samples/native-smoke-sanitized.json) (allowlisted counts, exit codes and stages from `native-smoke.cjs`; digest recorded in the manifest).

| Manifest ID | Row | Status | Evidence scope | Requires | Evidence or exact blocker |
|---|---|---|---|---|---|
| `runtime-source-resolution` | 1 | PASS | native-programmatic | native | [runtime-source-resolution-sample](phase-4-evidence/native-samples/runtime-source-resolution.json) |
| `microphone-envelope` | 2 | PASS | native-programmatic | native | [microphone-envelope-sample](phase-4-evidence/native-samples/microphone-envelope.json) |
| `playback-envelope` | 3 | PASS | native-programmatic | native | [playback-envelope-sample](phase-4-evidence/native-samples/playback-envelope.json) |
| `audible-playback-human` | 3 | PASS | human-observation | human | [audible-playback-human-record](phase-4-evidence/human-records/audible-playback-human.json) |
| `cancellation-envelope` | 4 | PASS | native-programmatic | native | [cancellation-envelope-sample](phase-4-evidence/native-samples/cancellation-envelope.json) |
| `disconnect-recovery` | 5 | PASS | native-programmatic | native | [disconnect-recovery-sample](phase-4-evidence/native-samples/disconnect-recovery.json) |
| `reconnect-no-replay` | 6 | PASS | native-programmatic | native | [reconnect-no-replay-sample](phase-4-evidence/native-samples/reconnect-no-replay.json) |
| `startup-two-clap` | 7 | PASS | native-programmatic | native | [startup-two-clap-sample](phase-4-evidence/native-samples/startup-two-clap.json) |
| `startup-assets` | 8 | PASS | native-programmatic | native | [startup-assets-sample](phase-4-evidence/native-samples/startup-assets.json) |
| `native-overlay` | 9 | PASS | native-programmatic | native | [native-smoke-sample](phase-4-evidence/native-samples/native-smoke-sanitized.json) |
| `native-command-center` | 10 | PASS | native-programmatic | native | [native-smoke-sample](phase-4-evidence/native-samples/native-smoke-sanitized.json) |
| `stale-ordering-native` | 11 | PASS | native-programmatic | native | [stale-ordering-native-sample](phase-4-evidence/native-samples/stale-ordering-native.json) |
| `idle-cpu` | 12 | PASS | native-programmatic | native | [idle-cpu-sample](phase-4-evidence/native-samples/idle-cpu.json) |
| `idle-ram` | 13 | PASS | native-programmatic | native | [idle-ram-sample](phase-4-evidence/native-samples/idle-ram.json) |
| `reduced-motion-native` | - | PASS | native-programmatic | native | [reduced-motion-native-sample](phase-4-evidence/native-samples/reduced-motion-native.json) |
| `av-sync-human` | 14 | PASS | native-programmatic | native+human | [av-sync-human-sample](phase-4-evidence/native-samples/av-sync-human.json); [av-sync-human-record](phase-4-evidence/human-records/av-sync-human.json) |

Regenerate after a new run: `python docs/engineering/evidence/hardware-run-scripts/build_manifest.py` (it only promotes rows whose retained sample says PASS), then `.venv/bin/python -m unittest tests.test_phase4_docs`.

## Historical probe observations (unverified, not acceptance)

Everything below is narrative history from 2026-10-16 runs. The values were transcribed from session transcripts; the producing samples are not retained in the repository, so none of it is credited as acceptance. Status words in the tables are the original claims, shown as unverified. Do not use this section to determine the current status.

Initial autonomous run: no native/hardware scenario could be exercised safely and honestly at the time; nothing was credited from fixtures, mocks or Chromium.

## Probes performed (all read-only)

| Probe | Result |
|---|---|
| `powershell.exe` available | yes (`/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe`) |
| Windows Python | `C:\Python312\python.exe` 3.12.0 (also `py.exe`); worktree has no `.venv/Scripts`, so no worktree-local Windows interpreter. A duplicate environment was deliberately not provisioned |
| Windows Python deps (`importlib.util.find_spec`) | present: numpy, aiohttp. Missing: sounddevice, faster_whisper, fastapi, uvicorn, pydantic, webview, PySide6, pythonnet, pystray |
| Electron desktop shell | `desktop/node_modules` absent, so Electron is not installed in this worktree; dependencies were not installed (task boundary) |
| Existing Jarvis processes | `Get-CimInstance Win32_Process` filtered on `jarvis`: only the probing PowerShell itself. `Get-Process electron,jarvis*`: none. Nothing to protect or terminate |
| Audio hardware (WMI `Win32_SoundDevice`) | 9 devices reported Status OK, including Intel Smart Sound digital microphones and Realtek/USB audio. Existence only; capture/playback was not exercised |
| `config.win.json` (key names/types only) | `audio.input_device`/`output_device` null (system default); `activation.mode` wake_word; `claps.enabled` true; models.providers: one `ollama` entry, no credential field set; `welcome.music_path` and `welcome.music_url` both unset; `daemon.control_port` set |
| Temporary runtime config | not derived: no runnable Windows runtime exists, so no stateful-path override was needed. No temp state was created |

## Scenario matrix

| # | Scenario | Status | Evidence / blocker |
|---|---|---|---|
| 1 | Imported `jarvis` source resolves to this worktree under Windows Python | unverified: BLOCKED | `pydantic` (and other runtime deps) missing from `C:\Python312`; import would fail or fall back to another checkout. Action: provide a Windows venv/deps for this worktree, then check `jarvis.__file__` |
| 2 | Real microphone envelope reaches visual core | unverified: BLOCKED | `sounddevice` missing; devices exist (see above) but capture path cannot start. Human-observed verification would still be required |
| 3 | Actual playback envelope, start and end | unverified: BLOCKED | No playback runtime (`sounddevice`, TTS deps). Tones or simulated audio deliberately not substituted |
| 4 | Cancellation / stop during speech | unverified: BLOCKED | Depends on 3. Fixture-level coverage lives in the T01 matrix and is labelled fixture |
| 5 | Disconnected recovery | unverified: BLOCKED | No native Python core could be launched. Fixture/bridge coverage only (T01) |
| 6 | Reconnect without startup replay | unverified: PASS | Two consecutive SSE connections each received exactly one `core.snapshot`, same session id, no welcome/startup events; bad token -> 401 |
| 7 | Startup phase ordering and two-clap entry | unverified: BLOCKED | Needs microphone + core; `claps.enabled` is true in config, but the clap detector cannot run |
| 8 | Configured startup assets/app launch | unverified: NOT CONFIGURED | `welcome.music_path`/`music_url` unset; startup apps deliberately not launched |
| 9 | Native Overlay presentation | unverified: PASS | `native-smoke.cjs` overlay run PASS (renderer-mounted, shown, clean close code 0); evidence `docs/engineering/evidence/native-smoke.json` |
| 10 | Native Command Center presentation | unverified: PASS | Same smoke, `command-center` entry PASS |
| 11 | Stale telemetry / ordering natively | unverified: PARTIAL | Live SSE ordering observed (above); stale/out-of-order rejection remains fixture/unit-covered |
| 12 | Native idle CPU < 2% | unverified: UNMEASURED | No native process exists to measure. Method when unblocked: sample `Get-Process <pid>` `TotalProcessorTime` deltas over a 60 s idle window, per-process for Electron main/renderer/GPU and the Python core, divided by logical cores. Chromium numbers are not proof |
| 13 | Native idle RAM < 75 MB | unverified: UNMEASURED | Same process set; sum `WorkingSet64`/private bytes over the window. Not extrapolated from fixtures |
| 14 | Human-observed hearing/visual sync | unverified: UNVERIFIED | No human observation in an autonomous task |

## Omitted integrations

Microphone and playback devices, Electron shell, Python core on Windows, STT/TTS engines, configured model providers and credentials, welcome music assets, clap detection, user startup apps.

## Cleanup verification

No processes or files were spawned or created by this task other than a temporary probe script outside the worktree and this document. The only Jarvis-matching process observed was the probe PowerShell itself, which exited.

## Unblock checklist

1. Create a Windows venv for this worktree with the project requirements (`requirements.txt`, `requirements-tts.txt`); confirm `jarvis.__file__` is under this worktree.
2. `npm --prefix desktop ci` (installs Electron) in an environment where dependency installation is allowed.
3. Configure a welcome music asset and provider credentials in a local, untracked config; derive a temp config that only moves database/output/window-state paths.
4. Re-run scenarios 1-13 with bounded probes and owned-PID cleanup; add a human observer for 14.

## Limitations

Fixture and Chromium results in `phase-4-test-report.md` do not substitute for any row above. The Phase 4 status is the manifest aggregate shown in the current status section.


## Owner-present hardware run (2026-10-16, interactive; unverified history)

Host: i7-12700H (20 logical cores), Windows Python `%LOCALAPPDATA%\jarvis-m002-venv` (py3.11) with `PYTHONPATH` set to this worktree's `src`; `jarvis.__file__` resolved under the worktree. Daemon started hidden from temporary configs (claps disabled for rows 1/5/6, enabled with empty startup workspace profile for row 7; DB moved to local NTFS). No user apps were launched, `jarvis.db` and `config.win.json` were not modified. Machine-readable summary: [evidence/hardware-run-2026-10-16.json](evidence/hardware-run-2026-10-16.json); the probe scripts are in `docs/engineering/evidence/hardware-run-scripts/`.

| # | Scenario | Status | Evidence |
|---|---|---|---|
| 1 | Source resolves to this worktree under Windows Python | unverified: PASS | `import jarvis` -> `\\wsl.localhost\...\M011\src\jarvis\__init__.py`; sounddevice lists the real devices |
| 2 | Real microphone envelope reaches visual core | unverified: PASS (producer side) | 6 s capture from Intel Smart Sound through `CoreSignals.microphone`: 196 frames, 88 `core.signal` microphone events, 108 throttled (50 ms), 0 rejected; peak 0.095 speaking vs 0.0001 silence. Not yet observed rendered in the native window |
| 3 | Actual playback envelope, start and end | unverified: PASS | Real SAPI TTS through `AudioOutputQueue`+`StreamRenderer` on the G435 headset: 101-123 `output` events, `playback-start` then `playback-end` outcome `ended`. Owner confirmed it was audible. First run went to the system default (G435) and was not heard; the owner then wore the headset |
| 4 | Cancellation during speech | unverified: PASS (envelope) | `flush` at 1.48 s -> `playback-end` `cancelled` at the same instant, 0 `output` events after (run on the default device; audibility of the cut was not separately confirmed) |
| 5 | Disconnected recovery | unverified: PASS (after fix) | Native Overlay open, Core killed with `quit` and relaunched: bridge `connected -> reconnecting -> disconnected -> connected` and the UI `Idle -> Offline -> Idle` (recovered 2 s after the bridge reconnected). Before the fix it stayed on `Offline - State unavailable` indefinitely (see below) |
| 6 | Reconnect without startup replay | unverified: PASS | Two consecutive SSE connections each received exactly one `core.snapshot`, same session id, no welcome/startup events; bad token -> 401 |
| 7 | Two-clap entry and startup phase ordering | unverified: PASS | Instrumented (two runs): `activation.started` -> `candidate` (2000 ms) -> `activation.confirmed` -> `startup` acknowledged -> initializing -> announcing -> degraded, welcome playback 8.5-22.3 s, then `capture` active. Degraded only because Ollama and the cloned-voice worker are absent in this venv (R014 case). Owner-observed, not event-traced: a single clap did nothing, two claps started Jarvis, and a third clap afterwards did nothing |
| 8 | Configured startup assets/app launch | unverified: NOT CONFIGURED | `welcome.music_path`/`music_url` unset; startup apps deliberately not launched |
| 9 | Native Overlay presentation | unverified: PASS | `native-smoke.cjs` overlay run PASS (renderer-mounted, shown, clean close code 0); evidence `docs/engineering/evidence/native-smoke.json` |
| 10 | Native Command Center presentation | unverified: PASS | Same smoke, `command-center` entry PASS |
| 11 | Stale telemetry / ordering natively | unverified: PARTIAL | Live SSE ordering observed (above); stale/out-of-order rejection remains fixture/unit-covered |
| 12 | Native idle CPU < 2% | unverified: PASS (after fix) | Overlay connected in IDLE, 60 s windows, Electron process tree divided by 20 cores: 0.21 %, 0.22 %, 0.24 % (before the fix 1.65-2.06 %; reduced motion 0.05 %) |
| 13 | Native idle RAM < 75 MB | unverified: PASS (after fix) | Private bytes (USS) 65.5, 65.7, 65.7 MB (before the fix 125-127 MB; reduced motion 57.6 MB) |
| 14 | Human-observed hearing/visual sync | unverified: PASS (owner attestation) | Owner-attended run 2026-10-16: the native Overlay followed real playback (THINKING then SPEAKING) within 70 ms, and cancel during playback left SPEAKING within 60 ms; the owner heard the audible output (row 3) and accepts it as given. Owner-attested, not machine-measured - see "Owner attestation" below |

### Rows 12/13 root cause and fix
The GPU-less shell (`desktop/src/main.js:591-592` disables acceleration) has no vsync, so `requestAnimationFrame` fired ~238 times per second, and `CoreSvg` re-armed it every tick in every state, including ones with nothing to animate. Not caused by the UNC app path (identical from local NTFS); lowering the frame cap alone did not help, because the rAF wake-ups themselves were the cost.

Fix in `ui/src/components/jarvis/visual-core/VisualCore.tsx`: no loop for states with no motion (SUCCESS, ERROR, DISCONNECTED, STOPPING); frames are paced with a timer and rAF is used only to draw one frame; pace is 15 fps for breath (IDLE, LISTENING), 30 fps for rotation (THINKING, EXECUTING) and ~80 fps cap for live audio level (SPEAKING, STARTING). Measured rAF rate in IDLE: 238/s -> 14/s. `visual-core-performance.spec.ts` now asserts "at most one pending frame handle" (rAF or pacing timer) and that IDLE does not run at display rate (< 200 rAF in 10 s). `test:phase4` 154 passed, transport 237, desktop command center 39, lint unchanged (15 baseline warnings). Measured on the compact Overlay in IDLE only; other states were not re-measured natively.

### Owner attestation: native hearing/visual sync (2026-10-16; narrative, no durable record)
The owner attended the Windows run recorded in the table above and attested the S05 human-follow-up check (`windows-audible-playback`) as PASS in a session narrative; no durable human-observation record exists:

- The native Overlay followed the real playback envelope, showing THINKING then SPEAKING within 70 ms of the corresponding audio events.
- Cancel during playback left SPEAKING within 60 ms.
- The visual-follow harness wrote to the monitor's HDMI output (LG HDR 4K) to avoid disturbing the room; the speech on the G435 headset is row 3 (owner heard it). The owner states that audible output always works and accepts it as given.
- Two-clap (row 7): owner-observed that one clap did nothing, two claps started Jarvis, and a third clap afterwards did nothing; the instrumented event trace for the two-clap start is in row 7.

Supporting evidence rows: 2 (real microphone envelope reaches the visual core), 3 (actual playback envelope, start and end), 4 (cancellation during speech) and 6 (reconnect without startup replay). This entry is a human observation recorded by the owner, not a machine measurement; the automated rows remain as measured.

### Row 5 defect found and fixed: restarted Core never re-installed
`CoreSignals.snapshot` always emits `replaces_session: null` (`src/jarvis/observability/core_signals.py:349`) and a restarted Core has a brand-new session id, but `visualCoreMachine.ts` only accepted a snapshot from another session when `replacesSession` equalled the current session. A live renderer therefore rejected the new Core's snapshot forever. Fix: the machine has a `rejoining` flag (set on every (re)connect, cleared when a snapshot installs); while it is set, a foreign-session snapshot with `replacesSession: null` installs as a fresh baseline (fences reset, as for any session replacement). On a healthy link a foreign session is still rejected. Test: `node-tests/visual-core-machine.test.ts` ("a restarted Core ... installs after reconnect, but never on a healthy link"); transport suite 238/238.

### Findings outside the matrix
- SQLite `database is locked` when the daemon runs with `memory.db_path` relative and cwd on the WSL UNC path (`pushd` maps the UNC share). Launch artefact, not a code defect; the real installation uses a local path. It cascades into `welcome speech failed: 'runtime'` because `holder["runtime"]` is never populated when services fail to start.
- `output_device: null` follows the Windows default output, which was a Bluetooth headset, so the first playback went unheard.
- Electron win32 binary had to be provisioned into `desktop/node_modules` (`npm_config_platform=win32 npm ci` plus `node node_modules/electron/install.js`) for `native-smoke.cjs`.
