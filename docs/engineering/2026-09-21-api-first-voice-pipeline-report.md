# API-first low-latency voice pipeline: engineering report (interim)

Tracked as GSD milestone M005. This is an interim report: it documents what
this session implemented and verified with unit tests, and is explicit about
what remains blocked on real API credentials and real Windows hardware,
which this development environment (Linux/WSL2, no provider accounts) does
not have. No benchmark numbers in this report are fabricated; anything not
measured says so.

## 1. Previous architecture

Jarvis v2 (before this session) was already largely API-first for text
generation: `src/jarvis/adapters/models/fallback.py`'s `ProviderChain`
already routed conversational turns through a cloud-first OpenAI-compatible
provider with local Ollama fallback (GSD milestone M004). TTS had a single
configured provider (`local` XTTS, `sapi` Windows built-in, or `elevenlabs`
cloud) with no fallback chain. STT was local `faster-whisper` only. The
turn pipeline already streamed LLM tokens into sentence chunks and into TTS
before playback (`TurnManager._handle_model`), and already propagated
cancellation for barge-in (`TurnContext.cancellation`, `TurnManager.interrupt`).
Two concrete defects existed in this already-decent design:

- `_handle_model` called `self._model.generate()` **twice** per turn: once to
  stream audio, once more afterward just to collect the transcript. This
  doubled LLM API cost on every conversational turn and could log a
  transcript that did not match what was actually spoken, for any
  non-deterministic provider.
- `LocalTTS` (XTTS-v2) reconstructed its full neural TTS engine on every
  `synthesize()` call instead of caching it, unlike `WhisperSTT` which
  already cached its model. (Found and fixed in the prior session; see
  `docs/superpowers/specs/2026-09-21-openjarvis-comparison.md`.)

## 2. Final architecture (this session's changes)

- **Turn manager**: single `model.generate()` call per turn via a tee of the
  sentence-chunk stream (`turn_manager.py`).
- **Router**: `AGENT_CUES` extended so the spec's agentic-escalation examples
  (revisar proyectos, analizar repositorio, preparar informe, terminar
  tarea) route to Hermes; simple requests still do not.
- **Local ack-audio cache**: `AckAudioCache` (`adapters/tts/ack_cache.py`)
  serves pre-generated audio for the fixed fast-command acknowledgements
  with zero TTS provider calls. Generated offline by
  `scripts/generate_ack_cache.py`; wired into `TurnManager`/`_build_real_runtime`.
- **TTS provider abstraction**: `TextToSpeech` protocol already existed
  (`core/contracts.py`); added a fallback chain, `TTSChain`
  (`adapters/tts/fallback.py`), mirroring `ProviderChain`'s pre-first-byte
  commitment rule, backed by a new generic `CircuitBreaker`
  (`core/circuit_breaker.py`).
- **New TTS adapters**: `MiniMaxTTS` (SSE streaming T2A HTTP) and
  `FishAudioTTS` (v3 sync HTTP), alongside the existing `ElevenLabsTTS`,
  all implementing `TextToSpeech`. `resolve_tts()` accepts all three by
  configuration (`tts.provider: minimax|fish_audio|elevenlabs|local|sapi`).
- **Cost telemetry**: `observability/cost.py` — `CostTracker` records
  per-call usage (tokens, characters, audio-seconds) against a caller-supplied
  pricing table and reports per-interaction/per-minute/projected rollups.
  Not yet wired into the live turn pipeline (see Limitations).
- **TTS bake-off harness**: `scripts/tts_bakeoff.py` +
  `docs/engineering/windows-tts-bakeoff.md`, mirroring the existing
  `model_bakeoff.py` secret-safe evidence pattern for MiniMax/Fish
  Audio/ElevenLabs.
- **Cartesia adapter, STT remote-provider evaluation, routing profiles
  (FAST/CHEAP/QUALITY/AUTO), offline-mode systematic audit**: scoped as GSD
  slices S06/S09/S10/S11 but deliberately left as sketches, not implemented,
  pending either provider-selection decisions or real bake-off data (see
  Limitations and Remaining work).

