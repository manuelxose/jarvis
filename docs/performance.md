# Performance

Measured on the reference machine: i7-12700H, RTX 3070 Laptop 8 GB, Windows 11,
built-in mic array (Intel Smart Sound) and speakers. Raw data:
[`bench/latest.json`](bench/latest.json). Reproduce with
`python scripts\perf_bench.py --config config.win.json` (silent: device timings
use zero-amplitude audio).

For a before/after comparison against pre-M005 latency, see the
[latency report](latency-report.md). For the M009 bb29c0f baseline-versus-after
Windows evidence, paired p50/p95, exclusions and remaining acceptance gaps, see
the [M009 performance report](engineering/m009-performance-report.md). Those
runs used different hosts; the table below comes from `latest.json`, not the
M009 after-run JSON.

## Benchmarks (p50 / p95)

| Metric | Result | Note |
|---|---|---|
| Gesture confirmation after the second clap | **48 / 55 ms** | 40/40 synthetic gestures detected |
| First clap → speculative voice load starts | 30 ms | |
| Trigger → chime | 77 ms (first run 121 ms) | opening the output stream dominates |
| Trigger → music | 28 ms | track decoded at daemon start |
| Trigger → welcome audio | **2037 / 2122 ms** | target 2.0 s (movie-style delay) |
| Cached acknowledgement → audio device | **0.18 / 0.7 ms** | plus the device buffer latency |
| Intent routing | 0.01 / 0.04 ms | |
| Local command (route + tool, TTS excluded) | 0.08 / 0.26 ms | |
| Interruption (playback stopped) | 4.3 / 9.3 ms | real device |
| Voice ready, session after a previous one | 0.1 ms | model reused from cooldown |
| Voice ready, cold session | 23.7 s | first-clap speculation saves only ~0.4 s |
| Clone warm time-to-first-audio | 208 / 220 ms | |
| Whisper load / transcribe 2.5 s | 8.6 s / 264 ms | turbo model, CUDA |
| DeepSeek time to first token | 769 / 930 ms | provider-bound |
| End of utterance → first audible reply (warm) | 1382 / 1552 ms | |
| Idle sentinel | 1.1 % of one core, ~140 MB | earlier estimate (all `jarvis_daemon.pyw` processes); superseded by the PID-scoped figures below |
| VRAM, voice warm → evicted | 5540 → 1275 MB | eviction returns ~4.2 GB |

### Baseline (bb29c0f)

[`bench/baseline-bb29c0f-fast.json`](bench/baseline-bb29c0f-fast.json) and
[`bench/baseline-bb29c0f-models.json`](bench/baseline-bb29c0f-models.json) are
the `perf_bench.py` outputs measured on 2026-09-24 on the reference machine with
the 3-clap gesture. They were recovered byte-for-byte from the pre-deletion
blobs (`git show 17f6b7c^:docs/engineering/bench/baseline-*.json`). Commit
`bb29c0f` itself is not in this repository's history.

Print a p50/p95 baseline-vs-current table from saved results (no audio, GPU or
network needed):

```
python scripts/perf_bench.py --compare-only docs/bench/latest.json --compare docs/bench/baseline-bb29c0f-fast.json
```

Or run the benchmark live on the reference machine and compare at the end:

```
python scripts\perf_bench.py --config config.win.json --compare docs/bench/baseline-bb29c0f-fast.json
```

## Windows startup and idle measurements (2026-09-30)

