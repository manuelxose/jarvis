# M008 — Futuristic voice interface (design, implementation-ready)

Goal: an everyday desktop window that *is* Jarvis's internal state made visible —
not a decorative animation. Every pixel that moves is driven by a real signal:
synthesized-voice samples, microphone level, pipeline phase, tool execution,
service health or hardware load.

Depends on: M007 (sentinel daemon, startup sequence, tool gateway, mixer).

## 1. Frontend technology decision

| Option | Fit with this repo | GPU rendering | Cost | Verdict |
|---|---|---|---|---|
| **Local web UI served by the daemon, shown in an Edge/WebView2 app window** | Python-only repo; stdlib `asyncio` HTTP + Server-Sent Events, vanilla JS + WebGL2, no build step | Chromium GPU compositor + WebGL2 on the RTX 3070 | 0 new Python deps; Edge/WebView2 ships with Windows 11 | **Chosen** |
| Tauri (Rust + WebView2) | Adds Rust + Node toolchains and a second process model | Same WebView2 | Toolchains, signing, updates | Only if a single packaged `.exe` becomes a requirement |
| Electron | Adds Node + ~150 MB runtime | Chromium | Heavy, duplicate Chromium | Rejected |
| PySide6 / Qt Quick (native) | Python, in-process | QML scene graph + shaders | ~200 MB wheels, QML learning curve, UIA quirks | Fallback if WebView2 is unavailable |

Window: `msedge --app=http://127.0.0.1:<port>/?t=<token> --window-size=...`
(frameless app window, own taskbar entry). Optional later: `pywebview` for an
always-on-top / tray variant (one small dependency, same HTML).

Why web tech here: the visual layer is a shader + a few DOM panels; the DOM gives
keyboard focus, ARIA live regions and screen-reader support for free, and WebGL2
keeps the animation on the GPU. The Python side stays the single source of truth.

## 2. Architecture

```
            ┌────────────────────────── Jarvis daemon (Python, one process) ───────────────────────────┐
 mic ──► VoiceLoop ──phase/level──┐                                                                  │
 TTS ──► StreamRenderer ─audio frames (rms, 16 bands, t_play)─┐                                      │
 Mixer (music) ── level ─────────────────────────────────────┐│                                      │
 TurnManager ── route/transcript/response ──────────────────┐││                                      │
 ToolGateway ── tool start/end, risk, confirmation ────────┐│││                                      │
 StartupSequence ── on_phase, timings ────────────────────┐││││                                      │
 Supervisor ── HealthChanged ────────────────────────────┐│││││                                      │
 MetricsSampler (1 Hz: CPU, RAM, GPU util/VRAM/temp) ───┐││││││                                      │
                                                        ▼▼▼▼▼▼▼                                      │
                                                   EventHub (in-process pub/sub, bounded queues)     │
                                                        │                                            │
                                     UiServer: GET / (static)  GET /events (SSE)  POST /control      │
            └───────────────────────────────────────────┼────────────────────────────────────────────┘
                                                        ▼  127.0.0.1 only, per-launch token
                                   Edge app window: panels (DOM) + orb (WebGL2 canvas)
```

- **EventHub** (`jarvis/observability/hub.py`): `publish(event: dict)` is
  non-blocking and thread-safe (audio callbacks publish from PortAudio threads via
  `loop.call_soon_threadsafe`); each subscriber has a bounded deque; audio frames
  are coalesced (latest wins) so a slow UI never back-pressures the voice pipeline.
- **UiServer** (`jarvis/apps/ui_server.py`): stdlib `asyncio.start_server` HTTP/1.1,
  serves `ui/` static files, streams `text/event-stream`, accepts `POST /control`.
  Binds 127.0.0.1, requires the random token (header or first-party cookie), checks
  `Origin`, no CORS. Controls map to existing daemon/gateway calls only.
- Nothing in the voice path waits on the UI; UI off = zero overhead beyond
  `publish()` returning when there are no subscribers.

## 3. Event contract (versioned, `"v": 1`)

All events: `{"v":1, "t": <monotonic ms>, "type": ..., ...}`. Rates are ceilings.

