# JARVIS — FINAL MASTER IMPLEMENTATION PROMPT

You are operating inside the already initialized and validated Jarvis repository.

The GSD Pi development environment has already been bootstrapped and verified.

Do NOT repeat the bootstrap.

The verified engineering environment already includes:

* GSD Pi 1.20.0
* native GSD multi-agent orchestration
* Ponytail
* Context Mode
* curated engineering skills
* gated UX/UI skills
* DeepSeek
* OpenAI/Codex
* Claude Code fallback
* provider fallback/recovery
* working tests
* verified subagent execution
* verified parallel agents

Your task now is to take Jarvis from its current repository state to a **working, polished, launchable application**.

You are responsible for the complete engineering lifecycle:

```text
inspect
→ understand
→ specify
→ architect
→ plan
→ implement
→ test
→ review
→ fix
→ integrate
→ run
→ validate
→ launch
```

Do not stop at architecture.

Do not stop at code generation.

Do not stop because individual components compile.

The task is complete only when Jarvis can actually be started and exercised successfully on the target Windows environment.

---

# 1. PRODUCT GOAL

Build Jarvis as a production-quality, Windows-first personal AI assistant with extremely low perceived latency, natural voice interaction, persistent useful memory, local system actions, API-backed intelligence, and Hermes-powered agent capabilities.

The target experience is:

```text
USER
  ↓
speaks naturally
  ↓
Jarvis detects activation / speech
  ↓
speech becomes actionable as early as possible
  ↓
fast commands execute immediately
  ↓
model requests stream as soon as possible
  ↓
speech output begins before the entire answer is complete
  ↓
user can interrupt Jarvis at any moment
  ↓
Jarvis immediately cancels the previous turn
```

Jarvis must feel responsive.

Latency is a first-class architectural requirement.

---

# 2. DEVELOPMENT PRINCIPLES

The implementation must favor:

```text
measured behavior > assumptions
simple architecture > framework accumulation
streaming > waiting for full completion
async I/O > blocking operations
API-backed inference > slow local inference where appropriate
explicit interfaces > SDK coupling
cancellation > abandoned background work
graceful degradation > total failure
observability > silent errors
runtime validation > code-only confidence
```

Do not optimize prematurely, but do not introduce obviously high-latency designs.

---

# 3. TARGET HIGH-LEVEL ARCHITECTURE

The desired runtime architecture is:

```text
                         ┌─────────────────────┐
                         │       AUDIO IN      │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ VAD / ACTIVATION    │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ STREAMING STT       │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ TURN CONTROLLER     │
                         │ + CANCELLATION      │
                         └──────────┬──────────┘
                                    │
                  ┌─────────────────┼─────────────────┐
                  │                 │                 │
                  ▼                 ▼                 ▼
        ┌────────────────┐ ┌────────────────┐ ┌────────────────┐
        │ FAST COMMAND   │ │ FAST MODEL     │ │ HERMES AGENT   │
        │ PATH           │ │ PATH           │ │ PATH           │
        └───────┬────────┘ └───────┬────────┘ └───────┬────────┘
                │                  │                  │
                └──────────────────┼──────────────────┘
                                   │
                                   ▼
                         ┌─────────────────────┐
                         │ STREAMING RESPONSE  │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ STREAMING TTS       │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ AUDIO OUTPUT        │
                         └─────────────────────┘
```

Memory, tools, health monitoring, observability and configuration are cross-cutting services.

---

# 4. ARCHITECTURAL BOUNDARIES

Jarvis must use explicit ports/interfaces for external systems.

Examples:

```text
AudioInputPort
AudioOutputPort
ActivationPort
SpeechToTextPort
TextToSpeechPort
ModelProviderPort
MemoryPort
ToolPort
AgentPort
HealthPort
ConfigurationPort
TelemetryPort
```

Concrete integrations belong in adapters.

Examples:

```text
adapters/
    audio/
    stt/
    tts/
    models/
    hermes/
    memory/
    tools/
    windows/
```

The orchestration/domain/application code must not depend directly on external SDK semantics.

Provider replacement must not require rewriting the runtime.

---

# 5. ONE RUNTIME

Jarvis should be a single primary Python runtime.

Hermes should run as a small supervised child process or similarly isolated agent runtime where technically appropriate.

Do NOT create unnecessary microservices.

Do NOT turn every subsystem into a process.

The preferred model is:

