# Jarvis v2 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the legacy runtime's architectural center with a tested Windows-first foundation that owns configuration, lifecycle, state, tracing, health, and a usable CLI without coupling core code to audio, provider, or Hermes SDKs.

**Architecture:** Build a single-process asynchronous runtime around explicit typed ports and a supervised component lifecycle. Keep provider and hardware implementations out of core; use standard-library dataclasses, protocols, asyncio, JSON configuration, logging, and unittest until a later phase proves a dependency necessary.

**Tech Stack:** Python 3.10/3.11, standard library (`asyncio`, `dataclasses`, `typing.Protocol`, `argparse`, `json`, `logging`, `unittest`), `pyproject.toml`, Windows-first process/audio adapters in later phases.

**Spec:** `docs/superpowers/specs/2026-09-16-jarvis-v2-design.md`

## Global Constraints

- Launch platform is Windows-first.
- Provider policy is hybrid cloud-first with measured local fallback.
- GSD Pi is development control plane only and is not a Jarvis runtime dependency.
- Core logic must not import provider-specific SDKs directly.
- Read-only/reversible tools may run immediately; destructive or externally visible actions require explicit confirmation.
- Persistent memory is local, user-controlled, inspectable, correctable, deletable, and retention-configurable.
- Primary performance metric is `speech_end_to_first_audio_ms`, reported with p50 and p95 values.
- Do not preserve the legacy `main.py` architecture or recreate a large `main.py`.
- Do not add an external event broker, microservice deployment, speculative plugin registry, or dependency that the standard library covers.
- Do not commit credentials, private recordings, memory databases, generated audio, or generated build artifacts.
- Every non-trivial logic change ends with a runnable check; foundation checks use `python -m unittest` and `python -m compileall`.
- After source changes, run `graphify update .` from the Jarvis project root.

## Plan scope

This plan covers the first independently testable sub-project: GSD/legacy extraction plus the greenfield foundation. Later plans must cover voice, provider bake-off, Hermes, memory, tools, startup effects, performance, reliability, and production hardware acceptance separately.

## File map

### Planning and evidence

- `.planning/PROJECT.md` — v2 product context and scope.
- `.planning/REQUIREMENTS.md` — committed v2 requirements and acceptance IDs.
- `.planning/ROADMAP.md` — phased GSD roadmap and phase dependencies.
- `.planning/STATE.md` — current phase, decisions, verification evidence, and next action.
- `.planning/research/legacy-extraction.md` — evidence-backed legacy capabilities, retained concepts, discard decisions, and known gaps.

### Foundation package

- `pyproject.toml` — editable installation, `jarvis` console entrypoint, and supported Python versions.
- `src/jarvis/__init__.py` — package version and public package marker only.
- `src/jarvis/config.py` — JSON configuration loading, environment-variable secret resolution, validation, and immutable runtime settings.
- `src/jarvis/core/contracts.py` — provider, component, health, tool, and lifecycle protocols shared by core modules.
- `src/jarvis/core/events.py` — small typed event records and in-process event delivery protocol.
- `src/jarvis/core/turn.py` — cancellation token and per-interaction context/deadline/trace data.
- `src/jarvis/core/state.py` — runtime state enum and legal transition validation.
- `src/jarvis/core/lifecycle.py` — supervisor start/stop, component health aggregation, bounded restart policy, and shutdown behavior.
- `src/jarvis/observability/tracing.py` — trace IDs, monotonic stage timestamps, and latency calculations.
- `src/jarvis/observability/logging.py` — redacted structured logging configuration.
- `src/jarvis/apps/runtime.py` — foundation runtime composition; no provider or hardware SDK imports.
- `src/jarvis/apps/cli.py` — `argparse` commands for `run` and `doctor`.
- `src/jarvis/__main__.py` — `python -m jarvis` entrypoint.

### Tests and documentation

- `tests/test_config.py` — configuration validation and secret-redaction behavior.
- `tests/test_turn.py` — cancellation, deadlines, and trace context.
- `tests/test_state.py` — legal and illegal runtime transitions.
- `tests/test_lifecycle.py` — supervisor startup, failure isolation, restart, and shutdown.
- `tests/test_observability.py` — trace timing and redacted logging.
- `tests/test_cli.py` — CLI help, doctor output, invalid configuration, and non-success degraded status.
- `docs/operations/foundation.md` — install, configure, run, doctor, state meanings, and verification commands.
- `.github/workflows/ci.yml` — foundation test and compile checks.

