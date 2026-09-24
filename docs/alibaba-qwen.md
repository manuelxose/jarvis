# Alibaba Cloud Model Studio / DashScope Qwen voice APIs — reference

Background research behind the optional cloud voice adapters
(`src/jarvis/adapters/stt/alibaba_qwen.py`, `src/jarvis/adapters/tts/alibaba_qwen.py`), targeting the Singapore
(international) region. All claims below are sourced from Alibaba Cloud's
official Model Studio documentation (`alibabacloud.com/help/en/model-studio/*`),
fetched 2026-09-21. Model Studio's English docs are not exposed as raw HTML in
a form this research could diff byte-for-byte; each page was fetched live and
cross-checked against a second independent fetch or search hit where possible.
Model ids, especially any with embedded dates (e.g. `-2026-01-15` snapshot
suffixes), should be re-confirmed against the DashScope console model list
immediately before shipping — Alibaba renames and deprecates these models on
a matter of months, and this document is a snapshot, not a contract.

## 1. Realtime streaming ASR model id

The stakeholder guess `qwen-audio-3.0-asr-flash-streaming` is **real** — it
was not invented. Model Studio's real-time ASR guide lists it and a newer
sibling as the recommended streaming ASR models:

- `qwen-audio-3.0-asr-flash-streaming`
- `qwen-audio-3.1-asr-flash-streaming` (newer generation)
- `qwen3-asr-flash-realtime` (separate model family, VAD-capable, configurable
  via `session.turn_detection`)
- `fun-asr-realtime`

