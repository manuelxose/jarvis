# Task 4 report: supervisor lifecycle and health aggregation

## Changed files

- `src/jarvis/core/lifecycle.py` — stdlib-only `Supervisor` and `SupervisorPolicy`.
- `tests/test_lifecycle.py` — isolated async tests using fake managed components.
- `.superpowers/sdd/2026-09-16-jarvis-v2-foundation/task-4-report.md` — this implementation record.

## Lifecycle design

`Supervisor` accepts managed components, an optional bounded retry policy, and an
optional typed event sink. Startup uses `asyncio.gather` so independent components
start concurrently. Each component produces a `HealthReport`; retryable failed
components receive at most `retries` additional attempts, with a capped 250 ms
backoff. Aggregated health yields `READY` only when every report is healthy,
`DEGRADED` for non-required failures or degraded health, and `FAILED` for a failed
required component.

`run_until_stopped()` starts the supervisor then waits on its `asyncio.Event`.
`stop()` is idempotent, signals that event, stops every component once in reverse
configured startup order, and records shutdown errors as failed health reports.
Normal state changes use `transition()` and publish `RuntimeStateChanged`; health
changes publish `HealthChanged` through the supplied typed event sink.

## TDD evidence

1. RED: `python3 -m unittest discover -s tests -p 'test_lifecycle.py' -v`
   failed as intended with `ModuleNotFoundError: No module named
   'jarvis.core.lifecycle'` before production code existed.
2. GREEN: after adding the supervisor, the same focused suite passed.
3. RED: the required-shutdown-failure test failed with `InvalidTransition:
   Cannot transition from stopping to failed`, exposing the terminal-state
   conflict in the shared transition table.
4. GREEN: after handling that exceptional shutdown outcome, the focused suite
   passed with 7 tests.

## Verification commands and results

- `python3 -m unittest discover -s tests -p 'test_lifecycle.py' -v && python3 -m compileall -q src tests`
  — passed: 7 lifecycle tests; compilation completed with exit 0.
- `python3 -m unittest discover -s tests -v && python3 -m compileall -q src tests && git diff --check`
  — passed: 19 tests; compilation and whitespace check completed with exit 0.
- `graphify update .`
  — completed: rebuilt the local code graph (514 nodes, 956 edges).

The requested `python -m ...` commands could not run because this environment has
no `python` executable. `python3` is the available interpreter (Python 3.12), so
it was used for all executable verification; the implementation itself uses only
Python 3.10-compatible syntax and stdlib modules.

## Commit

- `7a72e77246019d25a372bd52db38102fce092855` — `feat: add supervised runtime lifecycle`

## Concerns

`RuntimeState.STOPPING` is terminal in the pre-existing `transition()` table, but
the task requires a required component that cannot stop to finish as `FAILED`.
The supervisor uses validated transitions everywhere else and directly records
this one post-stop exceptional state so that the required shutdown failure is not
hidden. The shared state transition table may later want an explicit
`STOPPING -> FAILED` transition to make that rule universal.

---

# Task 4 fix round 1: lifecycle/state contract hardening

## Changed files

- `src/jarvis/core/state.py` — permits validated `STOPPING -> FAILED`.
- `src/jarvis/core/lifecycle.py` — routes required shutdown failures through
  `_set_state()` and ignores startup completion after shutdown begins.
- `tests/test_state.py` — covers the shutdown-failure transition.
- `tests/test_lifecycle.py` — covers stop during in-flight startup and typed,
  meaningful startup/shutdown event publication.
- `.superpowers/sdd/2026-09-16-jarvis-v2-foundation/task-4-report.md` — this
  fix record.

## Commands and results

- `python3 -m unittest discover -s tests -p 'test_state.py' -v && python3 -m unittest discover -s tests -p 'test_lifecycle.py' -v`
  — passed: 4 state tests and 9 lifecycle tests.
- `python3 -m unittest discover -s tests -v && python3 -m compileall -q src tests && git diff --check`
  — passed: 22 tests; compilation and whitespace checks completed with exit 0.
- `graphify update .`
  — completed: refreshed the code graph (520 nodes, 975 edges).

## Commit

`197eecd225290ac383f24a1d3a2824068522ad12` — `fix: harden supervisor shutdown transitions`

## Concerns

None. The environment exposes `python3` (Python 3.12), so the standard-library
tests were run with it; the project code remains compatible with Python 3.10/3.11.