## Interfaces fixed by this plan

The following names and signatures are the handoff contract between tasks. Later plans may extend them only through a reviewed design change.

```python
# src/jarvis/config.py
def load_config(path: pathlib.Path, environ: Mapping[str, str] | None = None) -> RuntimeConfig: ...

# src/jarvis/core/turn.py
class CancellationToken:
    def cancel(self) -> None: ...
    @property
    def cancelled(self) -> bool: ...
    def raise_if_cancelled(self) -> None: ...

@dataclass(frozen=True)
class TurnContext:
    trace_id: str
    conversation_id: str
    deadline_monotonic: float
    cancellation: CancellationToken

# src/jarvis/core/state.py
class RuntimeState(str, enum.Enum): ...
def transition(current: RuntimeState, target: RuntimeState) -> RuntimeState: ...

# src/jarvis/core/contracts.py
class HealthStatus(str, enum.Enum): ...

@dataclass(frozen=True)
class HealthReport:
    name: str
    status: HealthStatus
    detail: str = ""
    required: bool = True
    retryable: bool = False

class ManagedComponent(Protocol):
    name: str
    required: bool
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def health(self) -> HealthReport: ...

# src/jarvis/core/lifecycle.py
class Supervisor:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def run_until_stopped(self) -> None: ...
    @property
    def state(self) -> RuntimeState: ...
    def health_snapshot(self) -> tuple[HealthReport, ...]: ...

# src/jarvis/observability/tracing.py
class InteractionTrace:
    def mark(self, stage: str, at: float | None = None) -> None: ...
    def elapsed_ms(self, start: str, end: str) -> float | None: ...
    def as_dict(self) -> dict[str, float | str | None]: ...
```

## Task 1: Capture v2 planning state and legacy evidence

**Files:**
- Modify: `.planning/PROJECT.md`
- Modify: `.planning/REQUIREMENTS.md`
- Modify: `.planning/ROADMAP.md`
- Modify: `.planning/STATE.md`
- Create: `.planning/research/legacy-extraction.md`

**Interfaces:**
- Consumes: approved design at `docs/superpowers/specs/2026-09-16-jarvis-v2-design.md`, existing legacy source, existing Graphify output, and current GSD state.
- Produces: committed GSD scope and evidence that foundation tasks can use without rereading the legacy monolith.

- [ ] **Step 1: Record legacy evidence before changing application files.**

  Use the existing Graphify graph and focused source inspection to record only verified facts: Python/Windows baseline; `main.py` composition; wake-word, VAD/audio, STT, TTS, Ollama, JSON memory, audio cache; PC/AIS/trading/web-search actions; setup and diagnostic helpers; tracked voice samples; and existing absence of automated application tests.

- [ ] **Step 2: Write the extraction report and discard decisions.**

  Create `.planning/research/legacy-extraction.md` with four sections: `Retain as requirement`, `Retain as evidence only`, `Replace in v2`, and `Not yet verified`. Explicitly record that the legacy `main.py` composition is replaced, current provider choices are not v2 defaults, and private logs/audio/memory data are not copied into the rebuild.

- [ ] **Step 3: Replace the onboarding-only project context with v2 context.**

  Update `.planning/PROJECT.md` to identify Jarvis v2, Windows-first launch, hybrid cloud-first providers, user-controlled local memory, managed Hermes, the approved design path, and the foundation plan as the active scope.

- [ ] **Step 4: Convert the approved design into requirement IDs and phase gates.**

  Update `.planning/REQUIREMENTS.md` with requirement IDs covering foundation/lifecycle, voice latency, provider neutrality, Hermes supervision, memory control, tool safety, observability, CLI/benchmarking, and real-hardware acceptance. Each ID must link to a phase and one or more verification scenarios from the design.

- [ ] **Step 5: Establish the GSD roadmap and current state.**

  Update `.planning/ROADMAP.md` with the approved phases and dependencies, keeping foundation as the first implementation phase after extraction. Update `.planning/STATE.md` to record the design commit, this plan path, open provider/Hermes implementation decisions that are intentionally deferred to their phases, and the exact next command for execution.

