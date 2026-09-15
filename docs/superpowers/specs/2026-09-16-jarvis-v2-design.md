# Jarvis v2 Design Specification

## Status

Approved conversational design. This document defines the target architecture for a greenfield rebuild of the legacy Jarvis repository. It is not a runtime implementation and does not make GSD Pi a production dependency.

## Product decisions

- Launch platform: Windows-first.
- Provider policy: hybrid cloud-first; measured cloud providers are preferred when they meet latency and quality goals, with local fallback.
- Action safety: read-only and reversible actions run immediately; destructive or externally visible actions require explicit confirmation.
- Memory: local, user-controlled, inspectable, correctable, deletable, and retention-configurable.
- Hermes: managed local child process supervised by Jarvis.
- Primary experience metric: `speech_end_to_first_audio_ms`, reported with p50 and p95 values.
- Voice languages: Spanish, English, and mixed Spanish/English technical vocabulary.

## Scope and delivery model

This is a greenfield rebuild. The legacy implementation is reference material only: it may provide functional requirements, hardware assumptions, useful integrations, wake-word experiments, audio findings, known failures, acceptance scenarios, and reusable assets/configuration. The legacy `main.py` architecture will not be incrementally extended.

GSD Pi is the development control plane. It owns requirements, planning, architecture state, phases, execution, verification, benchmarks, commits, and resumable state. It must not be imported by or bundled with the Jarvis runtime.

The delivery roadmap is phased:

1. legacy extraction;
2. greenfield foundation;
3. provider bake-off;
4. voice vertical slice;
5. wake, clap, and barge-in;
6. Hermes integration;
7. memory;
8. provider routing and failover;
9. voice quality;
10. tools and integrations;
11. startup;
12. performance;
13. reliability;
14. production acceptance.

GSD may split these phases further, but no phase is complete merely because code exists. Each phase requires targeted tests, relevant full checks, runtime validation where applicable, diff review, updated project state, a coherent commit, and evidence attached to acceptance criteria.

## Runtime architecture

Jarvis is one Windows-first Python runtime with a supervised Hermes child process. The supervisor owns lifecycle, health, restart, shutdown, and operational state; it does not contain domain logic.

```text
                     +-----------------------------+
                     |      Jarvis Supervisor      |
                     | health / lifecycle / restart |
                     +--------------+--------------+
                                    |
Audio Input -> VAD -> Wake / Activation -> Streaming STT
                                                |
                                           Turn Manager
                              cancellation / context / deadline / trace ID
                                                |
                                         Intent / Command Router
                                  +-------------+---------------+
                                  |                             |
                       deterministic local command      fast conversational model
                                  |                             |
                             Tool Gateway              streaming tokens
                                  |                             |
                                Result                    Agentic task
                                                                |
                                                          Hermes Adapter
                                                                |
                                                        Hermes child process
                                                                |
                                                     events / tokens / tool requests
                                                                |
                                      Response Stream -> Streaming TTS -> Audio Playback

Barge-in -> TurnManager.cancel()
             -> cancel STT/model/Hermes/tools where possible
             -> cancel TTS and flush audio output
```

The real-time voice plane and cognitive plane communicate through bounded, typed runtime messages. Blocking audio APIs, provider SDKs, and subprocess I/O are isolated behind worker boundaries. The initial implementation uses in-process coordination and standard-library concurrency primitives; it does not introduce an external event broker or microservice deployment.

Core logic depends on ports/adapters for:

- `SpeechToText`;
- `TextToSpeech`;
- `WakeDetector`;
- `VoiceActivityDetector`;
- `AudioCapture` and `AudioPlayer`;
- `IntentClassifier`;
- `AgentRuntime`;
- `MemoryProvider` and `ModelProvider`;
- `Tool` and `Skill`;
- `EventBus`;
- `HealthCheck`;
- `StartupEffect`;
- media providers.

Provider-specific SDKs and Windows-specific APIs are adapters, never imports in orchestration or domain modules. Interfaces are created where explicitly required by the product or where multiple implementations are expected; speculative registries and abstractions are out of scope.

## Voice and interaction flow

Audio capture feeds local VAD. Wake-word detection remains local and always available; manual and push-to-talk activation are separate adapters. Clap activation is optional until calibrated on real hardware. Streaming STT begins after activation and feeds the Turn Manager. Deterministic command classification happens before any conversational model call.

