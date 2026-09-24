# Performance

Measured on the reference machine: i7-12700H, RTX 3070 Laptop 8 GB, Windows 11,
built-in mic array (Intel Smart Sound) and speakers. Raw data:
[`bench/latest.json`](bench/latest.json). Reproduce with
`python scripts\perf_bench.py --config config.win.json` (silent: device timings
use zero-amplitude audio).

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
| Idle sentinel | 1.1 % of one core, ~140 MB | music preloaded, next runtime pre-built |
| VRAM, voice warm → evicted | 5540 → 1275 MB | eviction returns ~4.2 GB |

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
  sets the threshold 9 dB under the softest calibration clap. If typing still triggers
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
