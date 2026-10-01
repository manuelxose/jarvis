# M009 performance: bb29c0f baseline versus after-run

**Verdict: PARTIAL / UNPROVEN under equivalent conditions.** The saved Windows after-runs demonstrate measured routing, command, interruption, TTS, LLM and one voice lifecycle, but do not establish an all-sections same-host before/after speedup. The [fast baseline](../bench/baseline-bb29c0f-fast.json), [model baseline](../bench/baseline-bb29c0f-models.json), [fast after-run](../bench/m009-after-fast.json) and [model after-run](../bench/m009-after-models.json) are the source of the numbers below. Do not substitute [`latest.json`](../bench/latest.json): it is a separate run.

## Provenance and reproduction

| Run | Host / Python | Recorded local time | Sections and sample counts |
|---|---|---|---|
| bb29c0f fast | LAPTOP / 3.11.9 | 2026-09-24 09:00:51 | claps 40, route 1300, command 30+30, startup 3, interrupt 8; resources point values |
| M009 fast | MSI-MANUEL / 3.12.0 | 2026-10-01 11:26:36 | claps 40, route 1300, command 30+30+10, interrupt 8; startup and resources **error** |
| bb29c0f models | LAPTOP / 3.11.9 | 2026-09-24 09:01:32 | STT 10, TTS cold 1 and warm 16+16, LLM 8+8, E2E 5+5 |
| M009 models | MSI-MANUEL / 3.12.0 | 2026-10-01 11:27:02 | TTS cold 1 and warm 16+16, LLM 8+8, voice lifecycle 1; STT and E2E **error** |

T01 verified the saved Windows `scripts/perf_bench.py` runs against this worktree's source and the real `config.win.json` provider/model settings (Windows `C:\Python312\python.exe` imported `jarvis` from this worktree's `src`). The raw JSON records host, Python and time, **not** the full original shell invocation, device identity, GPU driver, config hash or provider actually selected per request. The commands below are reproducible section/output commands, not a claim that a byte-for-byte original command line was recorded. Run them from this worktree with a functioning Windows interpreter and the real Windows config; a run can take minutes, use providers and start a voice worker. Do not overwrite the preserved JSON. `--pid` is appropriate only for a confirmed project-owned daemon.

```text
python scripts/perf_bench.py --config config.win.json --only claps,route,command,startup,interrupt,resources --out docs/bench/m009-after-fast.json
python scripts/perf_bench.py --config config.win.json --only stt,tts,llm,e2e,voice --out docs/bench/m009-after-models.json
python scripts/perf_bench.py --compare docs/bench/baseline-bb29c0f-fast.json --compare-only docs/bench/m009-after-fast.json
python scripts/perf_bench.py --compare docs/bench/baseline-bb29c0f-models.json --compare-only docs/bench/m009-after-models.json
```

The last two commands only read JSON and print the unfiltered paired tables. The table below retains the same-definition numeric pairs, but **none is a controlled hardware-matched causal delta** (LAPTOP versus MSI-MANUEL, Python 3.11.9 versus 3.12.0). Percentages are `(after p50 / baseline p50 - 1) × 100`; lower latency is better. For small absolute times, rounding and timer noise matter more than percentage changes. The baseline LLM lists `deepseek` and `ollama`, while the after-run lists only `ollama`; the JSON does not identify which provider served each trial. Its numeric differences are therefore **not provider-matched**. No credentials, configuration contents or raw prompts are reproduced here.

## Paired latency observations

Each cell is p50 / p95 in **ms**; `n` is baseline → after. These are *descriptive* same-measurement-name comparisons, not proof of an improvement caused by M009. `perf_bench.py`'s compare-only renderer prints all matching keys regardless of host, provider, sample count or semantic changes; the exclusions below supersede any misleading printed row.

| Metric | n | Baseline p50 / p95 | After p50 / p95 | p50 delta | Interpretation |
|---|---:|---:|---:|---:|---|
| route.intent_ms | 1300 → 1300 | 0.01 / 0.03 | 0.02 / 0.05 | +100.0% | Same 13 utterance strings, 100 repetitions; changed routing decisions (window/network now local). Too small to infer a regression across hosts. |
| command.time_query_to_first_audio_ms (tool+route, TTS stubbed) | 30 → 30 | 0.11 / 0.30 | 0.08 / 0.21 | -27.3% | Stubbed speech, not device playback or real end-to-end command latency. |
| command.cached_ack_to_first_audio_ms | 30 → 30 | 0.07 / 0.13 | 0.13 / 0.24 | +85.7% | Cache-hit first chunk, not audible output; sub-millisecond. |
| interrupt.playback_stop_ms | 8 → 8 | 4.54 / 10.33 | 0.02 / 0.03 | -99.6% | Silent device/cancellation harness; cross-host, not speech barge-in detection. |
| tts.warm_ttfa_ms | 16 → 16 | 210.45 / 218.22 | 209.54 / 229.03 | -0.4% | Warm clone synthesis, not playback; after p95 is higher. |
| tts.warm_total_ms | 16 → 16 | 1206.53 / 2097.13 | 1009.36 / 1508.50 | -16.3% | Repeated short phrases, cross-host only. |
| llm.ttft_ms | 8 → 8 | 737.04 / 952.64 | 235.44 / 6692.71 | -68.1% | **Non-comparable providers**; after p95 is much worse despite lower p50. |
| llm.total_ms | 8 → 8 | 1007.82 / 1313.19 | 1065.97 / 8443.57 | +5.8% | **Non-comparable providers**; network/provider tail dominates. |

