# Reactive Core 2D Specification (Phase 4, S02)

Status: **proposed implementation contract, not rendered or subjectively approved**. Machine-readable twin: [reactive-core-spec.json](reactive-core-spec.json). JSON carries exact geometry, bounds, inputs and test-only traces; prose explains the same decisions. Neither file is a production store/schema package. S02 implements no renderer, reducer, telemetry transport, output device or settings API. Existing WebGL2 remains unchanged until S03/S04. SVG/CSS is the default 2D direction: no Three.js additions, new motion dependency, 3D or Phase 5.

## Sources and activation limits

- Runtime truth: [Phase 4 runtime audit](../engineering/phase-4-runtime-audit.md), checked against `src/jarvis/application/{startup,turn_manager,voice_manager}.py`, `src/jarvis/apps/daemon.py`, `ui/src/protocol/events.ts` and `ui/src/components/jarvis/orb/orbModel.ts` / `JarvisOrb.tsx`. The current model recognizes eight presentation states; its boolean `speaking` follows generated chunks, **not confirmed output**. Queue `played`, `first_sound`, `music_started`, `welcome_spoken` and `interactive` are not audible/device-drain proof.
- Adaptive Command identity: [design system](jarvis-design-system.md), [directions](visual-directions.md), [overlay UX](desktop-overlay-ux.md), [Command Center UX](command-center-ux.md). Live `ui/src/design-system/tokens.css` wins over historical prose. Graphite surfaces, cyan/blue geometry with the existing violet speaking/amber processing accents, restrained core-only glow, monospace labels/data and sans-serif prose; no new palette.
- Ponytail was activated from the explicitly resolved read-only skill. Other Phase 1 user skills were not available through this unit's permitted read paths; their recorded responsibilities in `docs/engineering/frontend-skills.md` inform the accessibility/performance checklist, not a claim of activation. `.claude/skills/jarvis-visual-identity/SKILL.md` is absent here; no reconstruction or other checkout was read.
- The Graphify graph/wiki and executable are absent; use the active S02 task plan and scoped execution evidence. No graph-refresh success is claimed.

## Fixed 2D identity and motion

Coordinates use `viewBox="-1 -1 2 2"`, center `(0,0)`, **one unit = half the core side**, not a radius exceeding a unit square. Ring radius `0.62`, dot radius `0.12`, base stroke `0.035` with round caps; painted extent including stroke must remain within radius `0.9`. Arc begins at the top (-90 degrees), runs clockwise. State glyph replaces dot except startup ticks retain it. JSON defines every glyph's coordinates (tick, dots, bars, square, check, triangle, slash, ticks, stop bars), dash lengths and sweep; no randomized blob geometry.

Required dash patterns in these normalized units: solid `[]`, dashed `[0.14,0.10]`, dotted `[0.01,0.11]`, segmented `[0.38,0.12]`. Optional output rings at `0.62,0.70,0.78`, clipped by each variant's budget. Fresh envelope `y` scales rings by `1+0.12*y`; clamp painted extent, drop optional outer rings before essential glyphs. No mic-driven or fake procedural speech. Glyphs stay upright; only a state ring rotates.

Full-motion breath is `scale=1+a*(1-cos(2*pi*t/P))/2`; ring rotation `360*t/P` degrees, with `t` and `P` in milliseconds. Zero period disables that motion. Defaults below are maxima before motion/accessibility overrides; `levelScaleMax` applies only to fresh confirmed output. No opacity flashing. State/label/fence/reset changes are immediate; decorative transitions use `--motion-ease-standard` and `--motion-duration-fast/base/slow` (120/200/320 ms). ERROR, DISCONNECTED and STOPPING snap (0 ms); reduced/disabled snap for all states. Interrupt transitions from the current transform, never enqueue them.

Surfaces: `--surface-app`, `--surface-panel`, `--surface-floating`. Essential glyphs and labels use `--text-primary`; state token colors can accent the ring. `--state-offline` is decorative only, not the sole readable slash/text. `--glow-core`/`--glow-core-inset` stay inside the core, never panels or controls. Labels use `--font-mono`, captions `--font-sans`.