Hermes remains outside the normal fast path exactly as before: it is invoked
only by `Router` returning route `"hermes"`, never on the fast-command or
fast-model paths.

## 3. Removed bottlenecks

- Duplicate LLM `generate()` call per conversational turn (2x → 1x API calls,
  and a correctness fix: the logged transcript now provably matches the
  played audio).
- Fast-command TTS round trip: cached acknowledgements now play with zero
  network calls once `scripts/generate_ack_cache.py` has been run once by
  the operator with a real TTS provider configured.
- (From the prior session, already shipped:) `LocalTTS` no longer reloads
  the XTTS-v2 model on every turn.

## 4. Provider benchmark table

**BLOCKED.** No MiniMax, Fish Audio, Cartesia, or Deepgram/OpenAI-STT API
keys are available in this development environment. `scripts/tts_bakeoff.py`
is built and unit-tested (`tests/test_tts_bakeoff.py`, fakes only) but has
never been run against a real provider. Real numbers require an operator
with API keys running it on the target Windows machine per
`docs/engineering/windows-tts-bakeoff.md`.

## 5. Selected TTS provider

**Not selected.** Per the project's own decision rule ("select the default
using measured production-relevant data," 35% latency / 25% quality / 20%
cost / 10% reliability / 10% integration), no provider can be declared
default without item 4's data. `tts.provider` remains operator-configurable;
`elevenlabs` is the only one with any production evidence (voice cloning
already set up in a prior milestone), so it is the incumbent by default, not
a benchmarked winner.

## 6. Selected STT provider

**Not evaluated this session.** Local `faster-whisper` remains the only STT
path. It already meets the documented acceptance bar
(`docs/engineering/windows-voice-acceptance.md`: transcription ≤2s for a
~4s Spanish utterance on the target CPU), so this was correctly deprioritized
below the TTS work this session, per GSD slice S09 (sketch, blocked on which
remote STT provider the operator wants credentials for).

## 7. Model routing

Unchanged in shape, extended in coverage:

- `fast_command` — deterministic regex/grammar match, no LLM call
  (`FastCommandClassifier`).
- `fast_model` — conversational default, cloud-first via `ProviderChain`
  with local Ollama fallback.
- `hermes` — agentic/multi-step cues (extended this session) or an optional
  pluggable `IntentClassifier` signalling `"agent"`.

TTS-side routing profiles (FAST/CHEAP/QUALITY/AUTO) are **not implemented**;
they are GSD slice S10, explicitly blocked on S08's real measurements
("AUTO... cannot be finalized until S08 produces real measurements" — this
is a direct constraint from the project's own decision rule, not an
oversight).

## 8. Latency before/after

**BLOCKED.** This Linux/WSL2 host has no microphone, speakers, or WASAPI
stack; per `docs/engineering/windows-voice-acceptance.md`, hardware timing
evidence has always required the target Windows machine. No before/after
wall-clock numbers are reported here. What changed measurably in-process:
one fewer LLM round trip per conversational turn, and zero TTS round trips
for cached fast-command acknowledgements — both provable from call counts in
`tests/test_turn_manager.py`, not from wall-clock milliseconds.

## 9. Cost estimates

Cost telemetry infrastructure (`observability/cost.py`) is built and unit
tested, but:

- It is not yet wired into the live turn pipeline: `ModelProvider.generate()`
  yields plain text tokens with no token-count metadata, and
  `TextToSpeech.synthesize()` yields plain bytes with no character-count
  attached. Wiring real per-turn cost capture requires extending those
  return shapes (or measuring text length as a token-count proxy), scoped as
  follow-up work, not done this session to avoid guessing at an interface
  change without a concrete consumer yet.
- No pricing table is populated. Cartesia's pricing (character-based credits,
  tiered monthly plans) was found via search during this session; MiniMax
  and Fish Audio's current per-character/per-minute rates were not
  confirmed and must be re-checked at the time pricing is actually wired in,
  per the instruction to use current pricing rather than hardcode
  assumptions.

## 10. Real acceptance-test results

