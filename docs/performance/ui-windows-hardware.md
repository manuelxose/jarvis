# Windows command-center hardware acceptance — 2026-10-01

**Gate: PARTIAL / owner control flow accepted.** A real Windows daemon and Edge session provide connected idle frame, CPU and process-attributed GPU Engine measurements. The owner heard Jarvis, saw both confirmation cards fully after the small-window fix, and confirmed Sleep worked. A physical-window check confirmed 1920×1080 but found that Windows clamps the requested 2560×1440 window to about 1920×1080 on this display; 1440p remains an emulated viewport result, not target-size hardware proof. A live speaking frame sample also remains unmeasured.

## Reproduction and safety

From this worktree root, build the real UI first (`npm --prefix ui run build`), then run `node ui/scripts/measure-hardware.mjs`. Start the **real** daemon using its normal Windows configuration before rerunning. When the owner is present to observe live audio and consent to actions, run `node ui/scripts/measure-hardware.mjs --exercise-controls`. If `py -3` is not the configured Windows interpreter, set `JARVIS_WINDOWS_PYTHON` to the absolute Windows Python executable in the process environment (do not change the daemon configuration). Exit 2 means the acceptance measurements did not run. The runner reads the real `jarvis ui --print` URL into memory, keeps its token only in process memory and never prints or persists it, launches a dedicated Edge `--app` with a unique temporary user-data directory and a Windows **127.0.0.1-only** CDP port, and stops only Edge processes matching that exact directory. It never starts, quits, or kills a Jarvis daemon. A CDP listener grants full browser control, including the in-memory token: do not expose or forward the debug port, run the probe only on a trusted local machine, and confirm the isolated Edge processes and temporary profile are gone afterwards. WSL-to-Windows localhost forwarding is needed only when launching this probe from WSL; Windows Node can connect to the loopback port directly.

The JSON report is printed without the token; retain it as the raw run record **only after inspecting it for sensitive data**. Eight seconds per 1920×1080 and 2560×1440 condition, at most 1,000 frame intervals per condition. `requestAnimationFrame` intervals estimate scheduling cadence, not actual GPU draw/presentation time or a Core Web Vitals score. Reported p50/p95, >16.7 ms and >50 ms counts, Edge-process cumulative CPU seconds delta, aggregate working set, process count, and CDP `TaskDuration`/heap/layout/recalc metrics should be interpreted together. The sampled idle state is connected UI without forced activity (the orb may still animate). An active/speaking sample is named only when the live orb state actually reads `listening` or `speaking`; an accepted activation is **not** itself a speaking sample. Renderer identity uses `WEBGL_debug_renderer_info` if exposed; WebGL2 alone does not prove hardware acceleration. GPU utilization is explicitly unmeasured until a session-attributable Windows counter is sampled. At 10× event rate, JS dispatch/render and the one animation loop are likely first bottlenecks; the current probe does not inject load and makes no 10× performance claim.

## 2026-10-01 Windows Edge idle run

The daemon used the active M008 worktree source, the existing Windows virtual environment, and a temporary copy of the owner's complete Windows configuration with only `memory.db_path` changed to an isolated Windows-local database. The UI was the worktree's built `ui/dist`. Windows Node and Edge ran on the same host, avoiding the WSL localhost CDP failure. The token-free raw report is [ui-windows-hardware-2026-10-01.json](../bench/ui-windows-hardware-2026-10-01.json). No real control command was issued.

| Connected idle condition | Recorded frame intervals | rAF p50 / p95 | >16.7 ms / >50 ms | Edge CPU seconds over 8 s | Working set |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1920×1080 | 1,000 | 4.2 / 4.5 ms | 9 / 1 | 14.97 | 638 MB |
| 2560×1440 | 1,000 | 4.2 / 4.4 ms | 0 / 0 | 13.34 | 655 MB |