### Ten canonical states

| ID | Label / static non-color cue | Accent token | Full motion: breath P/a; rotation P; level max | Transition / hold ms | Trigger and exit |
|---|---|---|---|---|---|
| IDLE | Idle / solid 360° ring + dot | `--accent-listening` | 4000/.03; 0; 0 | 320 / 0 | Authoritative ready/idle/sentinel or no active facts; exits on current activity/activation/loss. |
| LISTENING | Listening / open 270° arc + tick | `--accent-listening` | 2400/.04; 0; 0 | 200 / 0 | Actual Core capture state, never input amplitude or sentinel mic; exits on capture end/work/cancel/error/loss. |
| THINKING | Thinking / dashed 360° ring + three dots | `--accent-processing` | 0/0; 2400; 0 | 200 / 0 | Legacy processing/current correlated agent start; exits on result/execution/output/other activity. Generated speech detail forces static fallback. |
| SPEAKING | Speaking / solid concentric ring + three bars | `--accent-speaking` | 0/0; 0; .12 | 120 / 0 | Current device-write-confirmed speech playback **and fresh same-identity level**; exits on end/cancel/failure/stale/absence/stop/loss. |
| EXECUTING | Executing / segmented 300° arc + square | `--accent-processing` | 0/0; 1600; 0 | 120 / 0 | Legacy executing or proposed typed Core execution-start; exits on matching result/cancel/failure/loss. |
| SUCCESS | Done / solid 360° ring + check | `--state-success` | 0/0; 0; 0 | 120 / 900 | Matching uncancelled successful command/agent outcome after error aggregation; expires or is preempted. |
| ERROR | Error / broken 300° ring + triangle | `--state-danger` | 0/0; 0; 0 | 0 / 1500 | Correlated non-cancelled failure/output failure/startup failed/legacy error. Recoverable hold expires; persistent startup/device failure needs explicit recovery/new generation. |
| DISCONNECTED | Offline / dotted 360° ring + slash | `--state-offline` | 0/0; 0; 0 | 0 / 0 | Initial connection, loss, absent/invalid snapshot, legacy offline; only fresh validated snapshot clears it. |
| STARTING | Starting / solid ring, five ticks, dot | `--accent-listening` | 0/0; 0; .12 | 320 / 0 | Candidate/confirmed activation or nonterminal startup; actual terminal phase clears it. Welcome-only confirmed output can modulate rings without hiding phase text. |
| STOPPING | Stopping / solid 360° ring + two bars | `--text-secondary` | 0/0; 0; 0 | 0 / 0 | Confirmed stop/cancelled startup; authoritative cleanup then IDLE, or loss. No timer asserts cleanup. |

All ten have recognizable static geometry plus label under reduced/disabled motion. SUCCESS is never inferred from notification, generated speech completion or a startup timing mark. Ready startup has **Ready**, not an inferred successful app launch.

### Source-backed mapping, not invented events

`processing -> THINKING`, `offline -> DISCONNECTED`, `notification -> separate notification channel` (no core state). Legacy `executing -> EXECUTING`, `error -> ERROR`; legacy `speaking` is only a candidate requiring output confirmation. These are current orb/StatusIndicator **presentation aliases**, not a claim that current live Core publishes those values on `voice.state`.

Live `voice.state` comes from VoiceModelManager residency; do not confuse it with RuntimeState capture/turn state. TurnManager sets internal THINKING before routing and publishes `agent.started` after routing. `agent.progress.detail` is untyped text, **not** execution start. Proposed Core activity input must distinguish capture/tool/stop; no renderer parses text into activity or commands.

`command.executed` and `agent.completed` carry `trace_id/ok`. A failed tool can be followed by `agent.completed ok=true` because the error was successfully handled/spoken: preserve tool failure rather than displaying Done. `ok=false` may represent cancellation; cancellation fences win, and uncorrelated outcome stays static with outcome-unconfirmed text, not guessed error/success.

