# Task 3 report — core turn context, contracts, events, and state

## Changed files

- `src/jarvis/core/__init__.py`
- `src/jarvis/core/contracts.py`
- `src/jarvis/core/events.py`
- `src/jarvis/core/state.py`
- `src/jarvis/core/turn.py`
- `tests/test_turn.py`
- `tests/test_state.py`

## TDD commands and results

Red:

```text
python3 -m unittest discover -s tests -p 'test_turn.py' -v
python3 -m unittest discover -s tests -p 'test_state.py' -v
```

Both commands failed as expected before implementation: `jarvis.core.turn` and `jarvis.core.state` did not exist. After expanding the tests for events and contracts, the tests also failed as expected because `jarvis.core.events` and `jarvis.core.contracts` did not exist.

Green:

```text
python3 -m unittest discover -s tests -p 'test_turn.py' -v
python3 -m unittest discover -s tests -p 'test_state.py' -v
python3 -m compileall -q src tests
```

All checks passed: `test_turn.py` ran 4 tests, `test_state.py` ran 3 tests, and compilation completed successfully. `python3` was used because this environment has no `python` executable alias.

Self-review also passed:

```text
git diff --check
rg -n -i '^(import|from) (openai|anthropic|google|azure|boto|win32|ctypes\\.windll|winsdk)' src/jarvis/core
graphify update .
```

There were no whitespace errors and no provider or Windows SDK imports in the core modules. The local code graph was refreshed.

## Public interfaces

- Turn control: `CancellationToken`, local `TurnCancelled`, and immutable `TurnContext` with UUID4 hex trace IDs, monotonic deadlines, cancellation, `expired`, and `from_config()`.
- State: string-backed `RuntimeState`, `InvalidTransition`, and `transition(current, target)` backed by one legal-transition mapping.
- Health and lifecycle: string-backed `HealthStatus`, immutable `HealthReport(name, status, detail="", required=True, retryable=False)`, and `ManagedComponent`.
- Ports: `AudioCapture`, `AudioPlayer`, `VoiceActivityDetector`, `WakeDetector`, `SpeechToText`, `TextToSpeech`, `IntentClassifier`, `AgentRuntime`, `MemoryProvider`, `ModelProvider`, `Tool`, `Skill`, `HealthCheck`, and `StartupEffect` protocols.
- Events: immutable `RuntimeStateChanged`, `HealthChanged`, `TurnStarted`, `TurnCancelled`, `TurnCompleted`, `RuntimeEvent`, and async `EventSink` protocol. Delivery remains in-process; no broker was added.

## Commit

- Implementation: `d306c9707ff40588e116ff836faa46a14b39403c` (`feat: define Jarvis runtime contracts and state`)

## Concerns

None. The event sink is intentionally a protocol only; production fan-out is deferred as specified.

---

# Task 3 fix report — round 1

## Changed files

- `tests/test_turn.py`
- `.superpowers/sdd/2026-09-16-jarvis-v2-foundation/task-3-report.md`

## Verification

```text
python3 -m unittest discover -s tests -p 'test_turn.py' -v
```

Result: PASS — 5 tests ran, including controlled monotonic deadline conversion,
UUID-hex trace format, and fresh uncancelled cancellation tokens from
`TurnContext.from_config()`.

```text
python3 -m compileall -q src tests
```

Result: PASS — exited 0.

## Commit

- Fix: `dc56434e591cc84bc48fb6f2dd78aa2e1603584a` (`test: cover TurnContext config factory`)

## Concerns

None.
