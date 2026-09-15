# Task 1 implementation report — Capture v2 planning state and legacy evidence

## Status

DONE_WITH_CONCERNS

## Files changed

- `.planning/PROJECT.md` — replaced onboarding-only context with the approved Jarvis v2 scope.
- `.planning/REQUIREMENTS.md` — added requirement IDs, phase links, and design verification scenarios.
- `.planning/ROADMAP.md` — established Phase 00 extraction through Phase 13 production acceptance, with Phase 01 Foundation as the first implementation phase.
- `.planning/STATE.md` — recorded the approved design commit, foundation plan path, deferred decisions, and next execution command.
- `.planning/research/legacy-extraction.md` — recorded verified legacy evidence, discard decisions, v2 replacements, and unverified items.
- `.superpowers/sdd/2026-09-16-jarvis-v2-foundation/task-1-report.md` — this report.

No runtime/application code changed. Legacy source and assets remain in place as reference.

## Evidence sources used

- Approved design: `docs/superpowers/specs/2026-09-16-jarvis-v2-design.md` at `1c0165999bdaa4a557883f2f90b3b3c33e48bddd`.
- Existing Graphify graph: `graphify-out/graph.json`, queried for legacy `main.py` composition and test evidence. It verified the composition imports and `build_runtime_components()` relationship for wake word, audio utilities, STT, TTS, Ollama, JSON memory, audio cache, and action routing.
- Focused tracked-source inventory: `main.py`; `voice/wake_word.py`, `voice/audio_utils.py`, `voice/stt.py`, `voice/tts.py`; `brain/llm.py`, `brain/memory.py`, `brain/action_router.py`; `cache/audio_cache.py`; `actions/pc_control.py`, `actions/ais_monitor.py`, `actions/trading_monitor.py`, `actions/web_search.py`; `setup.py`; `diagnostico_wakeword.py`; and `monitor_status.py`.
- Tracked-file test inventory: no `test*` or `*_test.py` application files were present.

Secrets, environment files, private logs, binary audio, conversation data, and memory data were not read or copied. Graphify was not refreshed because this task changed only planning/evidence documents; `graphify-out/` remains local-only and unstaged.

## Verification

| Command | Result |
| --- | --- |
| `rg -n "Windows-first|hybrid|Hermes|speech_end_to_first_audio_ms|Phase 01|acceptance" .planning` | Exit 0; required decisions and phase/acceptance references found. |
| `git diff --check` | Exit 0; no whitespace errors. |
| `git status --short` | Confirmed only the five requested planning artifacts before commit. |
| `git diff --cached --check` | Exit 0; no staged whitespace errors. |
| `git diff --cached --name-only` | Exactly the five requested `.planning` artifacts; no `graphify-out/` files staged. |

No application tests were run: this task made no runtime changes, and the verified legacy inventory has no automated application tests.

## Commit

`910171e docs: establish Jarvis v2 GSD scope`

## Concerns

- `.planning/` is ignored by repository configuration. The exact requested `git add` was rejected, so only the five explicit task artifacts were force-added with `git add -f` before the planning commit.
- The report itself is committed separately because the required planning commit hash must be recorded after that commit exists.

## Fix round 1 — architectural boundaries

### Changed files

- `.planning/REQUIREMENTS.md` — added V-12 and extended FND-01/PRV-01 with the mandated Phase 01 runtime boundaries: GSD Pi is development-only and absent from the runtime; coordination is in-process with standard-library primitives and excludes brokers, microservices, and speculative registries/abstractions; provider SDKs and Windows APIs remain adapter-only and never import into orchestration or domain modules.
- `.superpowers/sdd/2026-09-16-jarvis-v2-foundation/task-1-report.md` — appended this fix evidence.

### Verification

| Command | Result |
| --- | --- |
| `rg -n 'FND-01|PRV-01|V-12|GSD Pi|in-process|standard-library|event broker|microservice|speculative registr|Provider SDK|orchestration|domain modules' .planning/REQUIREMENTS.md` | Exit 0; V-12, FND-01, and PRV-01 explicitly contain every required boundary and Phase 01 verification reference. |
| `git diff --check` | Exit 0; no whitespace errors. |
| `git status --short` | Confirmed `.planning/REQUIREMENTS.md` was the only fix artifact before commit. |

No application tests were run: this is a planning-only requirements correction with no runtime code changes.

### Commit

`17d11fe docs: define Jarvis runtime boundaries`

### Concern

`.planning/` remains ignored by repository configuration, so the explicitly scoped requirements file required `git add -f`.