| Scenario (from the spec) | Result |
| --- | --- |
| "Jarvis, abre Spotify" — no Hermes, no LLM, immediate | **Verified by unit test.** `test_fast_command_invokes_tool_and_returns_result`, `test_cached_acknowledgement_skips_tts_provider`. Wall-clock "<300ms" is not measured here (needs Windows hardware). |
| "Jarvis, sube el volumen" — local execution | **Verified by unit test** (existing `test_routes_to_fast_command` family). |
| "¿Qué opinas de esta arquitectura?" — fast conversational model, streaming | **Verified by unit test** (`test_fast_model_streams_and_plays`, `test_generates_the_model_response_exactly_once`). Real end-to-end streaming TTS playback is not exercised outside fakes. |
| "Analiza este repositorio y dime por qué falla el build" — escalates to Hermes | **Verified by unit test** (`test_routes_agentic_examples_to_hermes`, added this session — previously this exact phrasing did **not** escalate). |
| Barge-in while Jarvis speaks | **Not newly tested this session.** Existing `TurnManagerCancellationTests` cover cancellation propagation; the <150ms reaction-time target needs real audio hardware to measure. |
| Primary TTS provider fails → automatic fallback | **Verified by unit test** (`tests/test_tts_fallback.py`, 8 cases including circuit-breaker skip). Not yet wired into `_build_real_runtime` as a multi-provider chain (see Limitations). |
| Internet fails → degraded local mode | **Not audited this session.** GSD slice S11 (sketch). |

## 11. Remaining limitations

- `TTSChain` exists and is tested but `_build_real_runtime` still resolves a
  single TTS provider (`resolve_tts`), not a chain — wiring an actual
  multi-provider fallback chain into production is GSD slice S10, correctly
  gated on S08's bake-off deciding provider order.
- Cost telemetry has no live data source yet (see item 9).
- Cartesia adapter (S06), STT remote-provider evaluation (S09), TTS routing
  profiles (S10), and a systematic offline-degraded-mode audit (S11) are
  unimplemented sketches, not partial implementations.
- GSD slice bookkeeping: M005's slices were implemented directly rather than
  through GSD's task-execution loop, so `gsd_slice_complete` could not close
  them out (it requires task-level proof records). The milestone/slice plan
  itself is recorded (`gsd_roadmap` M005); a future session should either
  backfill task records or treat the test suite as the completion evidence.
- MiniMax's SSE final chunk (`status: 2`) is deliberately not forwarded as
  audio, since the docs describe it as possibly containing a redundant full
  aggregate; this is a conservative assumption marked with a `ponytail:`
  comment in `adapters/tts/minimax.py`, not a confirmed behavior — verify
  against a real response before shipping.

## 12. Exact commands to run Jarvis

Unchanged from the existing project procedure (PowerShell, target Windows
machine):

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\bootstrap.ps1 -SkipModelPull
.\run_jarvis.bat
```

To generate the fast-command ack-audio cache once, with a real TTS provider
configured:

```powershell
.\.venv\Scripts\python.exe scripts\generate_ack_cache.py
```

To run the (currently fake-only) TTS bake-off unit tests, or the real
opt-in bake-off with credentials, see
`docs/engineering/windows-tts-bakeoff.md`.

## 13. Exact environment configuration required

`config.local.json` (gitignored; copy from `config.local.example.json`),
values resolved from the process environment via `${VAR}` placeholders:

```json
{
  "tts": {
    "provider": "elevenlabs",
    "voice": "${ELEVENLABS_VOICE_ID}",
    "api_key": "${ELEVENLABS_API_KEY}"
  }
}
```

Environment variables this session's new adapters read (set in the
PowerShell session, never committed):

```
MINIMAX_API_KEY=
MINIMAX_VOICE_ID=          # optional, defaults to English_expressive_narrator
FISH_AUDIO_API_KEY=
FISH_AUDIO_VOICE_ID=       # required — a voice id from Fish Audio's /voices
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=
```

This project does not use a `.env` loader (no `python-dotenv` dependency);
`config.local.json`'s `${VAR}` placeholder resolution against the process
environment is the existing, working mechanism and was reused rather than
introducing a parallel one.