| type | fields | source | rate |
|---|---|---|---|
| `state` | `state`: `sleeping`\|`starting`\|`listening`\|`transcribing`\|`thinking`\|`speaking`\|`executing`\|`confirming`\|`error`; `detail` | VoiceLoop phase, TurnManager, gateway, sentinel | on change |
| `audio` | `src`: `tts`\|`mic`\|`music`; `rms` (0–1); `bands` (16 log-spaced, 60 Hz–8 kHz, 0–1); `t_play` (ms when the frame reaches the speaker) | StreamRenderer slice writes, VoiceLoop frames, Mixer.render | ≤ 30 Hz per src |
| `speech` | `event`: `start`\|`segment`\|`end`\|`interrupted`; `text` (segment) | TurnManager / StreamRenderer.abort | per segment |
| `transcript` | `role`: `user`\|`jarvis`; `text`; `final` | VoiceLoop / TurnManager | per utterance |
| `tool` | `name`, `risk`, `phase`: `start`\|`end`; `ok`; `ms`; `say` | ToolGateway (same record as the audit log, already redacted) | per call |
| `confirm` | `id`, `tool`, `explanation`, `expires_at` | VoiceConfirmer | per request |
| `startup` | `phase`, `timings_ms`, `workspace` | StartupSequence.on_phase | per phase |
| `health` | `components`: `{name: {status, detail}}` | Supervisor HealthChanged | on change |
| `metrics` | `cpu`, `ram`, `gpu_util`, `vram_used_mb`, `vram_total_mb`, `gpu_temp_c`, `gpu_clock_mhz` | MetricsSampler (psutil + nvidia-smi) | 1 Hz |
| `providers` | `llm`, `stt`, `tts`, `voice_clone_ready`, `mic_device`, `speaker_device` | runtime diagnostics | on change |

`POST /control` bodies: `{"action": "push_to_talk"|"stop_speaking"|"cancel_operation"|"sleep"|"mute_mic"|"confirm"|"deny", "id"?: ...}`.
`stop_speaking` = turn interrupt (narration only); `cancel_operation` = `ToolGateway.cancel_operations()`
— the same narration/execution split as the voice commands. `confirm`/`deny`
resolve the pending `VoiceConfirmer` (click or voice, first wins).

Seams already present from M007 (no rework needed): `StartupSequence(on_phase=…)`,
`Mixer.level`, `AudioOutputQueue.state()`, `ToolGateway` audit records,
`VoiceConfirmer.waiting/answer`, `Sentinel.status()`.

## 4. Audio-driven animation pipeline

1. **Analysis at the source (Python, no extra deps):** `StreamRenderer._blocking`
   already writes 40 ms slices. For each slice compute `rms` and a 16-band
   magnitude spectrum (`numpy.fft.rfft` of 40 ms ≈ 640 samples at 16 kHz; ~20 µs).
   Stamp `t_play = now + stream.latency*1000 + queued_ms` so visuals match what is
   *heard*, not what was synthesized. Mic frames (VoiceLoop, 100 ms) and the mixer
   block do the same with `src` set.
2. **Transport:** coalesced SSE at ≤30 Hz per source (~2 KB/s).
3. **Client smoothing:** a jitter buffer schedules frames at `t_play`; per-band
   envelope follower (attack 15 ms, release 120 ms) so plosives punch and vowels
   glow; interpolated to the display rate with `requestAnimationFrame`.
4. **Interruption:** `speech.interrupted` (from `StreamRenderer.abort`) drops the
   jitter buffer immediately and triggers a 180 ms collapse — the orb visibly stops
   when the voice stops.
5. **Intensity:** long-window loudness (1.5 s) drives global energy; short-window
   rms drives the pulse; band centroid drives hue shift within the state palette.

## 5. Visual design

Restrained, dark, legible for daily use (no constant bloom, no scanline gimmicks).

- **Palette (dark):** background `#070B10`, surface `#0D141C`, hairlines
  `#1B2A36`, text `#D7E3EC`, muted `#7C8B97`. State accents: listening cyan
  `#38D6F5`, thinking amber `#F5B63B`, speaking arc-blue `#5BA8FF`, executing
  violet `#9B7BFF`, confirming warm white `#FFE8C2`, error red `#FF5A5F`,
  sleeping slate `#3A4652`. All text ≥ 4.5:1 contrast.