```text
Jarvis Python Runtime
        │
        └── supervised Hermes child
```

Use additional processes only when there is a concrete technical reason.

---

# 6. WINDOWS-FIRST

Jarvis is Windows-first.

It must work properly on the actual Windows development machine.

Account for:

* Windows audio devices
* subprocess behavior
* signals/cancellation differences
* path handling
* process termination
* startup
* environment variables
* PowerShell
* desktop notifications where appropriate
* media controls
* default applications
* common Windows APIs

Do not assume POSIX behavior.

Any cross-platform abstractions are welcome, but Windows correctness comes first.

---

# 7. NO GSD RUNTIME DEPENDENCY

GSD Pi is the development orchestrator.

It must NOT become a production dependency of Jarvis.

The resulting Jarvis application must run independently of GSD.

Likewise:

```text
Ponytail
Context Mode
development skills
GSD agents
```

belong to the engineering workflow, not the production application.

---

# 8. TURN MODEL

A voice interaction must be modeled as an explicit turn.

Example:

```text
TurnSession
    id
    created_at
    state
    transcript
    intent
    route
    cancellation_token
    timing
```

Possible states:

```text
LISTENING
TRANSCRIBING
ROUTING
EXECUTING
RESPONDING
SPEAKING
COMPLETED
CANCELLED
FAILED
```

Transitions must be explicit enough to debug.

---

# 9. CANCELLATION AND BARGE-IN

Barge-in is mandatory.

When the user begins speaking while Jarvis is speaking:

```text
detect user speech
      ↓
stop audio playback
      ↓
cancel TTS generation if possible
      ↓
cancel active model stream
      ↓
cancel active Hermes work where safe
      ↓
cancel reversible tool work where appropriate
      ↓
create new turn
```

This must happen quickly.

Do not merely mute playback while expensive work continues unnoticed.

Cancellation must propagate through the turn.

Use structured cancellation rather than arbitrary global flags where possible.

---

# 10. FAST PATH

Simple commands must NOT go through Hermes or a heavyweight model unnecessarily.

Create a deterministic low-latency command path.

Examples:

```text
open application
close application
volume
mute/unmute
media controls
time
date
simple system state
launch URL
basic configured shortcuts
stop
cancel
repeat
```

Flow:

```text
speech
→ command classification
→ local tool
→ result
→ TTS
```

The classifier must be fast and conservative.

Do not build an enormous NLP framework.

Use deterministic patterns and/or a lightweight classifier where appropriate.

If confidence is insufficient, route upward.

---

# 11. ROUTING

Implement an explicit request router.

Conceptually:

```text
incoming request
       ↓
fast deterministic command?
       │
       ├── yes → local fast path
       │
       └── no
            ↓
normal conversational request?
       │
       ├── yes → fast model
       │
       └── no
            ↓
requires planning/tools/multi-step autonomy?
       │
       ├── yes → Hermes
       │
       └── fallback model path
```

The router should produce an inspectable decision.

Example:

```text
RouteDecision(
    route="fast_model",
    confidence=0.94,
    reason="conversational factual request"
)
```

Routing must be observable.

---

# 12. MODEL STRATEGY

Do not use a single model for everything.

Optimize for latency/cost/capability.

Support provider abstraction and configuration.

Expected conceptual tiers:

```text
FAST
    quick conversation
    intent classification
    simple transformations

REASONING
    difficult reasoning
    complex technical questions
    difficult planning

AGENT
    multi-step execution
    tools
    autonomous workflows
```

Prefer API-backed inference when it materially improves responsiveness compared with local models.

Do not introduce heavyweight local LLM inference into the critical voice path unless measurements demonstrate that it is beneficial.

---

# 13. PROVIDER FALLBACK

Model calls must handle transient provider failures.

Examples:

```text
quota exceeded
rate limit
temporary unavailable
network transient
model unavailable
```

Use configured provider fallback rather than failing the whole assistant.

Separate:

```text
transient provider failure
```

from:

```text
bad credentials
invalid configuration
invalid request
application bug
```

Do not endlessly retry configuration errors.

Implement bounded retries with sensible timeout/backoff behavior.

---

# 14. STREAMING EVERYWHERE POSSIBLE

The desired processing pipeline is not:

```text
record
wait
transcribe
wait
generate entire response
wait
synthesize entire response
play
```

It should approach:

```text
audio chunks
    ↓
partial transcript
    ↓
early routing when safe
    ↓
streamed model tokens
    ↓
sentence/chunk segmentation
    ↓
streaming synthesis
    ↓
playback
```

Optimize time-to-first-useful-audio.

Measure it.

---

# 15. STT

Select and implement a high-quality low-latency STT strategy.

Prioritize:

* Spanish
* English
* mixed speech where practical
* low latency
* streaming/partial transcripts
* robustness to common room noise
* API availability
* predictable error handling

Do not blindly preserve the current STT implementation if it is the cause of unacceptable latency.

Evaluate current implementation first.

Create an STT adapter so providers can change.

Support configurable provider selection.

---

# 16. TTS

The current poor-quality voice experience must be replaced.

The target voice must be:

* natural
* expressive
* low latency
* suitable for conversational streaming
* stable across turns

Do not rely on a poor cloned sample merely because it already exists.

Keep TTS provider-specific logic behind `TextToSpeechPort`.

Support streaming synthesis when the provider allows it.

If a cloned/custom voice is used, the user must have the right to use the source voice/audio.

Do not fetch or imitate unauthorized third-party voice assets.

---

# 17. RESPONSE CHUNKING

Do not wait for an entire long response before speaking.

Implement sensible chunking.

For example:

```text
model stream
   ↓
sentence boundary / punctuation / safe chunk threshold
   ↓
TTS queue
   ↓
playback
```

Avoid:

* chunks so small that speech sounds fragmented
* chunks so large that latency becomes excessive

Measure and tune.

---

# 18. AUDIO OUTPUT QUEUE

Create a controlled audio playback queue.

Requirements:

* ordered playback
* immediate flush on cancellation
* no overlapping responses unless explicitly desired
* device errors handled gracefully
* queue state observable

Do not allow abandoned TTS chunks from a cancelled turn to play later.

Every audio chunk must belong to a turn ID.

---

# 19. AUDIO INPUT

Audio capture must remain responsive while Jarvis is working.

Do not block microphone processing because a model/tool/TTS request is running.

Use appropriate concurrency.

Audio input must support barge-in detection during playback.

---

# 20. ACTIVATION

Support a configurable activation model.

Potential modes:

```text
wake word
push-to-talk
continuous conversation
manual activation
```

Do not overcomplicate wake-word recognition initially.

Design it behind an activation interface.

The first stable production version may use whichever activation mechanism proves most reliable and responsive.

---

# 21. CONVERSATION MODE

Jarvis should support natural follow-up speech.

Avoid requiring the wake word for every sentence when conversation mode is active.

Use a configurable conversational timeout/window.

For example:

```text
wake
→ user question
→ answer
→ short follow-up window
→ user follow-up without wake word
```

The behavior must be configurable.

---

# 22. MEMORY

The current weak/no-memory behavior must be replaced with a deliberate memory architecture.

Do NOT treat the entire conversation log as "memory".

Separate at least:

```text
working memory
conversation memory
long-term semantic memory
user preferences
operational state
```

Conceptually:

```text
MemoryService
│
├── WorkingMemory
├── ConversationStore
├── LongTermMemory
└── PreferenceStore
```

Memory must have retrieval rules.

Do not dump all memories into every prompt.

Use relevance.

---

# 23. WORKING MEMORY

Working memory contains context necessary for the current conversation/task.

Examples:

```text
current topic
recent turns
active task
current entities
temporary decisions
```

It should be fast and bounded.

---

# 24. LONG-TERM MEMORY

Long-term memory should contain durable useful information.

Examples:

```text
stable user preferences
important recurring facts
ongoing projects
frequently used application preferences
explicitly remembered information
```

Do not store everything.

Introduce criteria for memory promotion.

Design retrieval around relevance.

---

# 25. MEMORY SAFETY

Memory operations must be inspectable.

Support at least an internal representation for:

```text
source
created_at
last_used
confidence/relevance
category
```

Avoid silently treating model guesses as user facts.

Do not store credentials or secrets in conversational memory.

---

# 26. HERMES

Hermes provides agentic capabilities.

It should be used for tasks such as:

```text
multi-step planning
research
tool orchestration
complex system operations
longer autonomous workflows
tasks requiring iterative execution
```

Do NOT send every conversational query to Hermes.

Hermes must not sit in the critical path for:

```text
volume up
open Spotify
what time is it
simple conversation
```

---

# 27. HERMES PROCESS MANAGEMENT

Hermes should be supervised.