Real Windows host, worktree-local `.venv-win` (Python 3.11.9), `config.win.json` with only the DB path, control
port 47911 and the `terminal` workspace task changed (details and replay in [startup.md](startup.md#windows-daemon-acceptance-2026-09-30)).
Raw data: [`bench/windows-acceptance-2026-09-30.json`](bench/windows-acceptance-2026-09-30.json). Sign-in-equivalent run, not a reboot.

Reproduce: `python scripts\perf_bench.py --config <cfg> --only claps,startup` (daemon stopped, device output zeroed) and, with the
daemon running, `python scripts\perf_bench.py --config <cfg> --only resources --pid <daemon PID>`
(PID of the launched `pythonw.exe`; its child is included; other Jarvis instances are never summed).

| Metric | Result | Target | Verdict |
|---|---|---|---|
| Live daemon, `activate` → `first_sound` (report, `timings_ms_since_gesture`) | **39.3 ms** (3.9 ms after the trigger) | < 300 ms | Pass (one activation; timestamp when `play_sfx` returned, not audible output) |
| Bench, gesture → first non-silent chime callback, **primed** stream (as the daemon runs) | 6.6 ms p50, 7.1 max (n = 3, 3/3 idle callbacks seen before trigger) | < 300 ms | Pass; excludes the device buffer and the OS/microphone path |
| Bench, same, **cold** stream | 993, 83.7, 86.8 ms (n = 3) | < 300 ms | Fail for the first (truly cold) trial: 993 ms = import + stream open. Trials 2-3 reuse the imported modules and are not cold; the p50 (86.8 ms) is not representative. The daemon never takes this path once primed |
| Bench, first welcome audio after trigger | 3005-3009 ms primed (3.0 s `welcome_delay_seconds`) | movie-style delay | Configured, as before |
| Synthetic clap confirmation after the last clap | 48 / 55 ms p50 / p95, 40/40 detected | n/a | **Offline only**: no microphone, OS or chime latency. `clap_eval --synthetic` three-clap case: 510 ms (waits for the third clap) |
| Idle daemon CPU (10 s window, 1 s samples, pid 28632 + child) | mean 3.1 %, max 9.4 % of one core | low | Measured 30 s after the socket came up, so still settling (output prime, next runtime build) |
| Idle daemon RSS (same processes) | 78.9 MB | n/a | Measured in the same window; the child reached 281 MB after activation |
| Whole-GPU VRAM: before launch / idle / active / after `sleep` / after `quit` | 192 / 846 / 4124 / 5368 / 802 MB | n/a | Whole-GPU `nvidia-smi` value, includes other apps (VS Code, browsers). It rose 192 → 847 MB during the 10 s idle window and was still 802 MB after `quit` with the daemon gone, so **idle VRAM attributable to the daemon is not established** |
| Live daemon, `activate` → `interactive` | 15.1 s | n/a | Degraded welcome spoken through the SAPI fallback (the clone was cold) |

VRAM note: the active reading is +3.3 GB over idle (clone worker started at activation, Whisper loaded by the voice loop); after
`sleep` the models stay warm during the 600 s cooldown (`voice cooldown -> cold (shutdown)` only at `quit`), which is why the
after-sleep reading is higher than the active one. No per-process VRAM was available, so none is claimed.

Criterion-by-criterion status and the owner UAT checklist: [M007 verification record](engineering/m007-verification.md).

Unverified: cold `first_sound` through a real clap on the microphone, audible chime loudness, and the cloned-voice (`cache`/`live`) welcome.

For a like-for-like local Whisper versus Alibaba Qwen realtime STT comparison on the reference machine, follow the [STT bake-off procedure](stt-bakeoff.md); no cloud STT measurements are recorded yet.

## Targets

| Target | Status |
|---|---|
| Local command acknowledgement < 300 ms | Met when the phrase is cached; the first use of a phrase is live synthesis (~210 ms warm TTFA). |
| Cached audio start < 150 ms | Met. |
| First audible AI reply < 1.5 s | Met at p50 (1.38 s), missed at p95 (1.55 s). The VAD adds 600 ms of end-of-speech silence on top. |
| Audio interruption < 200 ms | Met for stopping playback. With `audio.barge_in` on, detecting the interruption needs ~500 ms of loud frames. |

## Known bottlenecks

1. **Clone cold start: 25–74 s.** About 16 s is model load; the rest is the torch
   import inside the worker (worse with a cold disk cache). The voice lifecycle hides
   it after the first session (cooldown reuse) and the recorded welcome hides it at
   activation. `voice.preload: "always"` removes it at the cost of ~4.2 GB of VRAM.
2. **DeepSeek first token (~0.75 s) and VAD end of speech (0.6 s).** Whisper variants
   (no Silero pre-pass, no timestamps) gained ≤ 20 ms and `without_timestamps` dropped
   the leading "Jarvis", so they are not used.
3. **GPU thermals.** Under sustained synthesis the laptop GPU throttles to 210 MHz and
   TTFA rises to 0.8–1.5 s. See [voice-clone.md](voice-clone.md#main-bottleneck-gpu-thermals).

## Clap detection

- One clap never activates (the candidate expires and the speculative load is undone);
  two claps confirm ~50 ms after the second; a third clap lands in the cooldown.
- Speech, music with drums (120/170 bpm), tones and quiet taps never activate in the
  test suite. Real recordings (190 s of music, owner speech): 0 activations at default
  and maximum sensitivity.
- Keyboard clicks have a clap-like shape, so loudness is the discriminator: calibration
  sets the threshold 12 dB under the softest calibration clap, and never within 12 dB
  of the room's p99 noise. If typing still triggers
  it, set `claps.confirm_quiet_seconds: 0.25` (adds a rhythm guard, +250 ms latency).

## Failure scenarios and their tests

| Scenario | Covered by |
|---|---|
| One / two / three claps, clap-like sounds | `test_claps` |
| Voice model unavailable or loading slowly | `test_startup`, `test_qwen_clone_tts` |
| Not enough VRAM, GPU-heavy app running | `test_voice_manager` |
| No Internet, cloud timeout or quota | `test_provider_fallback`, `test_model_adapters` |
| Startup music missing or undecodable | `test_startup` |
| Audio device missing | `test_daemon`, `test_audio_renderer` |
| Owner interrupts the welcome | `test_daemon.test_sleep_during_startup_interrupts_the_welcome` |
| Question before the voice has loaded | `test_qwen_clone_tts` |
| Repeated or simultaneous activations | `test_daemon.test_duplicate_activations_during_startup_are_ignored` |
| Application launch failure | `test_workspace` |