The 1,000-interval cap covers only the first part of each 8-second window on this high-refresh display; CPU deltas cover the full window. Edge reported `ANGLE (Intel Iris Xe Graphics, Direct3D11)` as its unmasked WebGL renderer. These are rAF scheduling intervals, not presented-frame timings. The Edge process tree consumed more than one CPU-second per wall-clock second while idle, so idle CPU needs profiling before an efficiency claim. This initial run had an invalid GPU counter reading above 11 million percent and did not exercise controls; later owner and valid GPU evidence is recorded below. No live speaking sample was captured.

The original runner set Playwright's viewport without resizing the native Edge app window. A later CDP `Browser.getWindowBounds` check showed that a 1920×1080 native window is available, while Windows clamps a requested 2560×1440 native window back to 1920×1080. Treat all 2560×1440 figures above as **emulated viewport results**, not a physical 1440p GPU/frame acceptance sample. The runner now records actual window bounds and caps the viewport to those bounds.

Run the probe with Windows Node when WSL cannot reach Windows loopback CDP; the script uses its current directory on Windows and `wslpath` in WSL. Confirm the isolated Edge profile is removed afterward.

## Owner-present Activate/Sleep run

With the owner at the Windows machine, the Windows-native probe ran `--exercise-controls` against the same isolated real daemon. Its token-free [first raw control report](../bench/ui-windows-controls-2026-10-01.json) records keyboard confirmation of both actions. The status immediately after Activate was `starting`; a later independent `jarvis status` showed startup reached `ready` with `welcome_spoken` at 14,750.9 ms. The immediate status query after Sleep timed out, but the independent later status was `sentinel`, with the voice in cooldown and no session holders. The owner reported hearing Jarvis's voice and that the Edge window closed, but said the confirmation cards were cut off by the small window.

The card now caps its height to the viewport and scrolls internally. The regression test reproduces the original cut-off at 320×200 and passes for both Activate and Sleep after the fix. The runner now resizes the physical Edge window through CDP and records its bounds. In the [second owner-present report](../bench/ui-windows-controls-fixed-2026-10-01.json), both keyboard confirmations were accepted and the status after Sleep was `sentinel`. The owner explicitly confirmed that both cards, including Cancel and Confirm, were fully visible and that Sleep worked. This closes the **owner-observed control/audio/keyboard** part of the gate.

The run did not capture an active/speaking frame sample. The GPU-counter attempts available at that point were invalid or unmatched; the later valid sample is recorded below. The probe reported profile cleanup failure, but a later process check found no isolated Edge processes and the exact temporary profile was removed manually. No unrelated Edge process was terminated.

## Process-attributed GPU Engine sample

A later idle probe enumerated the isolated Edge process tree after the child processes had started, then sampled Windows `GPU Engine(*)\Utilization Percentage` three times at one-second intervals. The 13 matching engine counters had no invalid readings; their per-sample sums were 18.80%, 19.13%, and 18.24%. The [raw counter record](../bench/ui-windows-gpu-2026-10-01.json) preserves the source and timestamps. This is utilization attributed to the isolated Edge process tree, not whole-device GPU use or presented-frame time. Earlier invalid and unmatched attempts were discarded, not averaged into this result. The reported WebGL renderer was Intel Iris Xe through Direct3D11.

## 2026-10-01 speaking-frame attempt (WSL-launched, Windows-run)

Run from WSL with Windows Node and a Windows daemon started via `jarvis daemon` (worktree source, owner config, isolated worktree-local memory DB). The probe now waits up to 75 s after Activate for the orb to read `listening` or `speaking`. [Raw report](../bench/ui-windows-speaking-attempt-2026-10-01.json): Activate and Sleep accepted, final status `sentinel`; idle rAF p50/p95 16.7 / 16.9 ms (1080p) and 16.7 / 17.0 ms (emulated 1440p, 480 intervals each), Edge CPU 2.0 s and 1.2 s per 8 s window. Two same-day idle runs differ widely (14.7 / 8.1 s earlier, 2.0 / 1.2 s here), so idle CPU is noisy and no efficiency claim is made.