`speech.started` (cache/live) precedes output writes; `speech.completed` is orchestration and has coverage gaps in `speak_text`/welcomes. Neither confirms audibility nor ends a separately confirmed output stream. With absent/stale output, use static underlying THINKING/IDLE/STARTING and **Speech prepared; output unconfirmed** / **Output telemetry unavailable**, never an unconfirmed SPEAKING state or simulated waveform. Confirmed successful device writes still do not prove physical sound, volume at the ear or final DAC drain.

## Four presentation variants

Dimensions are CSS px/native DIPs, not backing pixels. Reference windows are budgets, not a change to persisted native bounds. Current native compact default is 360×72; expanded is a proposed design target (prototype card remains 360 px wide today).

| Variant | Reference window | Base core / scale | Grouped vector-element ceiling / rings / glow | Label / caption budget |
|---|---|---|---|---|
| compact-overlay | 360×72 | 40 px / .8–1 | 6 / 1 / off | 13 px / 1 line |
| expanded-overlay | 420×520 | 96 px / .75–1.25 | 12 / 2 / on | 13 px / 2 lines |
| command-center | 1280×800 | 240 px / .6–1.5 | 24 / 3 / on | 14 px / 2 lines |
| startup | existing host, 1280×800 reference | 320 px / .6–1.5 | 24 / 3 / on | 14 px / 2 lines |

Startup in an overlay **inherits that overlay's size/scale/stroke/detail ceilings**; it is not a new window or modal. A compound glyph/tick set counts as one grouped primitive. Keep the essential glyph/label at every size. Scale square uniformly; enforce ≥2 px painted strokes, ≥3:1 essential nontext contrast and ≥4.5:1 text on graphite. These are downstream test targets, not measured contrast acceptance. Drop outer rings/glow before shrinking text or hiding controls. High DPI/zoom must reflow without clipped text/focus.

Compact has adjacent label and reserved ellipsized one-line caption; expanded offers full text and focusable scrollable conversation. Preserve caption content, notification location/z-order and existing expand/collapse/hide/Command Center controls. Core adds no interaction island and must not change native drag regions, pass-through/focus ownership or hit targets. Decoration is aria-hidden next to accessible status. Do not auto-expand from amplitude.

Command Center replaces the AssistantStatus slot only: eight workspace navigation, command palette, skip link, center/sidebar/rail focus and responsive rail behavior remain intact. Startup cannot cover navigation/captions with a cinematic takeover. Notifications remain independent even while the core shows activity or error.

## Priority, identity and snapshots (proposed)

Highest active fact wins **after** identity/order validation and terminal fences:

`DISCONNECTED > STOPPING > ERROR > STARTING > SPEAKING > EXECUTING > THINKING > LISTENING > SUCCESS > IDLE`.

This is active-fact precedence, not sticky highest-ever state. Matching outcomes clear activity facts. Recoverable holds start on receipt, are not prolonged by duplicates, and expire while hidden. Startup announcing remains STARTING; confirmed welcome can modulate rings with its .12 budget without replacing phase/partial text. Stop/loss always clear levels immediately.

Accepted existing observations: bridge connection; `activation.started/confirmed/cancelled`; `startup.progress/completed/degraded`; `voice.state` (model only); `agent.started/progress/completed`; `command.executed`; generated `speech.started/completed`. JSON records provenance for each. Proposed conceptual inputs, **not existing wire APIs**, are Core `activity`, `output.playback`, `output.level`, authoritative `snapshot`. Existing envelope is `v/ts/mono_ms`, without required session/generation/playback/sequence/replay fields. S03/S05 must address Python/UI/desktop allowlists and schema parity, not silently accept new events.

