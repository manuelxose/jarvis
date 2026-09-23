# Cloud voice consolidation: Alibaba Qwen only — engineering report

This session's task was explicit and decisive: replace Jarvis's multi-provider
cloud voice stack (MiniMax, Fish Audio, ElevenLabs) with a single cloud
provider, Alibaba Cloud Model Studio (Qwen), with local fallback
(`faster-whisper` for STT, Windows SAPI for TTS). This is a product decision,
not a measured pick — it explicitly supersedes Phase 02's bake-off gate (see
`docs/engineering/windows-tts-bakeoff.md`'s own rule, "no provider is declared
the default without these numbers," and the M005 report's item 5, which
correctly refused to pick a default without bake-off data). That refusal
stands for a measured decision; this is a directed one, recorded here and in
`.planning/STATE.md`/`ROADMAP.md`.

No prior bake-off data existed for Alibaba (it was never a bake-off
candidate), so there is no regression in rigor to account for — this is new
provider work, not a shortcut around existing evidence.

## 1. What changed

- **Removed** from the production path, tests, docs, and env-var surface:
  `src/jarvis/adapters/tts/{minimax,fish_audio,elevenlabs}.py`,
  `scripts/tts_bakeoff.py`, `scripts/elevenlabs_clone_voice.py`,
  `docs/engineering/windows-tts-bakeoff.md`, and their dedicated tests.
  `MINIMAX_API_KEY`, `FISH_AUDIO_API_KEY`, `ELEVENLABS_API_KEY` are no longer
  read anywhere. The only cloud voice secret is now `DASHSCOPE_API_KEY`.
- **Added**: `src/jarvis/adapters/_dashscope.py` (central region/auth
  resolver — single place that knows about `{workspace_id}.{region}.maas
  .aliyuncs.com`, per the "do not scatter endpoint URLs" requirement),
  `src/jarvis/adapters/stt/alibaba_qwen.py` (`AlibabaQwenSTT`),
  `src/jarvis/adapters/tts/alibaba_qwen.py` (`AlibabaQwenTTS`),
  `src/jarvis/adapters/stt/fallback.py` (`STTChain` — did not exist before;
  STT had no fallback chain at all, only a single configured provider).
- **Wired, for the first time**: `_build_real_runtime` now builds an actual
  fallback chain (`TTSChain`/`STTChain`) when the configured provider is
  `alibaba_qwen` — Alibaba primary, local fallback (SAPI/whisper) automatic.
  Per the M005 report's own "Remaining limitations" (item 11), `TTSChain`
  existed but was never wired into the real runtime before this session;
  that gap is closed here, and `STTChain` is new.
- **Config**: `stt.provider`/`tts.provider` accept `alibaba_qwen`; new
  `alibaba.{region, workspace_id, stt_model, tts_model}` settings
  (`src/jarvis/config.py`), reusing the existing `stt.api_key`/`tts.api_key`/
  `stt.language`/`tts.voice` fields rather than duplicating them.
- **Provisioning CLI**: `scripts/alibaba_voice_clone.py` (`create`/`status`/
  `test` subcommands) — validates reference audio (duration, channels, sample
  rate, bit depth, clipping, near-silence via stdlib `wave`+`audioop`, no new
  dependency), uploads it, registers the cloned voice, and can synthesize a
  test phrase with it. Mirrors the deleted `elevenlabs_clone_voice.py`'s
  one-shot-provisioning shape.
- **New dependency**: `dashscope==1.27.6` (official Alibaba SDK) added to
  `requirements.txt`. This is the one new dependency this session added —
  justified because there is no stdlib or already-installed way to speak the
  realtime WebSocket task protocol safely, and the alternative (hand-rolling
  that protocol from prose docs) was assessed and rejected as too
  error-prone; see the research doc's §9 for why.

## 2. Research method (why the model ids/endpoints should be trustworthy)

The task itself warned against hardcoding a stale model id. Three research
passes were done before writing adapter code:

1. A background agent fetched Alibaba's live English Model Studio docs
   (`docs/engineering/alibaba-qwen-voice-research.md`, §1-§7) — found real
   model ids (`qwen3-asr-flash-realtime`, `qwen3-tts-flash-realtime`,
   `qwen-audio-3.0-tts-flash`), but flagged the WebSocket JSON wire protocol
   as unverified (docs describe it in prose, not literal payload examples).