- [ ] **Step 6: Verify planning artifacts and commit them.**

  Run:

  ```bash
  rg -n "Windows-first|hybrid|Hermes|speech_end_to_first_audio_ms|Phase 01|acceptance" .planning
  git diff --check
  git status --short
  git add .planning/PROJECT.md .planning/REQUIREMENTS.md .planning/ROADMAP.md .planning/STATE.md .planning/research/legacy-extraction.md
  git commit -m "docs: establish Jarvis v2 GSD scope"
  ```

  Expected: all required decisions and phase references are present; no secrets, logs, binary audio, or memory data are staged.

## Task 2: Create the installable package and validated configuration

**Files:**
- Create: `pyproject.toml`
- Create: `src/jarvis/__init__.py`
- Create: `src/jarvis/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Consumes: JSON configuration path and process environment.
- Produces: `RuntimeConfig` and `load_config(path, environ)` for runtime and CLI tasks.

- [ ] **Step 1: Write failing configuration tests.**

  Test that `load_config()` accepts a complete JSON document, applies defaults for optional fields, rejects an absent required `runtime` object and invalid non-positive deadlines, resolves `${JARVIS_TEST_TOKEN}` from the supplied environment mapping, and never includes the resolved secret in `repr()` or the public dictionary returned by `public_dict()`.

  ```python
  class ConfigTests(unittest.TestCase):
      def test_loads_defaults_and_resolves_secret(self):
          config = load_config(self.path, {"JARVIS_TEST_TOKEN": "secret"})
          self.assertEqual(config.runtime.command_deadline_ms, 500)
          self.assertEqual(config.providers.fast_model_api_key, "secret")
          self.assertNotIn("secret", repr(config))
          self.assertNotIn("secret", json.dumps(config.public_dict()))
  ```

- [ ] **Step 2: Run the focused test to verify it fails.**

  Run `python -m unittest discover -s tests -p 'test_config.py' -v`.

  Expected: FAIL because the package and loader do not exist yet.

- [ ] **Step 3: Implement the minimal immutable configuration model.**

  Use frozen dataclasses for `RuntimeSettings`, `ProviderSettings`, `MemorySettings`, `SecuritySettings`, and `RuntimeConfig`. Load JSON with `json.load`, resolve only `${NAME}` values in secret fields, reject unknown top-level sections only when malformed types make validation unsafe, and expose `public_dict()` with secret values replaced by `"<redacted>"`. Keep configuration file paths and environment names explicit; do not add a settings framework.

- [ ] **Step 4: Add package metadata and the console entrypoint.**

  Configure `pyproject.toml` for Python `>=3.10,<3.12`, editable installation, `src` layout, and the `jarvis = "jarvis.apps.cli:main"` script. Do not list runtime dependencies in this foundation task.

- [ ] **Step 5: Run focused checks and commit.**

  Run `python -m unittest discover -s tests -p 'test_config.py' -v` and `python -m compileall -q src tests`.

  Expected: all configuration tests pass and compilation exits 0. Commit with `git add pyproject.toml src/jarvis/__init__.py src/jarvis/config.py tests/test_config.py && git commit -m "feat: add Jarvis v2 package configuration"`.

## Task 3: Define turn context, ports, events, and runtime state

**Files:**
- Create: `src/jarvis/core/__init__.py`
- Create: `src/jarvis/core/contracts.py`
- Create: `src/jarvis/core/events.py`
- Create: `src/jarvis/core/turn.py`
- Create: `src/jarvis/core/state.py`
- Create: `tests/test_turn.py`
- Create: `tests/test_state.py`

**Interfaces:**
- Consumes: `RuntimeConfig` from Task 2.
- Produces: `CancellationToken`, `TurnContext`, `RuntimeState`, `transition`, `HealthReport`, `ManagedComponent`, and the explicit port protocols for later voice/provider/Hermes plans.

- [ ] **Step 1: Write failing tests for cancellation and legal state transitions.**

  Cover idempotent cancellation, `raise_if_cancelled()` raising a local `TurnCancelled` exception, an expired deadline being observable through `TurnContext.expired`, legal transitions from `starting` to `ready` to `listening` to `thinking` to `speaking`, and illegal `failed` to `speaking` transitions raising `InvalidTransition`.

- [ ] **Step 2: Run the focused tests to verify they fail.**

  Run `python -m unittest discover -s tests -p 'test_turn.py' -v` and `python -m unittest discover -s tests -p 'test_state.py' -v`.

  Expected: FAIL because the core modules do not exist.

- [ ] **Step 3: Implement the turn and state primitives.**

  Generate trace IDs with `uuid.uuid4().hex`, use `time.monotonic()` for deadlines, use a thread-safe event in `CancellationToken` so later blocking adapters can observe cancellation, and keep legal transitions in one mapping in `state.py`. Include runtime states `starting`, `ready`, `listening`, `thinking`, `speaking`, `interrupted`, `degraded`, `failed`, and `stopping`.

- [ ] **Step 4: Define ports with `typing.Protocol`.**

  Add protocols for `AudioCapture`, `AudioPlayer`, `VoiceActivityDetector`, `WakeDetector`, `SpeechToText`, `TextToSpeech`, `IntentClassifier`, `AgentRuntime`, `MemoryProvider`, `ModelProvider`, `Tool`, `Skill`, `HealthCheck`, `StartupEffect`, and `ManagedComponent`. Keep methods small and typed around `TurnContext`; do not implement adapters or add SDK imports.

- [ ] **Step 5: Add typed event records without an external broker.**

  Define immutable `RuntimeStateChanged`, `HealthChanged`, `TurnStarted`, `TurnCancelled`, and `TurnCompleted` records, plus an `EventSink` protocol with `async def publish(event: RuntimeEvent) -> None`. The foundation runtime may use an in-process list-backed sink in tests; production fan-out is a later need.

- [ ] **Step 6: Run checks and commit.**

  Run `python -m unittest discover -s tests -p 'test_turn.py' -v`, `python -m unittest discover -s tests -p 'test_state.py' -v`, and `python -m compileall -q src tests`.

  Expected: PASS with no provider-specific imports. Commit with `git add src/jarvis/core tests/test_turn.py tests/test_state.py && git commit -m "feat: define Jarvis runtime contracts and state"`.

## Task 4: Implement supervisor lifecycle and health aggregation

**Files:**
- Create: `src/jarvis/core/lifecycle.py`
- Create: `tests/test_lifecycle.py`

**Interfaces:**
- Consumes: `ManagedComponent`, `HealthReport`, `RuntimeState`, `transition`, and event records from Task 3.
- Produces: `Supervisor.start()`, `Supervisor.stop()`, `Supervisor.run_until_stopped()`, `Supervisor.state`, and `Supervisor.health_snapshot()`.

- [ ] **Step 1: Write failing supervisor tests using tiny fake components.**

  Test concurrent startup of independent components, one failed optional component producing `degraded` while healthy components remain running, one recoverable component being restarted no more than the configured retry count, idempotent stop, and cancellation/stop causing `run_until_stopped()` to return. Use fake components that record calls; do not use sleeps longer than 10 ms.

- [ ] **Step 2: Run the focused tests to verify they fail.**

  Run `python -m unittest discover -s tests -p 'test_lifecycle.py' -v`.

  Expected: FAIL because `Supervisor` does not exist.

- [ ] **Step 3: Implement the supervisor with bounded restart policy.**

  Use `asyncio.gather` for startup, an `asyncio.Event` for stop, and a per-component health report. Keep retry count and backoff in `SupervisorPolicy`; default to two retries with a capped 250 ms backoff for tests and foundation use. A failed required component yields `failed`; a failed optional component yields `degraded`. Never report `ready` while a required health check is unknown or failed.

- [ ] **Step 4: Implement orderly shutdown.**

  Transition to `stopping`, cancel the supervisor stop event, call each component's `stop()` once in reverse startup order, collect exceptions into health/error records, and transition to `failed` only when shutdown itself cannot release a required component. Do not terminate arbitrary processes in this task; Hermes process ownership belongs to its later plan.

- [ ] **Step 5: Run checks and commit.**

  Run `python -m unittest discover -s tests -p 'test_lifecycle.py' -v` and `python -m compileall -q src tests`.

  Expected: PASS, including degraded behavior and idempotent shutdown. Commit with `git add src/jarvis/core/lifecycle.py tests/test_lifecycle.py && git commit -m "feat: add supervised runtime lifecycle"`.

## Task 5: Add trace timing and redacted structured logging

**Files:**
- Create: `src/jarvis/observability/__init__.py`
- Create: `src/jarvis/observability/tracing.py`
- Create: `src/jarvis/observability/logging.py`
- Create: `tests/test_observability.py`

**Interfaces:**
- Consumes: trace IDs and runtime events from Task 3.
- Produces: `InteractionTrace`, `configure_logging`, and redacted JSON-compatible records for the CLI and later benchmark collectors.

- [ ] **Step 1: Write failing observability tests.**

  Test that `InteractionTrace.mark()` records monotonic stages, `elapsed_ms("speech_end", "playback_start")` returns a non-negative value when both exist, missing stages return `None`, `as_dict()` includes `speech_end_to_first_audio_ms` when applicable, and a log record containing `api_key`, `authorization`, `token`, or `password` is emitted as `<redacted>`.

- [ ] **Step 2: Run the focused test to verify it fails.**

  Run `python -m unittest discover -s tests -p 'test_observability.py' -v`.

  Expected: FAIL because the observability modules do not exist.

- [ ] **Step 3: Implement monotonic trace collection.**

  Use `time.perf_counter()` and a fixed stage-name allowlist containing the required latency fields. Calculate `speech_end_to_first_audio_ms` from `speech_end` to `playback_start` and preserve raw stage timestamps only as relative milliseconds from trace creation. Serialize only scalar values.

- [ ] **Step 4: Implement redaction at the logging boundary.**

  Configure a standard-library logger with a JSON formatter. Recursively redact values under case-insensitive secret keys before serialization. Do not attempt to scrub arbitrary user speech content in the foundation; later memory/privacy work will define content retention.

- [ ] **Step 5: Run checks and commit.**

  Run `python -m unittest discover -s tests -p 'test_observability.py' -v` and `python -m compileall -q src tests`.

  Expected: PASS and no secret values in captured log output. Commit with `git add src/jarvis/observability tests/test_observability.py && git commit -m "feat: add trace timing and redacted logs"`.

## Task 6: Wire the foundation runtime and CLI diagnostics

**Files:**
- Create: `src/jarvis/apps/__init__.py`
- Create: `src/jarvis/apps/runtime.py`
- Create: `src/jarvis/apps/cli.py`
- Create: `src/jarvis/__main__.py`
- Create: `tests/test_cli.py`
- Create: `docs/operations/foundation.md`

**Interfaces:**
- Consumes: configuration, contracts, supervisor, state, and observability from Tasks 2–5.
- Produces: `jarvis run`, `jarvis doctor`, `python -m jarvis`, and a foundation runtime that refuses to claim voice readiness until real adapters are supplied.

- [ ] **Step 1: Write failing CLI tests.**

  Test `main(["--help"])` returns 0 and mentions `run` and `doctor`; `main(["doctor", "--config", valid_path, "--json"])` returns 0 with JSON containing `state` and component health; an invalid config returns 2 with an actionable error; and `main(["run", "--config", valid_path, "--check-only"])` returns non-zero/degraded when no audio/STT/TTS adapters are configured. Use `io.StringIO` and injected streams instead of spawning a process.

- [ ] **Step 2: Run the focused tests to verify they fail.**

  Run `python -m unittest discover -s tests -p 'test_cli.py' -v`.

  Expected: FAIL because the CLI and runtime composition do not exist.

- [ ] **Step 3: Implement runtime composition with explicit unavailable health checks.**

  Build `create_foundation_runtime(config)` in `apps/runtime.py` using a supervisor and named health checks for configuration, storage path, audio input, audio output, STT, TTS, fast model, Hermes, and network. Configuration/storage checks may pass; unimplemented hardware/provider/Hermes checks must report `unavailable`, leaving state `degraded` or `failed` rather than playing startup success effects.

- [ ] **Step 4: Implement the CLI with `argparse`.**

  Support `run` with `--config` and `--check-only`, and `doctor` with `--config` and `--json`. Return exit code 0 only for healthy doctor/check-only results, 2 for invalid input/configuration, and 1 for runtime failure. Ensure `python -m jarvis` delegates to the same `main()` function.

- [ ] **Step 5: Document reproducible foundation startup.**

  Document Python 3.10/3.11, `python -m venv .venv`, editable install, a minimal config example with environment-variable references, `jarvis doctor`, expected degraded output before later phases, and the exact foundation test commands. State clearly that `doctor` does not pretend unavailable voice components are healthy.

- [ ] **Step 6: Run the full foundation checks and commit.**

  Run:

  ```bash
  python -m unittest discover -s tests -v
  python -m compileall -q src tests
  python -m jarvis --help
  python -m jarvis doctor --help
  git diff --check
  ```

  Expected: all tests pass, both help commands exit 0, and no provider SDK is imported. Commit with `git add src/jarvis/apps src/jarvis/__main__.py tests/test_cli.py docs/operations/foundation.md && git commit -m "feat: add Jarvis foundation CLI"`.

## Task 7: CI, graph refresh, and foundation handoff evidence

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `.planning/STATE.md`
- Create: `.planning/phases/01-foundation/VERIFICATION.md`

**Interfaces:**
- Consumes: all foundation files and commits from Tasks 1–6.
- Produces: repeatable CI checks, refreshed Graphify output, and evidence for the GSD phase gate.

- [ ] **Step 1: Update CI with the smallest meaningful checks.**

  Configure CI for Python 3.10 and 3.11, install the package editable without optional provider dependencies, run `python -m unittest discover -s tests -v`, and run `python -m compileall -q src tests`.

- [ ] **Step 2: Run local CI-equivalent checks.**

  Run the exact commands from CI locally. Expected: both Python checks pass before Graphify refresh.

- [ ] **Step 3: Refresh the project graph.**

  Run `graphify update .` from `/home/manuelxose/workspace/jarvis`. Confirm the command completes and that `graphify-out/graph.json` contains the new `jarvis.apps`, `jarvis.core`, `jarvis.config`, and `jarvis.observability` nodes. Do not treat graph output as test evidence.

- [ ] **Step 4: Record verification evidence.**

  Create `.planning/phases/01-foundation/VERIFICATION.md` with the commit IDs, exact commands, exit results, CLI degraded-mode result, CI result, and Graphify refresh result. Include unresolved work only as named later phases, not as unbounded foundation defects.

- [ ] **Step 5: Update GSD state and commit the gate.**

  Update `.planning/STATE.md` from foundation active to foundation verified only after all evidence exists, then run `git diff --check` and commit with `git add .github/workflows/ci.yml .planning/STATE.md .planning/phases/01-foundation/VERIFICATION.md && git commit -m "chore: verify Jarvis foundation"`. Leave refreshed `graphify-out/` files untracked according to the repository policy.

## Execution order and dependencies

Execute tasks in order. Task 1 is documentation-only and must precede source changes. Task 2 establishes packaging/configuration; Task 3 establishes contracts; Tasks 4 and 5 can then be implemented independently but should be completed before Task 6. Task 7 is the phase gate and must run after all source tests pass.

The next plans should begin only after Task 7 evidence is complete:

- provider bake-off and benchmark harness;
- real voice vertical slice;
- wake/clap/barge-in;
- managed Hermes;
- persistent memory;
- tools and permission gateway;
- startup effects, performance, reliability, and hardware acceptance.

## Plan self-review

- Spec coverage: runtime boundaries and state are covered by Tasks 2–4; streaming/voice ports are defined for later phases; provider neutrality is enforced by Task 3 and CLI checks; Hermes lifecycle is reserved for its own phase; memory and tools are requirements recorded in Task 1 and future-plan boundaries; observability is Task 5; CLI/startup health is Task 6; testing and acceptance evidence are Task 7.
- Placeholder scan: no unfinished markers or vague implementation steps are present.
- Type consistency: `RuntimeConfig`, `CancellationToken`, `TurnContext`, `RuntimeState`, `HealthReport`, `ManagedComponent`, `Supervisor`, and `InteractionTrace` are introduced before later tasks consume them.
- Scope check: this plan intentionally stops after a working foundation and does not mix provider/audio/Hermes/memory implementation into one change.