- Session: opaque Core boot/session identity established only by snapshot. Retired session packets cannot restore state.
- Generation: nonnegative safe integer for a turn/startup activation; existing trace_id must be correlated, never invented by the renderer. Activation candidate identity is separate from an unrelated active turn.
- Playback: unique output instance within live generation, required on output data; null for non-output activity. Start accepted only under a live generation. Terminal end fences playback. Turn cancellation fences **all** playbacks/starts/results for the cancelled generation; a new playback ID cannot bypass it. Session stop fences the whole session.
- Sequence: safe nonnegative integer strictly increasing across **one session stream**, shared lifecycle/output/snapshot watermark, not per event type/window. Duplicate/lower sequence is ignored without refreshing sample age. Gap requests reconciliation and suppresses speaking. Valid envelopes advance observed watermark even when fenced payload is ignored; trace `acceptedSequences` describes applied state changes, not all observed envelopes.
- Initial/reconnect: remain DISCONNECTED until atomic authoritative snapshot with state, terminal fences, sequence watermark, startup progress, output age and available app outcomes. Proposed snapshot deadline 2000 ms; missing/invalid snapshot remains Offline / State unavailable. Drop queued packets ≤ watermark. Restored progress does not re-run activate, welcome, music, startup or any command. No new endpoint/polling implementation is introduced here.
- Freshness: proposed output age ≤250 ms; liveness budget 45000 ms or transport loss requires a new snapshot. Core monotonic capture age + bridge transit age + local monotonic elapsed; never subtract clocks from different origins or order by wall-clock `ts`. Negative/nonfinite/future ages invalid. Invalid levels never refresh last-valid sample age. Silence is not disconnection; lifecycle persists within live session until matching terminal/recovery.
- Stale >250 ms: reset level, peak and gate to zero, stop audio motion, fall back to static underlying activity with telemetry-unavailable text. No stale-tail decay animation. Fresh zero tail samples may release normally while the stream is active. Missing output telemetry stays static, not SPEAKING.

JSON `lifecycle.guards` makes these identity/order/fence/output prerequisites checkable without implementing an event reducer; `motionPolicy.staticModes` records the zero-transition, no-loop/no-level-redraw, glyph-and-label overrides for reduced and disabled motion. They describe the same proposed policies above, not new runtime fields.

Diagnostic reasons are declarative future observable fields: `level-stale`, `level-absent`, `sequence-duplicate`, `sequence-out-of-order`, `sequence-gap`, `identity-fenced`, `level-invalid`, `age-invalid`, `snapshot-unavailable`, `output-failed`, `disconnected`. Inspect JSON traces for expected reason/state/reset; S02 adds no logs/endpoints.

## Audio mapping (proposed, not current telemetry)

Raw PCM and the single microphone owner remain Python-side. Sentinel mic closes before startup; voice-loop mic takes over afterward. Browser opens no microphone/analyser, processes no PCM, and does not infer output from VAD/capture, TTS generation, Mixer.level or queue counters. Existing mixer/music and browser URL fallback are separate from speech output; music is phase text only here.

Python decodes actual WAV rate/channel/width (today speech renderer supports int16), divides int16 by 32768 (future float PCM bounded to [-1,1]), applies actual output gain, computes channel RMS over ≤40 ms **successfully written speech** windows, then uses max-channel RMS. Units are dimensionless normalized RMS [0,1], digital full scale, not dB SPL/loudness. No per-window max normalization or automatic gain amplification. Peak is max absolute written sample [0,1]. Reject nonnumbers, NaN and ±Infinity before arithmetic; clamp finite negative/over-range values to [0,1] with `level-invalid`. Sanitize all text; normalized telemetry never grants asset/command access.

Gate starts closed: opens at ≥.02, closes at ≤.012, retains state between; closed target is zero. Envelope:

`y += (x-y) * (1-exp(-dt/tau))`

`x` is gated target; attack tau=40 ms when x>y, release tau=220 ms otherwise. dt in ms, valid clamp [0,250]; negative/nonfinite dt gives no step. Freshness/terminal reset wins first. Peak hold ≤120 ms then release tau=220 ms; it never raises RMS envelope or flashes. All envelopes/gates/peak reset to zero on cancel, session/generation/playback replacement, disconnect, terminal end and stale telemetry.

