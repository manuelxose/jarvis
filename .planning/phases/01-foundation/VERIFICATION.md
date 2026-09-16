# Phase 01 — Foundation verification

## Scope and commits

The foundation gate covers the completed Task 1–6 work and this Task 7 CI/evidence gate:

- Task 1: `910171e docs: establish Jarvis v2 GSD scope`
- Task 2: `aa10252 feat: add Jarvis v2 package configuration`
- Task 3: `d306c9707ff40588e116ff836faa46a14b39403c` and `dc56434e591cc84bc48fb6f2dd78aa2e1603584a`
- Task 4: `7a72e77246019d25a372bd52db38102fce092855` and `197eecd225290ac383f24a1d3a2824068522ad12`
- Task 5: `bf99edf feat: add trace timing and redacted logs` and `f59e784 fix: harden observability boundaries`
- Task 6: `6f138d7 feat: add Jarvis foundation CLI`

## CI configuration

`.github/workflows/ci.yml` runs on pushes and pull requests to `main` with Python 3.10 and 3.11. Each matrix entry runs these exact commands without optional provider dependencies:

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

The GitHub Actions matrix result is pending its next push or pull request. The available local host has only `/usr/bin/python3` (3.12) and no `pip`, so the documented source-path fallback was used for the runnable CI checks:

```sh
PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -v
# exit 0: 32 tests passed

PYTHONPATH=src /usr/bin/python3 -m compileall -q src tests
# exit 0
```

## CLI degraded-mode smoke check

```sh
PYTHONPATH=src /usr/bin/python3 -m jarvis run --config /dev/stdin --check-only <<'EOF'
{"runtime": {}}
EOF
# exit 1 (expected): state: degraded; unconfigured audio, STT, TTS, model, Hermes, and network adapters reported unavailable
```

## Graphify refresh

```sh
graphify update .
# exit 0: Code graph updated; no code-graph topology changes detected
```

`graphify-out/graph.json` contains the foundation package source nodes under its AST schema:

- `jarvis.apps`: 30 nodes, including `src_jarvis_apps_runtime_healthcomponent` from `src/jarvis/apps/runtime.py`
- `jarvis.core`: 84 nodes, including `src_jarvis_core_turn_cancellationtoken` from `src/jarvis/core/turn.py`
- `jarvis.config`: 12 nodes, including `src_jarvis_config_memorysettings` from `src/jarvis/config.py`
- `jarvis.observability`: 20 nodes, including `src_jarvis_observability_logging_jsonformatter` from `src/jarvis/observability/logging.py`

Graphify output is local-only, ignored, and is not test evidence or part of this commit.

## Deferred work

Provider selection remains Phase 02; voice work remains Phases 03–04; Hermes remains Phase 05; memory remains Phase 06; provider routing remains Phase 07; tools and integrations remain Phase 09; startup and benchmarks remain Phases 10–11; reliability remains Phase 12; and production acceptance remains Phase 13. These are planned later phases, not foundation defects.