**Speaking remains unmeasured.** The startup welcome publishes no `speech.started` event, so the orb never enters `speaking` for it; only a turn through `turn_manager` does, and in the shared room nobody spoke a command during the window. No daemon command injects a spoken turn. To close this, run `--exercise-controls` with the owner saying "Jarvis, ¿qué hora es?" after the welcome. The daemon and isolated Edge profile were stopped and removed afterwards.

## Previous 2026-09-30 blocked run

| Check | Observation | Acceptance |
| --- | --- | --- |
| Windows bridge | `powershell.exe` responded; Windows 11 Home | Available |
| CPU | Intel Core i7-12700H | Identified only |
| GPUs | NVIDIA GeForce RTX 3070 Laptop GPU; Intel Iris Xe Graphics | Identified only; which renderer would be selected is unknown |
| Edge | Version 154.0.4258.37 installed | Identified only; not launched |
| Real daemon | Configured control port 47811: no listener (`Get-NetTCPConnection`) | **Blocked** |
| 1080p / 1440p DPR, renderer, frame intervals, CPU delta, memory, CDP metrics | No samples (`n = 0`, no raw intervals or aggregates) | **Unmeasured** |
| GPU utilization / real-GPU fallback | No browser session; no per-session counter sample | **Unmeasured** |
| Actual Activate and Sleep confirmation, status and audible result | Not issued: real daemon absent | **Blocked** |
| Offline/control denial and WebGL fallback | Covered by independent browser/contract fixtures, not target-hardware measurements | Not hardware acceptance |

No threshold comparison can be asserted from zero samples. Existing architecture budget is a <250 KB gzip initial JS shell, event-driven idle (no polling), and startup paint before shell-ready. The historical 68.67 KB gzip figure predates the present command center; the current build emitted 33.10 + 70.99 + 1.33 = 105.42 KB gzip JavaScript across three chunks (not an initial-transfer measurement), but no Windows idle CPU, frame pacing or GPU threshold has been demonstrated. Mitigation if idle p95 exceeds 16.7 ms or CPU consumption is material: profile Edge Performance/CDP tasks and the orb animation loop, compare reduced-motion and CSS fallback, then lower animation work before adding polling. A >50 ms frame is recorded as long; counts alone are not a pass criterion. Do not tune blindly against WSL software rendering.

## Recovery and remaining acceptance

1. Check `jarvis status` with `config.win.json` on Windows; start the real daemon through its usual owner workflow, preserving provider/model settings and state. Confirm UI serving and rebuild if `ui/dist` is missing. Do not start an incomplete test daemon just to produce a pass.
2. Re-run the probe at both sizes. If Windows Python, Edge, CDP or WSL localhost forwarding fails, record that precise gate; inspect only the isolated profile and never terminate unrelated Edge sessions. Confirm cleanup after timeout/failure.
3. With owner present, rerun `--exercise-controls`; verify focus/Escape/Cancel and the confirmation's accessible status, both accepted dispatches, before/after daemon status and audible/visible effect. An HTTP 202 means dispatch accepted, **not** that voice startup completed. Check denial and disconnect separately; never send an action when offline.
4. Independently simulate WebGL context loss (or disable WebGL2) to verify the CSS fallback, without mislabelling its frame intervals as real-GPU samples. The automated `ui/src/testing/orb.spec.ts` covers fallback/reduced motion; `ui/src/testing/controls.spec.ts` covers Cancel/Escape, repeat click, denied token, foreign Origin, unsupported action, callback failure, offline disabling, and axe on the dialog. `tests/test_event_stream.py` covers malformed and oversized HTTP. These are functional negatives, not a hardware pass.
5. Collect a genuine live `speaking` sample and an attributable GPU Engine counter if available. Record actual raw JSON sample counts, aggregates and counter provenance here before changing this gate. If a counter is unavailable, mark utilization unmeasured rather than estimating it from WebGL2 or whole-GPU telemetry.