Python off-audio-thread coalescer computes one shared envelope per accepted sample using monotonic sample dt. Proposed ceiling 30 telemetry updates/s, one latest-value slot per playback; do not synchronously publish per audio sample/slice into EventHub. Windows receive the same normalized RMS/peak/envelope facts, no independent PCM analysis or re-smoothing. Renderer may interpolate at ≤60 Hz via refs/imperative SVG/CSS, **no React per-frame state**. Reduced/disabled redraw only semantic changes, never level samples; hidden renderer has 0 Hz animation. Visibility resets local interpolation and restores current snapshot/envelope, not a backlog. No performance claim follows from these design ceilings.

### Numerical examples

Independent worked cases (except explicitly stated prior values), tolerance .002. dt=33 ms gives attack factor .562, release .139. Gate is closed initially for silence/quiet/normal, open before emphasis/pause. End is a fresh zero tail **before** actual terminal end.

| Case | dt ms | From y | RMS input / gated x | Expected y |
|---|---|---|---|---|
| silence | 33 | 0 | 0 / 0 | 0 |
| quiet | 33 | 0 | .1 / .1 | .056 |
| normal | 33 | 0 | .5 / .5 | .281 |
| emphasis | 33 | .281 | .9 / .9 | .629 |
| pause | 33 | .629 | 0 / 0 | .541 |
| end tail | 220 | .541 | 0 / 0 | .199 |

Actual terminal end resets to 0 immediately. Gate boundary trace `.019,.020,.015,.012` from closed expects `closed,open,open,closed`, targets `0,.020,.015,0`. Never substitute frame counts for elapsed time.

## Startup sequences tied to real Python phases

Source authority: `StartupOptions`, `StartupSequence._run/_announce`, `Sentinel._session/_on_startup_phase`. Existing phases are `idle, acknowledged, initializing, announcing, ready, degraded, failed, cancelled`; candidate is an activation observation, **not** another StartupPhase. Five categorical ticks group candidate/confirmed, acknowledged, initializing, announcing, terminal; they are not percentages, countdowns or five serial tasks.

1. Default two-clap activation: first clap publishes `activation.started`, speculative voice load only. Show candidate; no invented chime/music/startup. Confirmation after the configured gesture (possibly three claps) or hotkey/wake/control selects confirmed; direct activation may skip candidate. Expired `activation.cancelled` returns IDLE and fences that candidate only, not STOPPING for an unrelated session.
2. `acknowledged`: existing activation asset or synthesized cue; silent failure remains possible. `first_sound` alone is not measured output.
3. Services and workspace launch concurrently before awaiting music start. `initializing` follows local music/browser fallback attempt. Show preparing services/music; marks cannot prove sound. Workspace is concurrent, not a serial apps stage.
4. `announcing`: existing healthy prerecorded welcome cache or live/degraded/fallback voice, preserving owner/greeting/health/cache identity. Without fresh output show static phase text; confirmed welcome may modulate rings while state remains STARTING. Do not change assets/allowlists or synthesize a visual stream.
5. `ready -> IDLE` / Ready until actual capture fact selects LISTENING; `degraded -> IDLE` / persistent Ready with limitations and issues; `failed -> ERROR` persistent until recovery; `cancelled -> STOPPING` until cleanup snapshot, then IDLE. `startup.degraded` at announcing updates issues but does not terminate phase. Healthy `startup.completed` alone is neither app success nor audible welcome proof.

Workspace report has per-app status/ms/detail or error, but current progress SSE carries phase only; outcomes may remain unavailable until a future sanitized snapshot/report integration. Display **Apps: status unavailable**, pending or actual per-app result; never all-apps-ready inferred from Core readiness. Cancellation keeps already launched apps running, with no implicit rollback. Session cleanup waits for pending workspace/voice work; runtime stopped/control acknowledgement is not cleanup complete.

Timing is Python/config-owned. The healthy **cached** welcome targets `music_started + welcome_delay_seconds` (default 2 s); ducking begins early, health/services may make it later. Live/fallback follow readiness/deadlines, not this delay. UI adds **no two-second delay**, never accelerates service/audio timers, and cannot infer progress from elapsed time.