Jarvis must detect:

```text
Hermes started
Hermes ready
Hermes crashed
Hermes timed out
Hermes unresponsive
Hermes exited
```

Provide restart/recovery behavior where safe.

Jarvis should still provide degraded functionality if Hermes is unavailable.

Example:

```text
Hermes DOWN

local commands → available
fast model → available
agent workflows → unavailable/degraded
```

---

# 28. HERMES COMMUNICATION

Use a clear structured protocol between Jarvis and Hermes.

Avoid parsing arbitrary terminal text if a structured mechanism is available.

Messages/events should conceptually include:

```text
request_id
turn_id
event_type
payload
timestamp
```

Possible events:

```text
started
thinking
tool_started
tool_completed
partial_response
completed
failed
cancelled
```

Streaming events should be surfaced to Jarvis where useful.

---

# 29. TOOLS

Define tools through explicit contracts.

A tool should expose metadata similar to:

```text
name
description
input schema
risk level
reversibility
execution timeout
```

Do not let models execute arbitrary shell commands directly without a controlled boundary.

---

# 30. TOOL RISK MODEL

Differentiate tool categories.

Example:

```text
READ_ONLY
REVERSIBLE
CONFIRM_REQUIRED
HIGH_RISK
```

Examples:

```text
read system volume       → READ_ONLY
open application         → REVERSIBLE
delete file              → CONFIRM_REQUIRED
system destructive task  → HIGH_RISK
```

Do not require confirmation for harmless repetitive actions.

Require appropriate confirmation for destructive/irreversible operations.

---

# 31. WINDOWS TOOLS

Implement a clean, extensible Windows tool layer.

Candidate initial tools:

```text
open application
open URL
media play/pause
next/previous
volume set/up/down
mute
system information
process inspection
window/application focus where practical
```

Do not implement every possible system command before the core loop works.

---

# 32. STARTUP EXPERIENCE

Jarvis should provide a polished startup experience once critical services are healthy.

Desired sequence:

```text
application launch
      ↓
parallel subsystem initialization
      ↓
critical voice path healthy
      ↓
short audible startup cue / clap-style cue
      ↓
optional configured startup music/sound
      ↓
Jarvis startup phrase
      ↓
ready for interaction
```

Important:

Do not make startup wait unnecessarily for non-critical services.

Critical voice interaction should become available as early as possible.

Music/audio assets must be configurable local assets.

Do not download or bundle copyrighted movie soundtrack/music or protected movie dialogue without a user-provided licensed asset.

Provide configuration hooks so the user can supply their own authorized startup sound/music and startup phrase.

Include a safe default startup cue and phrase.

---

# 33. PARALLEL STARTUP

Initialize independent components concurrently where safe.

Conceptually:

```text
               ┌─ audio
               ├─ STT
startup ───────┼─ TTS
               ├─ model providers
               ├─ memory
               ├─ tools
               └─ Hermes
```

Do not serialize startup unnecessarily.

Track dependencies.

Example:

```text
voice ready requires:
    audio input
    STT
    TTS
    at least one model

agent features require:
    Hermes
```

---

# 34. HEALTH MODEL

Create explicit health states.

Example:

```text
STARTING
HEALTHY
DEGRADED
UNHEALTHY
STOPPING
```

And component health.

Example:

```text
STT       HEALTHY
TTS       HEALTHY
MODEL     HEALTHY
MEMORY    HEALTHY
HERMES    DEGRADED
```

Jarvis may be usable in `DEGRADED`.

Do not make every optional subsystem fatal.

---

# 35. OBSERVABILITY

Introduce structured logging.

Useful fields:

```text
timestamp
turn_id
component
event
duration_ms
provider
model
route
success
error_type
```

Do not log secrets.

---

# 36. LATENCY TELEMETRY

Measure the actual voice pipeline.

For each turn capture timings such as:

```text
activation_detected
speech_started
speech_ended
first_partial_transcript
final_transcript
route_selected
model_request_started
first_model_token
tts_started
first_audio_generated
first_audio_played
turn_completed
```

Calculate meaningful metrics:

```text
STT latency
routing latency
model TTFT
TTS TTFA
speech-end → first Jarvis audio
total turn latency
```

The architecture must be optimized using measurements rather than guesses.

---

# 37. PERFORMANCE TARGET

Do not invent guarantees that providers/hardware cannot satisfy.

However, actively optimize toward conversational responsiveness.