Source: [Build Real-Time Speech Recognition with WebSocket & DashScope SDK](https://www.alibabacloud.com/help/en/model-studio/real-time-speech-recognition-user-guide), accessed 2026-09-21.
Also: [Qwen-Audio-3.x-ASR-Flash-Streaming/Fun-ASR-Realtime WebSocket API reference](https://www.alibabacloud.com/help/en/model-studio/fun-asr-realtime-websocket-api), accessed 2026-09-21 — page title and intro paragraph confirm `Qwen-Audio-3.x-ASR-Flash-Streaming` and `Fun-ASR-Realtime` as the two model families this WebSocket endpoint serves.

`paraformer-realtime-v2` is real but is Alibaba's **older-generation** ASR
family; the ASR model overview explicitly says to migrate off it: "Paraformer
is an older-generation ASR model family. Migrate to Fun-ASR or Qwen-ASR when
possible." Source: [Speech-to-text models for real-time and file transcription](https://www.alibabacloud.com/help/en/model-studio/asr-model), accessed 2026-09-21.

No `gummy-realtime` model was found anywhere in Model Studio's docs or in
targeted web searches restricted to `alibabacloud.com`. Treat that name as
not existing on this platform — **unverified/likely wrong**, do not use it.

Region availability: `qwen-audio-3.x-asr-flash-streaming`, `qwen3-asr-flash-realtime`,
and `fun-asr-realtime` are all documented as available in both Singapore
(`ap-southeast-1`) and Beijing (`cn-beijing`). Source: real-time-speech-recognition-user-guide (above), which gives per-region WebSocket configuration blocks for both. By contrast, one fetch of the dedicated Paraformer WebSocket reference stated flatly: "Paraformer is available only in the China (Beijing) region." Source: [WebSocket API for Paraformer real-time speech recognition](https://www.alibabacloud.com/help/en/model-studio/websocket-for-paraformer-real-time-service), accessed 2026-09-21. This is a further reason to avoid `paraformer-realtime-v2` for a Singapore deployment even setting aside the deprecation notice.

**Recommendation for the adapter:** use `qwen3-asr-flash-realtime` (current
generation, VAD support, Spanish in its language list — see §7) or
`qwen-audio-3.1-asr-flash-streaming` as a fallback; do not use `paraformer-realtime-v2`
or the stakeholder's invented `gummy-realtime`.

## 2. Realtime streaming TTS model id

The stakeholder guess `qwen3-tts-flash-realtime` is also **real**. It appears
in the TTS model catalog and in the rate-limit tables:

- `qwen3-tts-flash-realtime`, `qwen3-tts-instruct-flash-realtime` — realtime,
  instruction-controllable Qwen TTS family.
- `qwen-audio-3.0-tts-flash` / `qwen-audio-3.0-tts-plus` — realtime
  Qwen-Audio-TTS family, recommended for instruction control, voice cloning
  supported.
- `cosyvoice-v2`, `cosyvoice-v3-flash`, `cosyvoice-v3-plus` — realtime
  CosyVoice family, recommended for voice design; `cosyvoice-v3.5-*` exists
  but is Beijing-only per the docs fetched.

Sources: [Speech synthesis models — TTS, voice cloning, voice design](https://www.alibabacloud.com/help/en/model-studio/tts-model/), accessed 2026-09-21; [Real-time speech synthesis — Qwen TTS, Qwen-Audio-TTS/CosyVoice streaming](https://www.alibabacloud.com/help/en/model-studio/realtime-tts-user-guide), accessed 2026-09-21; rate-limit figures corroborating these ids as live/billed models: [Rate limiting](https://www.alibabacloud.com/help/en/model-studio/rate-limit), accessed 2026-09-21 ("Qwen3-TTS-Flash-Realtime (Singapore & Beijing): 180 requests per minute").

Region availability: `qwen3-tts-flash-realtime` was described in one fetch as
available on the "international (Singapore)" endpoint; the rate-limit page
lists it for both Singapore and Beijing. `qwen-audio-3.0-tts-*` and
`cosyvoice-v2`/`cosyvoice-v3-plus` are documented for both regions;
`cosyvoice-v3.5-*` is Beijing-only. Treat the exact Singapore-vs-Beijing
matrix for every model as **needing a live console check** — the two source
pages did not fully agree on which Qwen-Audio-Realtime variant is Singapore
vs Beijing-only (see Unverified section).

**Recommendation for the adapter:** `qwen3-tts-flash-realtime` for low-latency
realtime synthesis is a real, current, billed model id and a reasonable
default; confirm on your own DashScope console that it is enabled for your
account's Singapore workspace before hardcoding it (Model Studio model
enablement can be per-account/per-region, see §4).

## 3. Voice cloning

Voice cloning is a **separate operation, not a parameter of the realtime
synthesis call**, and per Alibaba's own docs, **the cloned voice can only be
used with the same model family it was cloned for** — a voice id from one
model does not carry over to another.

Workflow, as documented:

1. Call an enrollment endpoint using a dedicated pseudo-model,
   `"model": "voice-enrollment"`, with `"action": "create_voice"` (or
   `"create"` for the Qwen-TTS family), specifying the **target synthesis
   model** the voice will be bound to (e.g. `qwen-audio-3.0-tts-flash`,
   `cosyvoice-v3.5-plus`), plus the reference audio (URL or base64) and a
   user-chosen name/prefix.
2. The response returns a `voice_id` (the docs show an example shaped like
   `qwen-audio-3.1-realtime-plus-myvoice-xxxxxx`).
3. That `voice_id` string is passed as the `voice` parameter on subsequent
   synthesis calls to the **same** target model named in step 1.

Source: [Voice cloning — Create custom voices from audio samples](https://www.alibabacloud.com/help/en/model-studio/voice-cloning-user-guide), accessed 2026-09-21. Direct quote captured from that page: "voice cloning and speech synthesis must use the same model." Voice quota noted: up to 1,000 cloned voices per account per model family, auto-deleted after one year of inactivity (same source).

Whether `qwen3-tts-flash-realtime` itself accepts cloned voice ids, or
whether cloning for the Qwen3-TTS family requires a distinct
`qwen3-tts-vc-realtime` model, was **not consistently answered** by the pages
fetched:
- The TTS model catalog page listed cloning support separately under a
  `qwen3-tts-vc-realtime-<date>` snapshot family (e.g.
  `qwen3-tts-vc-realtime-2026-01-15`), distinct from plain
  `qwen3-tts-flash-realtime`, with its own language list (Spanish, Japanese,
  Korean, French, Russian). Source: tts-model/ (above).
- The voice-cloning guide's region/model table separately listed
  "Qwen3-TTS: Beijing and Singapore" and "`qwen3-tts-vc` variants" as the
  binding target, without stating plainly whether `qwen3-tts-flash-realtime`
  (non-vc) also accepts a `voice_id` from cloning.

**Treat this as needing explicit confirmation before implementation**: if the
adapter needs cloned-voice support, plan for a distinct `qwen3-tts-vc-realtime*`
model id (dated snapshot) as the synthesis target for cloned voices, separate
from the general-purpose `qwen3-tts-flash-realtime` id used for stock voices,
and verify the exact current snapshot id against the live console/model list
— dated snapshot ids rotate and this document cannot guarantee which is
current on your account.

CosyVoice (`cosyvoice-v2`, `cosyvoice-v3-plus`, `cosyvoice-v3.5-*`) and
Qwen-Audio-TTS (`qwen-audio-3.0-tts-flash/plus`) both independently support
voice cloning through the same `voice-enrollment` mechanism, bound to
themselves respectively. Source: tts-model/ (above).

## 4. Auth

`DASHSCOPE_API_KEY` (set as an environment variable, `Authorization: Bearer
<key>` on the wire) is the credential used throughout Model Studio's official
examples, including for the realtime WebSocket ASR/TTS services. Source:
[How to obtain an API key](https://www.alibabacloud.com/help/en/model-studio/get-api-key) and [Model Studio Base URL | API endpoints by region and plan](https://www.alibabacloud.com/help/en/model-studio/base-url), both accessed 2026-09-21.

Critically, **API keys are region-scoped and non-portable**: "Each region has
its own endpoint, API Key, and model list, and these cannot be used across
regions... an API key from another region is rejected with an authentication
error." A Beijing-issued key will not work against the Singapore endpoint and
vice versa — this is a real production failure mode to guard against (fail
fast with a clear error, do not silently retry against the other region).
Source: base-url (above, via search-result quote); corroborated by the
real-time ASR guide's regional config blocks noting "The API Key differs
between the Singapore and Beijing regions." Source: real-time-speech-recognition-user-guide (§1, above).

Workspace id: this differs by which **domain** you call:

- On the newer, recommended **workspace-dedicated domain**
  (`{WorkspaceId}.{region}.maas.aliyuncs.com`), the workspace id is embedded
  directly in the hostname, not sent as a separate header — it is mandatory
  to route the call at all. Source: real-time-speech-recognition-user-guide
  and cosyvoice-websocket-api pages (§5, below), which both show this URL
  pattern with `{WorkspaceId}` as a required path/host substitution.
- On the **legacy DashScope domain** (`dashscope-intl.aliyuncs.com` /
  `dashscope.aliyuncs.com`), an `X-DashScope-WorkSpace` header is documented
  as present but multiple fetches described it inconsistently as "optional"
  vs. simply "a header you may need for workspace-scoped calls." This
  research could not find a single primary-source sentence stating flatly
  "X-DashScope-WorkSpace is required/not required for the Singapore realtime
  websocket endpoint specifically" — **mark this unverified** and confirm
  against your own account (regions Germany/Tokyo/Hong Kong/Virginia require
  explicit workspace selection per Model Studio's regions doc, whereas
  Beijing and Singapore each expose only one deployment scope and the docs
  say "no selection is needed" for those two — which suggests the header may
  be unnecessary for Singapore specifically, but this is an inference, not a
  direct quote). Source for the "one deployment scope, no selection needed"
  claim: [Regions and endpoints](https://www.alibabacloud.com/help/en/model-studio/regions), accessed 2026-09-21 (via search-result summary; re-fetch directly before relying on it).

**Recommendation:** use the workspace-dedicated `{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com`
domain for the adapters — it makes the workspace id an explicit, required
config value (no ambiguity about an optional header), and Alibaba's own docs
present it as the recommended production domain (see §5).

## 5. Transport

Confirmed: these are **WebSocket** APIs (`wss://`), not HTTP/SSE. Multiple
independent pages state the handshake is validated via
`Authorization: Bearer <key>` during the WebSocket upgrade, returning HTTP
401/403 on failure. Sources: [WebSocket API for Paraformer real-time speech recognition](https://www.alibabacloud.com/help/en/model-studio/websocket-for-paraformer-real-time-service); [Qwen-Audio-TTS/CosyVoice speech synthesis WebSocket API](https://www.alibabacloud.com/help/en/model-studio/cosyvoice-websocket-api); [Qwen-Audio-3.x-ASR-Flash-Streaming/Fun-ASR-Realtime WebSocket API reference](https://www.alibabacloud.com/help/en/model-studio/fun-asr-realtime-websocket-api). All accessed 2026-09-21.

Two distinct hostname schemes are documented, and they are **not
interchangeable**:

1. **Workspace-dedicated domain (recommended for production)**:
   `wss://{WorkspaceId}.{region}.maas.aliyuncs.com/api-ws/v1/inference`
   (region codes seen: `ap-southeast-1` for Singapore/international,
   `cn-beijing` for mainland China). This domain also supports HTTP, SSE,
   WebRTC, and "AOQ" (AI over QUIC). Source: [Model Studio Base URL | API endpoints by region and plan](https://www.alibabacloud.com/help/en/model-studio/base-url), accessed 2026-09-21.
2. **Legacy DashScope domain**: `dashscope-intl.aliyuncs.com` (Singapore/
   international) vs `dashscope.aliyuncs.com` (Beijing/mainland China), with
   a third, `cn-hongkong.dashscope.aliyuncs.com`, for Hong Kong. This domain
   supports HTTP, SSE, and WebSocket (no WebRTC/AOQ). Same source.

So the stakeholder's implicit assumption of `dashscope-intl.aliyuncs.com` for
Singapore is directionally correct for the legacy domain, but Alibaba's
current guidance favors the workspace-dedicated `*.ap-southeast-1.maas.aliyuncs.com`
domain for new production integrations. **Pick one domain scheme
deliberately** — do not mix `{WorkspaceId}` path-style calls with
`dashscope-intl.aliyuncs.com` header-style calls in the same adapter.

## 6. Rate limits, timeouts, reconnect guidance

Documented rate limits (from [Rate limiting](https://www.alibabacloud.com/help/en/model-studio/rate-limit), accessed 2026-09-21):

- `qwen3-asr-flash-realtime`, `fun-asr-realtime`: 20 requests/second (RPS),
  Singapore and Beijing.
- Qwen-Audio-TTS realtime family: 60 calls/minute (RPM), 100,000 tokens/minute
  (TPM), Singapore and Beijing.
- `qwen3-tts-flash-realtime`: 180 requests/minute (RPM), Singapore and
  Beijing.
- Rate limiting is applied **at the Alibaba Cloud account level**, aggregated
  across all RAM users, workspaces, and API keys under that account — not
  per-key. To raise the limit you must contact Alibaba Cloud. Source: same
  page (via search-result quote; re-fetch directly to confirm current
  numbers before capacity planning, these change).

Explicit connection-timeout or max-session-duration numbers for the
WebSocket ASR/TTS endpoints were **not found** in any page fetched. The one
operational guidance found was for TTS: "Reuse the WebSocket connection
across tasks instead of creating a new one for each," and that all events in
one synthesis task (`run-task`, `continue-task`, `finish-task`) must share
the same `task_id`. Source: [Qwen-Audio-TTS/CosyVoice speech synthesis WebSocket API](https://www.alibabacloud.com/help/en/model-studio/cosyvoice-websocket-api), accessed 2026-09-21. No equivalent session-reuse statement was found for the ASR side in the pages fetched.

A separate page exists specifically for high-concurrency guidance —
[Performance optimization for real-time speech recognition in high-concurrency scenarios](https://www.alibabacloud.com/help/en/model-studio/paraformer-in-high-concurrency-scenarios)
— found via search but not fetched in this pass; it is Paraformer-titled, so
its applicability to `qwen3-asr-flash-realtime` is unconfirmed. Worth reading
before production rollout if you expect many concurrent sessions.

## 7. Spanish language support

**ASR**: `qwen3-asr-flash-realtime`'s documented language list explicitly
includes Spanish, alongside Chinese dialects, English, Japanese, German,
Korean, Russian, French, Portuguese, Arabic, Italian, Hindi, and others.
Source: [Speech-to-text models for real-time and file transcription](https://www.alibabacloud.com/help/en/model-studio/asr-model), accessed 2026-09-21 (verbatim-quote fetch of the model's language row). By contrast, `fun-asr-realtime`'s documented language list (Chinese dialects, English, Japanese) does **not** include Spanish, per the same page. `qwen-audio-3.x-asr-flash-streaming`'s own realtime-mode language list, per the streaming user guide, was stated only as "Mandarin Chinese... plus Cantonese, Sichuanese, and other dialects" with Spanish not mentioned in that guide — this conflicts in scope with the asr-model catalog page and should be re-checked directly against the asr-model page's row for that specific model id before relying on it for Spanish.

**TTS**: Alibaba's TTS model catalog documents Spanish support on:
- `qwen-audio-3.0-tts-plus` / `qwen-audio-3.0-tts-flash` (cloning-capable
  language list includes Spanish, Italian, Malay, Filipino, Arabic, plus
  Chinese dialects).
- `qwen3-tts-vc-realtime-<date>` snapshots (cloning-specific realtime family):
  Spanish, Japanese, Korean, French, Russian.
- Legacy `qwen-tts` / `qwen-tts-latest` (non-realtime, HTTP): Spanish,
  Japanese, Korean, French, Russian.

Source: [Speech synthesis models — TTS, voice cloning, voice design](https://www.alibabacloud.com/help/en/model-studio/tts-model/), accessed 2026-09-21.

Whether the plain `qwen3-tts-flash-realtime` (non-cloning) model itself has
Spanish in its own documented language list, separate from the `-vc-` cloning
variant, was **not confirmed** — one fetch of the realtime TTS user guide
returned no language list for it at all. If Spanish output is a hard
requirement for the TTS adapter and cloning is not otherwise needed, verify
directly on the `realtime-tts-user-guide` or `tts-model/` page which exact
row covers `qwen3-tts-flash-realtime`'s language support, or use
`qwen-audio-3.0-tts-flash`, which is documented with Spanish in its own
catalog row.

## 8. Official SDK

Yes — an official Python SDK exists: **PyPI package `dashscope`**, current version
**1.27.6**, released 2026-09-17 (actively maintained). Source: [PyPI: dashscope](https://pypi.org/project/dashscope/), accessed 2026-09-21. GitHub: [dashscope/dashscope-sdk-python](https://github.com/dashscope/dashscope-sdk-python) (`pip install dashscope`).

It wraps both realtime protocols and hides the raw WebSocket JSON envelope:

- **Streaming ASR**: `from dashscope.audio.asr import Recognition, RecognitionCallback`. Construct `Recognition(model="qwen-audio-3.1-asr-flash-streaming", format="pcm", sample_rate=16000, callback=MyCallback())`, call `.start()`, feed audio via `.send_audio_frame(chunk)`, then `.stop()`. The SDK manages the task lifecycle internally — no manual `run-task`/`continue-task`/`finish-task` JSON. Source: [Qwen-Audio-3.0-ASR-Flash-Streaming/Fun-ASR-Realtime Python SDK](https://www.alibabacloud.com/help/en/model-studio/fun-asr-realtime-python-sdk), accessed 2026-09-21.
- **Realtime TTS**: `from dashscope.audio.qwen_tts_realtime import *`, with a `QwenTtsRealtime` class driven by a `QwenTtsRealtimeCallback` (`on_open`/`on_close`/`on_event`). `voice` is a constructor/`update_session()` parameter and accepts a cloned `voice_id` string directly. A second, simpler class, `dashscope.audio.tts_v2.SpeechSynthesizer`, also documents `voice="<voice_id>"` for non-realtime/batch synthesis. Sources: search-indexed excerpts of [Real-time speech synthesis (qwen-tts-realtime)](https://www.alibabacloud.com/help/en/model-studio/qwen-tts-realtime) and the SDK's `speech_synthesizer.py` on GitHub — **not independently re-fetched line-by-line in this pass**, treat class/method names as needing a quick confirm against the live file before coding against them.
- **Voice enrollment**: reachable through the SDK too, via a call using model id `"qwen-voice-enrollment"`/`"voice-enrollment"` and `action="create"`/`"create_voice"` (matches §3's HTTP description) — this appeared only as a raw model-call pattern in sources found, **no dedicated `VoiceEnrollmentService` class name was confirmed**.

Region handling: the SDK defaults to the Beijing public endpoint. Call `dashscope.set_region(region="ap-southeast-1", workspace_id="<id>")` once at startup to point HTTP, WebSocket, and OpenAI-compatible base URLs at the Singapore workspace-dedicated domain (`https://{workspace_id}.ap-southeast-1.maas.aliyuncs.com/...`) in one call; base URLs can also be set individually (`dashscope.base_websocket_api_url`, etc). Realtime WebSocket support is documented as available in `cn-beijing` and `ap-southeast-1` **only** — not the other international regions (Frankfurt/Tokyo/Hong Kong/Virginia). Source: GitHub README excerpts fetched 2026-09-21 (`dashscope/dashscope-sdk-python`).

**Recommendation**: take the `dashscope` dependency (pin `>=1.27.0`, current known-good `1.27.6`) rather than hand-rolling the WebSocket protocol with raw `websockets` — it is official, current, actively released, and explicitly supports the Singapore realtime path. Before finalizing, re-fetch `dashscope/dashscope-sdk-python`'s `README.md` and `dashscope/audio/qwen_tts_realtime/` source directly to confirm exact class/method signatures (see Unverified section).

## Unverified / could not confirm

These items could not be pinned to an unambiguous primary-source statement
and must be checked against a live DashScope/Model Studio console or account
before shipping:

- Whether `X-DashScope-WorkSpace` is strictly required, optional, or
  irrelevant for the Singapore legacy-domain (`dashscope-intl.aliyuncs.com`)
  realtime WebSocket endpoints specifically (see §4).
- Whether `qwen3-tts-flash-realtime` (the plain, non-cloning realtime model)
  itself accepts a `voice_id` produced by voice cloning, or whether cloned
  voices are only usable through a distinct `qwen3-tts-vc-realtime-<date>`
  model id (see §3). If true, the adapter needs two TTS model ids: one for
  stock voices, one for cloned voices.
- The exact current dated snapshot id for the voice-cloning-capable Qwen3-TTS
  realtime model (docs showed `qwen3-tts-vc-realtime-2026-01-15` and
  `-2025-11-27` as examples of the naming pattern, not necessarily the
  current default) — snapshot ids rotate and must be read from the live
  model list, not hardcoded from this document.
- Full, authoritative Singapore-vs-Beijing availability matrix for every
  model id named in this document — different pages gave overlapping but
  not perfectly consistent answers (e.g. one source said "Qwen-Audio-Realtime:
  Beijing only," another implied Singapore availability for
  `qwen3-tts-flash-realtime`).
- `qwen-audio-3.x-asr-flash-streaming`'s own realtime-mode Spanish support —
  conflicting scope between the streaming user guide (no Spanish mentioned)
  and the general asr-model catalog page (see §7).
- Connection timeout duration, max session length, and any documented
  reconnect/backoff protocol for the ASR and TTS WebSocket endpoints — no
  primary-source page fetched stated concrete numbers (see §6).
- Applicability of the Paraformer high-concurrency guidance page to the
  Qwen3/Fun-ASR realtime models (not fetched in this pass).
- Exact current RPS/RPM rate-limit figures should be re-read from
  `alibabacloud.com/help/en/model-studio/rate-limit` immediately before
  capacity planning; Alibaba revises these without a stable changelog this
  research could find.
- Exact class/method signatures for `QwenTtsRealtime`/`QwenTtsRealtimeCallback` and any dedicated voice-enrollment helper class in `dashscope-sdk-python` — sourced from search excerpts, not a direct fetch of the GitHub source file (see §8).
- This research relied on an AI-summarizing fetch tool rather than raw HTML
  diffing; where only one fetch produced a given fact and it could not be
  cross-checked by a second independent fetch or search hit, that fact is
  flagged inline above. Before hardcoding any model id or endpoint into the
  adapters, a human should open the cited URLs directly and re-confirm the
  exact strings (model ids are case- and hyphen-sensitive on the wire).

## 9. Official SDK (`dashscope`) — verified against installed package source

Superseding the AI-fetch-based findings above wherever they conflict: the
implementing session `pip install`ed `dashscope==1.27.6` into a scratch venv
and read the actual installed source directly (ground truth, not a docs
summary). This is what the adapters are built against.

- **ASR**: `dashscope.audio.asr.recognition.Recognition` — threading-based
  (not asyncio), constructed with `model`, `callback` (a `RecognitionCallback`
  subclass implementing `on_open/on_event(RecognitionResult)/on_complete/
  on_error/on_close`), `format`, `sample_rate`, `workspace`. `.start()` spawns
  a worker thread and opens the WebSocket; `.send_audio_frame(bytes)` pushes
  PCM chunks; `.stop()` ends the task. No explicit `language` constructor
  param — extra `**kwargs` are merged into the task's `input` params, so
  Spanish is requested via a `language_hints=["es"]` kwarg (matches the
  model's documented language-list mechanism, but the exact kwarg name was
  not independently confirmed against a live call — verify against a real
  transcript before shipping).
- **TTS**: `dashscope.audio.qwen_tts_realtime.qwen_tts_realtime.QwenTtsRealtime`
  — also threading-based, wraps the `websocket-client` package directly.
  `.connect()`, `.update_session(voice=<voice_id>, audio_format="pcm",
  language_type=<code>, ...)`, `.append_text(text)`, `.commit()`, `.finish()`,
  `.close()`. Callback `QwenTtsRealtimeCallback.on_open/on_event(message:
  dict)/on_close`; audio arrives as `on_event` messages with
  `type == "response.audio.delta"` and a base64-encoded `delta` field
  (OpenAI-Realtime-shaped event protocol — confirmed by reading
  `on_message()`'s dispatch, not by a documented example).
  **Important divergence from the default constructor**: `QwenTtsRealtime`'s
  own `url` default is `wss://dashscope.aliyuncs.com/api-ws/v1/realtime`
  (the **Beijing legacy** host) — it does *not* read the region set by
  `dashscope.set_region()`. The adapter must pass `url=dashscope
  .base_websocket_api_url` explicitly after calling `set_region()`, or every
  TTS call silently goes to Beijing regardless of configured region. `.audio.
  asr.Recognition`, by contrast, calls `BaseApi.call()` with no explicit URL
  and does pick up the region-global `base_websocket_api_url`.
- **Voice cloning**: `dashscope.audio.tts_v2.enrollment.VoiceEnrollmentService
  (api_key, workspace).create_voice(target_model, prefix, url,
  language_hints=None)` — plain HTTP (`ApiProtocol.HTTP`), returns a
  `voice_id` string directly, raises `VoiceEnrollmentException` on failure.
  Also has `list_voices()`, `query_voice()`, `delete_voice()`. `url` takes an
  audio URL, not raw bytes — a local file is uploaded first via
  `dashscope.utils.oss_utils.OssUtils.upload(model, file_path, api_key)`,
  which returns an `oss://...` reference the enrollment call accepts.
- **Region/auth wiring**: `dashscope.set_region(region, workspace_id)`
  (`dashscope/__init__.py`) sets the module globals `base_http_api_url`,
  `base_compatible_api_url`, and `base_websocket_api_url` to
  `https://{workspace_id}.{region}.maas.aliyuncs.com/...` /
  `wss://{workspace_id}.{region}.maas.aliyuncs.com/api-ws/v1/inference`.
  `workspace_id` is a hard requirement — `set_region` raises `ValueError` if
  it is empty, which is exactly the "fail startup with an actionable message"
  behavior the adapters need; they just have to call `set_region()` eagerly
  and let that error surface as a `ProviderConfigError`. Supported region
  codes read from `dashscope.common.env.MAAS_REGIONS`:
  `ap-southeast-1, us-east-1, cn-hongkong, eu-central-1, ap-northeast-1`, plus
  `cn-beijing` handled as the legacy default (no `set_region()` call needed
  for Beijing). `dashscope.api_key` is a plain module attribute (defaults
  from the `DASHSCOPE_API_KEY` env var at import time, but the adapters set
  it explicitly from resolved config instead of relying on ambient env,
  matching this codebase's existing explicit-config-injection style).
- Package also transitively depends on `websocket-client` and `requests`;
  no separate `websockets` PyPI dependency is needed once `dashscope` is
  added.

**Still unverified** even after reading the source: the exact JSON shape of
`RecognitionResult`'s Spanish-language output, the accepted values for
`update_session`'s `language_type` parameter, and whether
`VoiceEnrollmentService.create_voice`'s `url` parameter accepts a `data:`
base64 URI directly instead of an `oss://`/`http(s)://` reference (the
installed source only shows the `OssUtils.upload` path). These are cheap to
confirm empirically against a real account and do not block writing adapters
that fail with a clear, typed error if a real call rejects them.