The Turn Manager owns one interaction deadline, cancellation token, conversation context, and trace ID. It is the cancellation boundary for STT, model generation, Hermes, tool execution, TTS, and playback. Cancellation is best-effort for tools that cannot safely interrupt an in-flight operating-system operation.

Responses are emitted as semantic phrase chunks: short natural units, not individual words and not full paragraphs. A persistent TTS worker stays warm, keeps provider connections or speaker conditioning when supported, and feeds a cancellable playback queue. Playback is non-blocking and supports priority, cancellation, interruption, queueing, fade where useful, and output-device selection.

While Jarvis speaks, speech detection remains active. User speech stops current playback, drops queued TTS chunks, cancels unnecessary generation, and returns the system to listening. Audio feedback mitigation and thresholds are calibrated against the actual microphone, speakers/headphones, room noise, and echo characteristics.

An optional realtime speech-to-speech mode may be evaluated later, but it cannot bypass memory, tools, permissions, lifecycle, or observability boundaries.

## Provider strategy and routing

STT, fast conversational models, and TTS each have independent adapter contracts and provider chains. No provider is selected permanently before the bake-off.

The bake-off uses repeatable Spanish, English, and mixed technical-language samples and measures setup latency, first usable output, final output, quality, resource usage, network dependency, cost, tool-call behavior, and failure rate. STT additionally measures partial transcript timing, final transcript timing, end-of-turn detection, real-time factor, word error rate, CPU, and RAM. TTS additionally measures first audio, speech start, naturalness, pronunciation, prosody, streaming quality, interruption behavior, and voice quality.

At runtime:

- deterministic local commands bypass all LLMs;
- normal conversation uses the healthiest provider that meets its deadline;
- complex, memory-heavy, multi-step, or tool-heavy work goes to Hermes;
- provider errors trigger a configured fallback chain;
- rolling latency, errors, availability, throttling, and failure rate influence provider choice;
- deadlines prevent unbounded retries or fallback loops.

The router records provider selection and outcome metrics. It uses explicit configuration and adapters, not a speculative plugin marketplace. Exact initial providers, model variants, and any vector-storage dependency are decisions made from benchmark evidence in their respective phases.

## Hermes lifecycle

The Hermes adapter owns the wire contract between Jarvis and the local Hermes child process. The supervisor starts Hermes, performs health checks, reconnects when possible, restarts recoverable failures with bounded backoff, streams events/tokens/tool requests, and terminates it cleanly during shutdown.

Hermes may use persistent memory, skills, tools, planning, session context, and long-running task execution. Local fast commands and basic conversation remain available when Hermes is unavailable. Hermes failure must produce an explicit degraded health state, not silent routing or fake success.

## Memory and privacy

SQLite is the initial local memory store. Memory is separated into:

- working context for the active turn;
- user profile facts and preferences;
- curated long-term summaries;
- episodic conversation history;
- retrieval indexes.

Memory writes and consolidation occur asynchronously after response playback begins. Retrieval has bounded latency and token budgets. SQLite FTS5 is the initial local retrieval path; embedding/vector retrieval is evaluated during the memory phase and added only if measured recall justifies the dependency.

The user can inspect, correct, delete, and configure retention for persistent memory. Deletion removes the logical record and its retrieval index. Sensitive values, credentials, auth headers, private recordings, and raw secrets are not written to logs or committed to the repository. The runtime must not assume unrestricted filesystem access or unrestricted shell execution.

## Tools and action safety

The Tool Gateway is the sole path from Jarvis intent or Hermes tool requests to system actions. Each tool declares typed inputs, validation rules, timeout, cancellation behavior, permission class, and audit metadata.

The gateway validates arguments, applies an allowlist, enforces permission policy, and emits redacted audit events. Read-only and reversible actions can run immediately. Destructive or externally visible actions—such as deletion, purchases, messages, shutdown, or irreversible system changes—require explicit confirmation bound to the current trace and expiring after a short period.

Initial integration areas are Windows/system control, media, browser, filesystem, development tools, smart-home, and retained legacy integrations where their contracts and safety can be made explicit. Tools fail clearly when unavailable and never silently substitute a more powerful action.

## Lifecycle, health, and startup

Runtime states are:

`starting`, `ready`, `listening`, `thinking`, `speaking`, `interrupted`, `degraded`, `failed`, and `stopping`.