| Sequence | What it means | Policy |
|---|---|---|
| full | Visual-only | Actual phase ticks settle ≤320 ms; fresh confirmed welcome can modulate rings; music uses phase text. No cinematic wait. |
| reduced | Visual-only | Instant static glyph/ticks/text, no loops or audio geometry, same runtime timing. |
| fast | Visual-only | Decorative transitions ≤120 ms only; OS reduced/disabled wins. No shortened runtime/audio sequence. |
| muted | Visual-only scenario | Text-complete view for actual silent/muted/unavailable output. **No new startup mute option or audio API.** music_volume=0 does not imply muted welcome; unknown output says unconfirmed. Selecting a test scenario sends no mute/command. |
| interrupted | Observation of existing runtime control | Existing sleep/sequence.cancel may interrupt startup; on confirmed cancellation fence old generation, zero levels, STOPPING until cleanup. No added stop API; request acceptance is not completion. |

Reconnect restores current phase/outcomes without triggering commands, replaying startup or replaying welcome/music. No forced modal or new launch outcome event is asserted available.

## Accessibility, motion and hidden work

Precedence: explicit user disabled > OS `prefers-reduced-motion` > user reduced > full. Proposed adjustable local presentation preference: amplitude multiplier [0,1], decorative transition multiplier [.5,1], still ≤320 ms; never speed loops or override OS preference. S02 adds no settings UI/store.

Reduced and disabled keep static ring/dash/glyph/label and update only semantic state/phase; no continuous breath/rotation, waveform, glow pulse or level redraw. No deliberate flashes/strobes/alternating full-field luminance (**0 flashes/s**). Do not confuse flash rate with telemetry/render cadence or cap necessary text changes at three per second.

One shared semantic announcer: `role=status`, polite, deduplicate, at most one announcement per 1000 ms, latest pending status only. Never announce amplitude/frame samples; preserve persistent failure detail. Caption text remains independently visible. Verify screen-reader behavior, contrast, monochrome cues, high DPI/zoom and keyboard behavior downstream; no human approval exists here.

Hidden windows stop RAF/animations and level work; coalesce semantic facts and retain fences, expire holds. On visible install current snapshot and reset local interpolation, not shared audio state; no catch-up success/startup/welcome animation. Existing native pass-through and focus ownership remain untouched.

## Deterministic contract traces

JSON `traces` are declarative test-only examples, not an implementation of the future machine, a device stream, production fixtures or human approval. Defaults for omitted fields: one live valid session/generation/playback, contiguous sequence, fresh output and no competing facts unless stated; traces isolate a policy rather than describing a wire schema.

| Trace | Declared expectation |
|---|---|
| generated-not-output | Cache speech.started stays static THINKING, level=0, output unconfirmed. |
| stale-level | Age 251 ms clears .6 to 0, static underlying THINKING, level-stale. |
| duplicate-seq / out-of-order | Duplicate 4 / lower 3 after 5 leaves .5, no accepted changes/freshness extension. |
| disconnect-during-speaking | Loss preempts speaking; later amplitude cannot leave Offline, level=0. |
| cancel-late-amplitude | Cancel seq9 fences generation; seq10 amplitude, seq11 new playback ID, seq12 success ignored; IDLE=0. |
| invalid-level / gate-boundaries | NaN/±Infinity reject; -.2/1.7 clamp 0/1; exact gate hysteresis above. |
| envelope-cases | Six numerical values above; terminal end clears envelope/gate to zero. |
| session-replacement | Snapshot b replaces a; old a seq999 cannot revive speaking, interpolation reset. |
| reconnect-snapshot / snapshot-missing | Restores announcing at watermark20, drops seq19, zero level/no startup, welcome or command replay; missing after 2001 ms remains Offline. |
| startup-partial | Issues at announcing keep STARTING; degraded then IDLE with limitations, apps unknown, not all operational. |
| startup-muted | Authoritative mute test-only scenario: STARTING/static/0, no new API/command. |
| startup-interrupted / candidate-expired | Cancelled startup stays STOPPING and ignores old amplitude, apps not rolled back; expired candidate alone returns IDLE. |
| startup-failed | Persistent ERROR, level=0. |
| command-failed-turn-handled | Failed tool followed by handled agent ok=true remains ERROR, no success inference. |