2. A follow-up pass confirmed an official `dashscope` PyPI SDK exists and
   wraps that protocol — but its exact class/method signatures again came
   from secondary summaries, not primary source.
3. Rather than trust a second-hand summary for code that will run in
   production, this session `pip install`ed `dashscope==1.27.6` into a
   throwaway venv and **read the installed package source directly**
   (`docs/engineering/alibaba-qwen-voice-research.md` §9). Every class name,
   method signature, and the region/workspace URL-construction logic in the
   two adapters is copied from that ground-truth read, not inferred from
   documentation prose. This caught a real bug risk before it shipped: the
   realtime TTS class's own constructor defaults to the Beijing legacy host
   regardless of `dashscope.set_region()`, so `AlibabaQwenTTS` passes
   `url=dashscope.base_websocket_api_url` explicitly — silently talking to
   the wrong region was the failure mode this check ruled out.

What is still genuinely unverified (listed exhaustively in the research
doc's "Unverified" sections, not hidden): the exact accepted values for the
TTS `language_type` parameter, whether `qwen3-tts-flash-realtime` (the
non-cloning model) itself supports Spanish output as opposed to the cloning
`-vc-` family, the ASR `language_hints` kwarg name, and whether voice
enrollment's `url` parameter accepts anything beyond an `oss://`/`http(s)://`
reference. These are the first things to check against a live account before
production use — they fail as typed `ProviderConfigError`/`ProviderUnavailable`
rather than silently, per the adapters' error mapping, but "fails clearly" is
not the same as "verified correct."

## 3. What is tested vs. blocked

**Unit-tested** (`tests/test_stt_provider.py`, `tests/test_tts_provider.py`,
`tests/test_stt_fallback.py`, `tests/test_config.py`, `tests/test_cli.py`):
config parsing and validation (region choice, workspace-id requirement,
provider allowlist), `resolve_stt`/`resolve_tts` wiring, availability
booleans, typed-error paths (missing key/voice/workspace), and — the part
worth calling out — the actual threading-callback-to-asyncio bridge in both
adapters, exercised against a fake `dashscope.audio.asr.Recognition`/
`dashscope.audio.qwen_tts_realtime.QwenTtsRealtime` that fires its callbacks
from a real background thread (not just an inline call), the same concurrency
shape the real SDK uses. `STTChain` gets the same fallback/circuit-breaker/
mid-stream-commit test coverage `TTSChain` already had.

**BLOCKED**, same as every prior session's cloud-voice work in this
repository (`docs/engineering/2026-09-21-api-first-voice-pipeline-report.md`
§4, §8, §10): this is a Linux/WSL2 development environment with no DashScope
account, no API key, no workspace id, and no Windows audio hardware. No real
WebSocket call to Alibaba has been made. No latency numbers
(`speech_end_to_first_audio_ms`, connection/first-partial/first-audio timing)
exist for this integration. Real validation requires an operator running
`scripts/alibaba_voice_clone.py` and the `doctor`/`run` commands on the
target Windows machine with real `DASHSCOPE_API_KEY`/
`ALIBABA_MODEL_STUDIO_WORKSPACE_ID` values, per this doc's §2 unverified list.

## 4. Not done in this session (explicitly out of scope, not forgotten)

- Streaming-TTS integration with live LLM token chunking end-to-end over a
  real network call (the sentence-chunking mechanism already exists in
  `TurnManager` from M005 and needs no adapter-side change, but has not been
  exercised against a real Alibaba connection).
- Barge-in-specific cancellation timing measurement against real audio
  hardware (the adapters do react to `TurnContext.cancellation` and tear down
  the WebSocket session immediately — see `synthesize`'s cancellation check —
  but "<150ms reaction time" is a hardware measurement, not a unit-test one).
- Rate-limit-triggered backoff behavior against Alibaba's actual 429-shaped
  responses (the adapters map non-transient errors to `ProviderConfigError`
  and everything else to `ProviderUnavailable`, which `TTSChain`/`STTChain`
  already retry-then-fallback on generically; no Alibaba-specific rate-limit
  response shape has been seen for real).