- **Layout (1280×800 default, responsive down to 800×600):** centre — the orb
  (40 % width). Left rail — health (per component dot + label), providers (LLM,
  STT, TTS, clone ready), devices (mic, speaker). Right rail — active tools and
  tasks with risk badges, startup progress (from `startup` timings). Bottom —
  transcript (last 50 turns, scrollable, copyable). Top bar — CPU/RAM/GPU/VRAM
  sparklines (60 s) and GPU temperature.
- **The orb (WebGL2 fragment shader, one full-screen quad):** a ring of 64
  radial spokes + a soft core. Spoke length = band energy (16 bands mirrored);
  core radius = rms; rotation speed = state; turbulence noise amplitude = long-window
  loudness. States: *listening* — cyan, spokes follow the **mic**; *transcribing* —
  spokes freeze and sweep once; *thinking* — amber slow orbit, no audio;
  *speaking* — blue, spokes follow the **TTS** frames at `t_play`; *executing* —
  violet segmented ring with one segment per running tool; *confirming* — warm
  pulsing ring + modal card; *error* — red flash then back; *sleeping* — dim slate
  breathing at 0.2 Hz. Transitions: 250 ms colour/shape crossfades.
- **Reduced motion:** `prefers-reduced-motion` → static ring + numeric level meter.

## 6. Accessibility and controls

- Every control is a real `<button>` with a visible label and shortcut:
  `Space` push-to-talk (hold), `Esc` stop speaking, `Ctrl+.` cancel operation,
  `Ctrl+Shift+S` sleep, `M` mute mic, `Enter`/`Backspace` confirm/deny when a
  confirmation card is focused (focus moves to it automatically).
- ARIA: `role="status"` live region announces state changes (throttled);
  transcript is a `role="log"`; confirmation card is `role="alertdialog"`.
- Voice parity: every control has an existing voice command ("para", "cancela la
  operación", "a dormir", "confirmo"/"no").
- High-contrast mode respects `forced-colors`.

## 7. Performance budget (RTX 3070 Laptop, 1080p window)

- GPU: < 3 % utilisation and < 150 MB VRAM for the window (one quad shader, no
  post-processing); the voice clone keeps its ~4.2 GB.
- CPU: < 2 % of one core for the daemon's event hub + SSE; < 5 % for the Edge
  renderer at 60 fps; pause rendering when the window is hidden/minimised
  (`visibilitychange`), 15 fps when `sleeping`.
- Voice pipeline impact: time-to-first-audio unchanged within noise (measured).

## 8. Slices

| Slice | Delivers | Proof |
|---|---|---|
| **S01 Event hub + instrumentation** | `EventHub`, publishers in VoiceLoop / TurnManager / ToolGateway / StreamRenderer / Mixer / Startup / Supervisor, MetricsSampler, contract doc as JSON schema | unit tests: rates, coalescing, thread-safety, redaction; no TTFA regression |
| **S02 UI server + shell** | `UiServer` (token, origin check, SSE, control), `ui/index.html` layout, panels, transcript, keyboard/ARIA, Edge app launcher (`jarvis ui`) | tests for auth/origin/control mapping; Narrator + keyboard-only run |
| **S03 Audio-reactive orb** | WebGL2 shader, jitter buffer at `t_play`, envelope followers, state palettes, interruption collapse, reduced-motion fallback | recorded frame-accurate test: TTS segment vs orb energy correlation > 0.8; barge-in collapse < 200 ms |
| **S04 Controls & confirmations** | push-to-talk, stop/cancel split, sleep, mute, confirmation card bound to `VoiceConfirmer` | tests: click vs voice race, expiry, narration vs execution |
| **S05 Hardware + owner acceptance** | GPU/CPU/VRAM measurements, 1-hour soak, owner visual UAT | numbers in `docs/engineering/m008-verification.md` |

## 9. Acceptance criteria

- Orb reacts to the **cloned voice's** actual samples (not a canned loop) and
  stops within 200 ms of barge-in.
- Each of the 9 states is visually distinct and announced to screen readers.
- Live CPU/GPU/RAM/VRAM and component health match `jarvis status` / nvidia-smi.
- UI can be closed and reopened at any time without affecting Jarvis.
- All controls work by keyboard, mouse and voice; HIGH-risk confirmation works from
  the card or by voice.
- Budget in §7 met on the target laptop; owner signs off the look for daily use.