Health checks cover microphone, output device, VAD, wake, clap, STT, TTS, fast model provider, Hermes, memory, storage, and required network connectivity. Recoverable components restart with bounded backoff. Shutdown cancels active work, stops workers, terminates Hermes, persists safe state, and releases audio devices.

Startup initializes independent dependencies concurrently, but the voice control path becomes available as soon as its required dependencies are healthy. A cinematic success sequence plays only after required health checks pass; startup failures expose the failing component and do not play success music or speak a success phrase. Startup effects are cancellable and do not block safety or shutdown.

The CLI must provide:

```text
jarvis run
jarvis doctor
jarvis benchmark
jarvis benchmark providers
jarvis benchmark stt
jarvis benchmark llm
jarvis benchmark tts
jarvis benchmark end-to-end
jarvis calibrate clap
jarvis voice analyze
jarvis voice prepare
jarvis voice benchmark
```

Configuration is validated before startup. Secrets come from environment variables or a local secret mechanism and are never included in logs or benchmark output.

## Observability and performance

Every interaction receives a correlation/trace ID. The runtime records:

`wake_ms`, `speech_start`, `speech_end`, `vad_finalize_ms`, `stt_first_partial_ms`, `stt_final_ms`, `routing_ms`, `memory_lookup_ms`, `provider_selection_ms`, `agent_first_token_ms`, `agent_total_ms`, `tts_first_audio_ms`, `tts_total_ms`, `playback_start_ms`, `speech_end_to_first_audio_ms`, and `total_request_ms`.

Initial goals are targets, not fabricated guarantees:

| Metric | Goal |
| --- | --- |
| wake detection | under 100 ms |
| speech end to routing | 250–400 ms |
| local action | under 500 ms |
| short STT final | under 500 ms |
| fast model first token | 300–500 ms |
| TTS first audio | 250–500 ms |
| normal conversation first audio | under 1–1.5 s |
| local command completion | under 500 ms |

Benchmarks persist repeatable results and compare against a baseline. Important changes flag regressions, especially any feature that materially increases first-audio p50 or p95. Full response time is tracked separately and may continue after speech starts.

## Repository organization

The target structure is:

```text
apps/             runtime and CLI entrypoints
core/             config, lifecycle, state, events, concurrency, orchestration
audio/            capture, playback, VAD, wake, clap, devices
speech/           STT, TTS, streaming
agent/            Hermes, routing, providers, tools, skills
memory/           working, profile, episodic, semantic, long-term stores
integrations/     system, browser, media, home, external adapters
observability/    traces, metrics, structured logs
startup/          initialization and startup effects
benchmarks/       provider and end-to-end harnesses
tests/            unit, integration, contract, and acceptance support
docs/             design, operations, benchmark, and hardware documentation
```

The plan may collapse directories that contain no meaningful boundary. The legacy monolith will not be preserved, and the rebuild will not recreate a large `main.py`.

## Verification and acceptance

Unit tests cover routing, memory behavior, provider contracts, lifecycle, state transitions, configuration, tool validation and permissions, parsers, cancellation, and fallback behavior. Integration tests cover SQLite persistence, provider adapters, supervisor recovery, and Hermes process management. Audio paths require real runtime checks in addition to mocks.

Required end-to-end scenarios are:

1. wake → speech → STT → route → response → TTS → audio;
2. `abre Spotify` → deterministic route → Windows action;
3. fast conversational provider → streamed TTS;
4. complex request → Hermes → tools/memory → streamed response;
5. store memory → restart → recall;
6. historical session → relevant retrieval;
7. Jarvis speaking → user interrupts → audio stops → new listening turn;
8. primary provider failure → fallback;
9. Hermes failure → degraded mode → local actions continue → recovery;
10. healthy startup → calibrated double clap → music/phrase → `LISTENING`.

Production acceptance requires actual microphone, speakers/headphones, network, configured API providers, local fallback, Hermes process, persisted memory, and Windows actions. Mock-only validation is insufficient.

## Explicit non-goals

- preserving the legacy architecture or backward compatibility;
- making GSD Pi a runtime dependency;
- selecting providers before benchmarking;
- routing every request to Hermes or an LLM;
- waiting for complete responses before speaking;
- using Whisper as an always-on wake detector;
- loading heavyweight models for every request;
- committing credentials, private recordings, memory databases, copyrighted media, or generated artifacts;
- claiming latency or health results without measurements.