## Downstream evidence and slice boundaries

- T02: `ui/node-tests/reactive-core-spec.test.ts` uses a built-in Node test-local validator against tracked JSON/tokens, malformed-contract mutations and arithmetic/trace checks. No dependency or production reducer. Tests resolve files relative to `import.meta.url` and never read ignored workflow/skill paths. The narrow companion skill is `.claude/skills/jarvis-audio-visualization/SKILL.md` (YAML name/description). It is local-only under the repository's existing `.claude/` ignore policy, not a tracked/portable dependency of the tests; no ignore/discovery settings were changed. Discovery depends on the host's project-skill configuration; file presence is not proof of activation and tests do not inspect host settings. Its scope is audio guardrails only, not a copy of Phase 1 design or orchestration skills.
- S03: typed machine, lifecycle identity/order/fences/snapshot parity; transport mirrors and no-command reconnect tests.
- S04: actual ten-state/four-variant rendering, screenshots/static/reduced/disabled, contrast, zoom, focus, captions, native overlay controls/pass-through. Browser smoke is deferred because S02 changes no rendered UI.
- S05: actual device-write output telemetry, cache/live/fallback coverage, cancellation/unplug/timeout, timestamp/latency/drain limits; no microphone duplication.
- S07: real startup synchronization and configured asset sequences, partial/muted/interrupted/reconnect cases. S06 is not claimed as the startup slice here.
- Later slices own `docs/architecture/reactive-visual-core.md`, audio synchronization/performance measurements and final report. S02 is specification/arithmetic evidence only, not hardware/native GUI/performance/subjective acceptance and not requirement closure.

## Failure Modes

No new service is called. Future bridge/network loss is immediately static DISCONNECTED; missing/malformed initial or reconnect snapshot remains DISCONNECTED through the proposed 2000 ms deadline, with no automatic activate/replay. Missing/stale/malformed output resets to truthful static underlying activity, never fake speaking; confirmed device error keeps ERROR until recovery. Services/TTS/media/filesystem/app failure remain Python-owned reports, not browser retries or asset loading. Absent app detail stays unknown. Missing skill/Graphify is reported, not fabricated. Existing source/test files fail visibly if absent or malformed; verification commands surface nonzero exits. These are design failure paths, not newly implemented containment.

## Load Profile

Expected proposed load: one live session/output stream, shared ≤30 Hz levels, a few windows at ≤60 Hz visible and 0 Hz hidden. At 10× samples/subscribers, synchronous EventHub/IPC observer and renderer scheduling saturate first; keep audio-thread publication out, one latest-value slot, one shared envelope, bounded element/ring budgets, no React frame state, and no hidden/reduced level redraw. These are downstream protections, not measurements or performance claims. Static spec checks are bounded by ten states, four variants and finite traces.

## Negative Tests

Current unchanged `ui/node-tests/orb-model.test.ts` protects its legacy eight-state model against unknown/empty states, unrelated events/no-op completion, huge/zero/negative/NaN dt and out-of-range parameters. It does **not** validate the new device contract. JSON declares stale/duplicate/out-of-order, invalid levels, gate boundaries, cancellation with late amplitude/new playback ID/success, disconnect-over-speech, replacement, missing snapshot, no-replay reconnect, partial/muted/interrupted/failed startup and failed-tool/handled-turn examples. `ui/node-tests/reactive-core-spec.test.ts` now validates exact sets, real token references, finite/bounded motion/audio/cadences, units and time-based envelope arithmetic. Its 46 deliberate malformed copies cover missing/duplicate states/variants/tokens, missing cues/fields, negative/nonnumeric/NaN/infinite/zero parameters, unbounded cadence, lifecycle precedence/cancel/order/fallback omissions, startup/command replay and reduced/disabled/OS-preference gaps. The valid contract check ties trace stimuli to declarative expectations and arithmetic, including gate edges, terminal reset, partial outcomes and snapshot watermark/timeout boundaries. No claim that declarative traces exercise a runtime reducer or device.