Prioritize:

```text
time to first response
barge-in reaction time
startup readiness
fast command execution
```

Create a benchmark/smoke mechanism that records actual values.

---

# 38. TIMEOUTS

Every external operation must have a sensible timeout.

Examples:

```text
model API
STT API
TTS API
Hermes IPC
tool execution
network calls
```

Never allow a single hung dependency to freeze Jarvis forever.

---

# 39. RESILIENCE

Handle:

```text
network unavailable
provider unavailable
audio device missing
audio device disconnected
Hermes crash
invalid provider response
tool timeout
memory failure
TTS failure
STT failure
```

Provide useful degraded behavior and clear logs.

---

# 40. CONFIGURATION

Configuration must not be scattered across source files.

Provide a coherent configuration system.

Possible categories:

```text
audio
activation
stt
tts
models
memory
Hermes
tools
startup
logging
timeouts
latency
```

Use environment variables for secrets.

Provide sane defaults for non-secret configuration.

Provide `.env.example` or equivalent without credentials.

---

# 41. DEPENDENCY DISCIPLINE

Do not introduce large dependencies for trivial tasks.

Every significant new dependency should have a reason.

Prefer standard library when it is adequate.

Remove obsolete dependencies only when proven unused.

---

# 42. EXISTING CODE

Do not blindly rewrite everything just because a clean rewrite was permitted.

First inspect the current repository.

Classify existing code:

```text
KEEP
REFACTOR
REPLACE
REMOVE
```

Then act.

If the current architecture is fundamentally poor, a substantial rewrite is acceptable.

Preserve useful tests, interfaces or implementation only when doing so improves the resulting system.

---

# 43. CLEAN ARCHITECTURE

Use pragmatic clean architecture.

Do not create layers merely for ceremony.

A likely structure may resemble:

```text
src/jarvis/
    domain/
    application/
    ports/
    adapters/
    runtime/
    config/
    observability/
    apps/
```

But do not force this exact structure if repository analysis reveals a better organization.

The important rule is dependency direction and replaceability, not folder aesthetics.

---

# 44. CONCURRENCY MODEL

Choose and document a coherent concurrency model.

For the Python runtime, prefer asyncio where it fits I/O-heavy orchestration.

Avoid mixing threads, processes and asyncio arbitrarily.

Use threads/processes only where libraries or CPU-bound operations require them.

Clearly define ownership of:

```text
event loop
audio capture
audio playback
Hermes process
background tasks
shutdown
```

---

# 45. STRUCTURED CONCURRENCY

Background work must have ownership.

Avoid orphan tasks.

When Jarvis shuts down:

```text
stop accepting turns
cancel active turn
stop TTS
stop playback
stop audio input
cancel background tasks
shutdown Hermes
flush memory if needed
close clients
exit cleanly
```

---

# 46. USER INTERFACE

Do not prioritize a large GUI before the voice runtime works.

The core experience is voice-first.

A lightweight status UI/tray/debug console may be implemented if useful.

If a UI is implemented, invoke the configured UI/UX skills.

The UI should expose useful state such as:

```text
listening
thinking
speaking
offline/degraded
current provider
Hermes status
```

Avoid unnecessary dashboard complexity.

---

# 47. CLI / DEVELOPMENT ENTRYPOINT

Provide a reliable application entrypoint.

The final application should be launchable with one documented command.

Prefer something such as:

```powershell
jarvis
```

or:

```powershell
python -m jarvis
```

depending on the existing packaging architecture.

Development startup may have an additional command if needed.

The user should not have to manually start five independent services.

Jarvis itself must supervise what it needs.

---

# 48. FIRST-RUN EXPERIENCE

If required configuration or API credentials are missing:

* detect exactly what is missing
* print a useful message
* do not crash with an opaque traceback
* explain which features are unavailable
* continue in degraded mode where possible

Do not print secrets.

---

# 49. TEST STRATEGY

Use multiple test layers.

At minimum:

```text
unit
integration
runtime smoke
```

Add contract/end-to-end tests where they provide value.

Critical areas requiring tests include:

```text
routing
turn lifecycle
cancellation
barge-in
provider fallback
Hermes supervision
memory retrieval
tool safety
audio queue cancellation
startup health
shutdown
```

---

# 50. FAKE ADAPTERS

Provide deterministic fake/test adapters for external dependencies where useful.

Examples:

```text
FakeSTT
FakeTTS
FakeModel
FakeAudioInput
FakeAudioOutput
FakeHermes
```

These should make orchestration tests fast and reliable.

Do not require paid API calls for every automated test.

---

# 51. REAL INTEGRATION VALIDATION

Mocks are not sufficient for final acceptance.

Where configured credentials/services permit, test the real adapters.

At least validate the actual configured runtime path.

Mark unavailable external integrations honestly.

---

# 52. REVIEW

After every substantial phase, use an independent GSD reviewer.

The reviewer should inspect for:

```text
architecture drift
unnecessary complexity
race conditions
cancellation bugs
blocking calls in async paths
resource leaks
provider coupling
unsafe tool execution
memory misuse
latency regressions
Windows incompatibilities
```

Fix meaningful findings.

Do not simply record them and move on.

---

# 53. USE GSD MULTI-AGENT EXECUTION

Use GSD native subagents where useful.

Examples of parallelizable investigations:

```text
Agent A
    current runtime architecture

Agent B
    voice/STT/TTS latency

Agent C
    memory architecture

Agent D
    Hermes integration research

Agent E
    Windows/process lifecycle

Agent F
    test architecture
```

Merge findings before implementation decisions.

Do not allow multiple agents to edit the same area concurrently without deliberate isolation.

---

# 54. CONTEXT EFFICIENCY

Use Ponytail and Context Mode.

Agents should inspect only relevant repository areas.

Do not repeatedly load the whole repository.

Use:

```text
search
→ identify subsystem
→ inspect
→ expand only as needed
```

---

# 55. MODEL USE DURING DEVELOPMENT

Use GSD model routing already configured.

DeepSeek may handle cost-efficient analysis/execution.

OpenAI/Codex may be used for implementation/reasoning according to configured routing.

Claude can be used when available.

Provider quota exhaustion must not stop development when another configured provider is capable.

Do not alter the validated provider setup without a concrete reason.

---

# 56. IMPLEMENTATION PHASES

After repository analysis, create an explicit implementation plan.

A sensible baseline is:

```text
PHASE 0
Repository audit and measurable baseline

PHASE 1
Core runtime + lifecycle + ports

PHASE 2
Audio input/output + cancellation

PHASE 3
Streaming STT

PHASE 4
Routing + fast command path

PHASE 5
Model provider layer + streaming

PHASE 6
Streaming TTS + playback pipeline

PHASE 7
Barge-in and end-to-end voice turn

PHASE 8
Memory architecture

PHASE 9
Hermes integration + supervision

PHASE 10
Windows tools

PHASE 11
Startup / health / resilience

PHASE 12
Observability + latency benchmarking

PHASE 13
UX/status surface if justified

PHASE 14
Full integration testing

PHASE 15
Production cleanup and launch
```

Adjust these phases after inspecting the repository.

Do not mechanically implement this numbering if different sequencing is technically superior.

But preserve the architectural dependencies.

---

# 57. PHASE GATES

Do not progress blindly.

Each phase must have:

```text
goal
implementation
tests
review
verification evidence
```

If a phase exposes an architectural flaw, fix it before building further on top of it.

---

# 58. DEFINITION OF DONE — CORE VOICE

The voice runtime is not complete until this real sequence works:

```text
Jarvis running
↓
user speaks
↓
speech recognized
↓
request routed
↓
response generated/executed
↓
voice response starts
↓
user interrupts
↓
old response stops
↓
new request is handled
```

Validate this with the real audio pipeline where hardware/environment permits.

---

# 59. DEFINITION OF DONE — FAST COMMAND

Validate at least one real deterministic command.

Example:

```text
"sube el volumen"
```

or another safe Windows action.

Evidence should show that it bypasses Hermes and the heavyweight reasoning path.

---

# 60. DEFINITION OF DONE — CONVERSATION

Validate at least one actual model-backed conversational turn.

Confirm:

```text
STT
→ router
→ model
→ streamed response
→ TTS
→ playback
```

---

# 61. DEFINITION OF DONE — MEMORY

Validate a multi-turn scenario where Jarvis recalls appropriate prior context without injecting unrelated stored memory.

---

# 62. DEFINITION OF DONE — HERMES

Validate at least one harmless real agentic workflow through Hermes.

Example:

```text
inspect a configured local fact or perform a safe multi-step operation
```

Confirm streamed events / completion and proper process supervision.

Do not use a destructive workflow as the acceptance test.

