# Task 2 implementation report — package configuration

## Status

Implemented the Jarvis v2 package metadata and immutable JSON configuration foundation. The implementation is limited to the four files named in the task brief.

## Files changed

- `pyproject.toml` — Python 3.10/3.11 package metadata, `src` layout, empty runtime dependency list, and the `jarvis` console entry point.
- `src/jarvis/__init__.py` — package exports for `RuntimeConfig` and `load_config`.
- `src/jarvis/config.py` — frozen configuration dataclasses, JSON loading, validation, environment-secret resolution, and redacted public output.
- `tests/test_config.py` — focused standard-library tests for defaults, validation, redaction, and the supplied environment mapping.

## TDD evidence

Initial red check:

```text
$ python3 -m unittest discover -s tests -p 'test_config.py' -v
ModuleNotFoundError: No module named 'jarvis'
FAILED (errors=1)
```

The supplied-environment regression test was then added before its fix:

```text
$ python3 -m unittest discover -s tests -p 'test_config.py' -v
test_uses_the_supplied_environment_mapping ... FAIL
AssertionError: ValueError not raised
FAILED (failures=1)
EXIT=1
```

## Final verification

The task brief names `python`, but this environment has no `python` executable. The equivalent available interpreter was `python3`.

```text
$ python3 -m unittest discover -s tests -p 'test_config.py' -v
test_loads_defaults_and_resolves_secret ... ok
test_rejects_missing_runtime_section ... ok
test_rejects_non_positive_command_deadline ... ok
test_uses_the_supplied_environment_mapping ... ok

Ran 4 tests in 0.002s
OK
EXIT=0
```

```text
$ python3 -m compileall -q src tests
EXIT=0
```

```text
$ git diff --check
DIFF_CHECK_EXIT=0
```

```text
$ graphify update .
[graphify watch] Rebuilt: 370 nodes, 627 edges, 30 communities
Code graph updated.
EXIT=0
```

Self-review command:

```text
$ python3 - <<'PY'
# Parses pyproject.toml and config.py, verifies Python bounds, no runtime
# dependencies, the console entrypoint, src layout, and all five frozen dataclasses.
PY
metadata and frozen dataclass review: OK
EXIT=0
```

## Commit

`aa10252 feat: add Jarvis v2 package configuration`

## Concerns

- The local interpreter is Python 3.12 while the package intentionally declares support only for Python 3.10 and 3.11, so editable installation was not attempted with this unsupported interpreter.
- The configured console target is intentionally not invoked: `jarvis.apps.cli` belongs to a later CLI task and was specified as the required entry point.
- `graphify update .` refreshed ignored local graph artifacts; they were not staged.