The new `command.cached_ack_to_device_ms (real output stream, silent)` is **0.12 / 0.43 ms (n=10)**, with **no baseline equivalent**; it timestamps entry to the renderer's blocking callback **before** the device write, not completion of that write or the device buffer reaching a listener. The route map now handles `minimiza chrome` as `fast_command:window_manage` and `muéstrame la actividad de red` as `fast_command:network_stats` (baseline `fast_model` for both). This demonstrates changed dispatch, not a measured response time for actual pycaw, window or network operations.

**Excluded even though compare-only prints a row:** `claps.confirmation_after_last_clap_ms` is 555.42 / 645.76 (n=40) for **three** required claps in the baseline versus 47.97 / 54.55 (n=40) for **two** now. The printed -91.4% is **not a like-for-like speedup**. The after-run first-clap candidate is 30.0 / 30.0 (n=40); baseline `first_clap_candidate_ms` has `n=0`, so no pair or delta exists. Both are offline synthetic ClapDetector trials, 40/40 detected after-run, not microphone, OS, acoustic clap-to-chime or real-microphone UAT. The data does not prove one clap never activates, a third never restarts, or speculative cancellation after expiry.

**Unavailable pairs:** after-run `startup` is `ModuleNotFoundError: No module named 'sounddevice'` (baseline first sound 68.64 / 731.52, music 30.03 / 699.24, welcome 2399.46 / 3057.24; baseline n=3 each). After-run `resources` reports `no jarvis_daemon.pyw process found; start the daemon or pass --pid`; no PID was guessed, so no after idle CPU/RSS/VRAM comparison. After-run `stt` and `e2e` are `ProviderUnavailable: faster-whisper is not installed; local STT unavailable` (baseline STT transcribe 252.95 / 620.44, n=10; E2E first audio 1753.93 / 1877.70 and STT 419.87 / 469.34, n=5). An error is not zero latency, and neither `n=0` nor a missing counterpart warrants a percentage. Previously published [Windows startup measurements](../performance.md#windows-startup-and-idle-measurements-2026-09-30) are a distinct run, not the missing M009 after section.

## Voice lifecycle and VRAM: point observations only

- Clone cold load: baseline **74,296.71 ms** and after **55,443.62 ms**, **one** run each. The compare-only table calls this -25.4%, but `n=1` means its `p50` and `p95` are just the *same sample*; it is not a distribution or matched-host cold-start improvement. After worker load was 41,188 ms, warmup 7,593 ms (baseline 14,750 and 5,672 ms); neither is a percentile.
- The **one** after-run voice lifecycle reports 28,533 ms from first-clap speculative request to cold-session voice ready (includes the approximately 0.4 s clap gap), then 0.0 ms for a later warm-session acquire during cooldown, and 231 ms from warm synth request to first audio chunk. Speculative loading is a *head start before confirmation*, **not** the warm reuse achieved only when the worker was already retained from a previous session. Zero here is a rounded observation, not a zero-cost general guarantee.
- After-run whole-GPU VRAM read **5,363 MB warm → 1,098 MB after shutdown/eviction**, a 4,265 MB drop, with **one** worker process started. The separate TTS section saw 1,098 MB before → 5,363 MB warm. Baseline TTS saw 3,598 → 7,863 MB warm (also +4,265 MB), but neither whole-GPU reading is per-process allocation; hardware and competing workloads differ. No baseline lifecycle/after-eviction point exists, no idle-daemon PID sample was taken, and no p95 or sustained-load GPU-pressure eviction claim is possible.

## Milestone success criteria and remaining proof

| Criterion | Status from this report |
|---|---|
| Exactly two confident claps activate, one never does, third never restarts | **UNPROVEN in live use.** Synthetic 2-clap confirmation was 40/40; microphone and real-noise/third-clap behavior need owner UAT, including re-enrollment/calibration after the clap-count change. |
| First clap starts cancellable speculation; expiry releases it | **PARTIAL.** Single voice cold-session speculative observation exists; expiry/cancellation and model release require runtime traces/tests and microphone-triggered validation. |
| Voice reused across sessions; evicted after cooldown or GPU pressure, never during synthesis | **PARTIAL.** One warm reacquire, one post-shutdown VRAM drop and one worker observed; no measured cooldown/pressure race or synthesis-safety proof in this run. |
| Cache keys cover profile identity/version, text, settings and format; bounded LRU | **UNPROVEN by benchmarks.** Cache-hit timing is not an invalidation or bounded-LRU test; use the cache contract tests and a profile-change integration check. |
| Degraded startup never plays success recording or blocks past deadline | **UNPROVEN by this after-run.** `startup` errored before recording a device timing. Exercise a failed dependency, inspect startup event/report and audible outcome against the deadline. |
| Equivalent-condition p50/p95 before and after | **UNPROVEN.** Numeric pairs exist for selected sections, but host/Python, provider and clap semantics differ; startup, STT, E2E and resources have no after figures. Repeat both sides on one pinned host/config/device/provider for a causal claim. |

Follow-up: live microphone claps and re-enrollment, physical sounddevice output, actual pycaw volume/window operations, and a live typed-event JSONL/SSE consumer remain unverified here. There is no long-duration production monitoring series for provider timeouts, device disconnects, GPU pressure, cache hit rate, dropped SSE events or startup deadline violations. Preserve section-level errors and host/provider/device metadata on reruns, and inspect runtime event/log surfaces rather than inferring production reliability from synthetic timing. No new live service, browser or listening test was performed for this report.