---

# 63. DEFINITION OF DONE — FALLBACK

Where feasible, validate provider fallback.

Do not intentionally consume unnecessary quota.

A controlled test adapter or safe provider-selection test is acceptable when deliberately causing a real provider failure would be wasteful.

---

# 64. DEFINITION OF DONE — DEGRADED MODE

Validate that Jarvis remains partially useful when Hermes is unavailable.

Expected:

```text
local commands still work
basic conversational model can still work
agent tasks clearly report unavailable/degraded
```

---

# 65. DEFINITION OF DONE — STARTUP

Validate cold startup.

Measure:

```text
process start
→ critical voice path ready
```

Confirm optional components do not unnecessarily block readiness.

---

# 66. DEFINITION OF DONE — SHUTDOWN

Validate graceful shutdown.

No:

```text
orphan Hermes process
stuck audio stream
zombie subprocess
unflushed queues
hanging Python process
```

---

# 67. DOCUMENTATION

Keep documentation practical.

At minimum provide:

```text
README
architecture overview
configuration
provider setup
audio/STT/TTS configuration
Hermes integration
memory model
running Jarvis
testing
troubleshooting
```

Do not generate hundreds of pages of redundant documentation.

---

# 68. ENVIRONMENT SETUP

Provide reproducible environment setup.

For Windows/PowerShell, document exact commands.

Keep existing supported packaging strategy where reasonable.

Do not require undocumented manual modifications to Python packages.

---

# 69. ONE-COMMAND LAUNCH

At the end, Jarvis must have a clear launch path.

Examples:

```powershell
jarvis
```

or:

```powershell
python -m jarvis
```

or an equivalent project-supported command.

If setup requires an initial installation command, document it separately.

After setup, daily startup should be simple.

---

# 70. LAUNCH SCRIPT

If beneficial, provide a Windows convenience launcher such as:

```text
run-jarvis.ps1
```

or equivalent.

It should:

```text
validate environment
load configuration
start Jarvis
surface useful startup errors
```

Do not hide errors.

---

# 71. NO FAKE SUCCESS

Never state that:

```text
audio works
Hermes works
memory works
streaming works
barge-in works
Windows tools work
```

unless validated.

Distinguish:

```text
IMPLEMENTED
UNIT VERIFIED
INTEGRATION VERIFIED
RUNTIME VERIFIED
BLOCKED
```

---

# 72. FIX FAILURES

If tests fail, investigate and fix them.

Do not stop at:

```text
"the implementation is complete but tests fail"
```

If an external dependency prevents validation, isolate that limitation and continue validating everything else.

---

# 73. DO NOT ASK FOR PERMISSION BETWEEN PHASES

You already have authorization to execute this implementation.

Do not repeatedly ask:

```text
Should I continue?
Do you want me to implement phase 2?
Can I proceed?
```

Proceed autonomously through the plan.

Pause only if a genuinely dangerous/destructive user decision is required or a credential/input exists that cannot be derived safely.

---

# 74. DO NOT STOP AT A PLAN

A plan is not the deliverable.

Architecture documentation is not the deliverable.

Generated code is not the deliverable.

The deliverable is:

```text
A RUNNING JARVIS APPLICATION
```

---

# 75. REAL APPLICATION LAUNCH

After all implementation and validation gates pass:

1. Run the full automated test suite.
2. Run static/type/compile validation.
3. Run integration tests.
4. Perform runtime smoke validation.
5. Start Jarvis using the final supported launch command.
6. Confirm the process remains healthy.
7. Confirm critical components initialize.
8. Confirm Jarvis reaches a READY or HEALTHY state.
9. Exercise at least one safe interaction if the environment supports interactive audio.
10. Leave the application in a usable launched state unless doing so would interfere with the GSD session or environment.

If persistent launch cannot safely coexist with the current GSD process, launch it long enough to validate readiness and then provide the exact final launch command.

Do not claim it was launched if only tests were run.

---

# 76. FINAL ACCEPTANCE SUITE

Before declaring completion, execute a final acceptance matrix similar to:

| Capability                     | Required state                                     |
| ------------------------------ | -------------------------------------------------- |
| Application startup            | RUNTIME VERIFIED                                   |
| Graceful shutdown              | RUNTIME VERIFIED                                   |
| STT                            | RUNTIME VERIFIED where credentials/hardware permit |
| TTS                            | RUNTIME VERIFIED where credentials/hardware permit |
| Fast command routing           | RUNTIME VERIFIED                                   |
| Model conversation             | RUNTIME VERIFIED                                   |
| Streaming response             | RUNTIME VERIFIED                                   |
| Barge-in                       | RUNTIME VERIFIED                                   |
| Cancellation propagation       | RUNTIME VERIFIED                                   |
| Memory                         | INTEGRATION/RUNTIME VERIFIED                       |
| Hermes                         | RUNTIME VERIFIED                                   |
| Hermes unavailable degradation | VERIFIED                                           |
| Provider fallback              | VERIFIED                                           |
| Windows tool                   | RUNTIME VERIFIED                                   |
| Startup health                 | RUNTIME VERIFIED                                   |
| Tests                          | PASS                                               |
| Compile/type validation        | PASS                                               |
| Secrets check                  | PASS                                               |

A blocked external integration does not justify pretending success.

Report it precisely.

---

# 77. PERFORMANCE REPORT

At final validation include measured values where available:

```text
cold startup readiness
STT finalization latency
model TTFT
TTS first-audio latency
speech-end → first Jarvis audio
barge-in cancellation latency
fast command latency
```

Do not fabricate unavailable measurements.

---

# 78. CLEANUP

Before final acceptance:

* remove dead experimental code
* remove obsolete architecture
* remove unused dependencies when confidently proven unused
* remove temporary debug hacks
* remove generated secrets
* ensure `.gitignore` is correct
* ensure logs/caches/runtime artifacts are not accidentally committed
* ensure development-only GSD artifacts are not part of the production runtime

Do not delete useful audit/documentation evidence.

---

# 79. SECURITY CHECK

Before completion verify:

```text
no API keys committed
no tokens logged
no arbitrary shell execution path exposed to models
tool permissions respected
dangerous actions gated
provider errors sanitized
memory does not store secrets accidentally
```

---

# 80. FINAL INDEPENDENT REVIEW

Run a final independent architecture/code review using a GSD review agent that did not perform the primary implementation.

Review:

```text
correctness
maintainability
latency
resilience
security
Windows behavior
async correctness
cancellation
Hermes lifecycle
audio lifecycle
memory design
tool safety
test coverage
dead code
```

Resolve all critical/high findings and meaningful medium findings before final acceptance.

---

# 81. FINAL REPORT

At the very end return a concise engineering report with exactly these sections:

## 1. Final architecture

Describe the architecture that was actually implemented.

## 2. Major changes

Only significant changes.

## 3. Runtime providers

Report actual configured:

```text
STT
TTS
LLM
Hermes
memory
```

## 4. Voice pipeline

Show the final real flow.

## 5. Routing

Explain:

```text
fast command
fast model
reasoning
Hermes
```

## 6. Memory

Describe implemented memory tiers and retrieval.

## 7. Hermes

Describe process supervision and integration.

## 8. Windows integration

List implemented tools/integration.

## 9. Verification

Table with:

```text
PASS
PARTIAL
BLOCKED
FAIL
```

for each important subsystem.

## 10. Performance

Report measured latency values.

## 11. Tests

Report exact counts/results.

## 12. Remaining limitations

Only real remaining limitations.

Do not hide them.

## 13. How to launch

Provide the exact final Windows command.

Example:

```powershell
jarvis
```

## 14. Runtime status

State whether Jarvis was actually launched and what health state it reached.

---

# 82. FINAL COMPLETION CONDITION

Only finish with:

```text
JARVIS READY
```

if all non-blocked critical capabilities have passed their acceptance gates and the application has been actually launched successfully.

Otherwise finish with:

```text
JARVIS NOT READY
```

and immediately before it list the exact remaining blockers.

Do not use optimistic wording to bypass this gate.

---

# 83. START NOW

Begin by inspecting the current repository state and the existing Jarvis implementation.

Use the already configured GSD planning state.

Do not rerun the GSD bootstrap.

Do not modify the validated GSD provider configuration unless technically necessary.

Use Ponytail and Context Mode for efficient repository exploration.

Use GSD native agents for independent investigation and review.

First establish the measurable current baseline, then produce the implementation plan, then execute it fully.

Proceed autonomously through implementation, testing, review, integration and runtime validation.

Do not stop after producing the plan.

Do not stop after generating code.

Do not stop after tests pass.

The end state is a working Jarvis application that can be launched on Windows with the documented command.

Begin.
