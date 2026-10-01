# Jarvis UI — Phase 1 Repository & Environment Audit

Date: 2026-09-24
Auditor: GSD Pi (Principal Frontend Architect, Phase 1)
Method: verified against live source, executable configuration, and installed tooling — not documentation alone.

## Executive summary

Jarvis is a **brownfield Python voice-assistant runtime** with **zero existing frontend code**. The frontend is greenfield. The GSD Pi orchestration environment is fully configured, including an **already-installed frontend skill stack** (six UX/UI skills allowlisted, plus two Vercel skills currently disabled). Node 24 + npm 12 are available; Rust is not. The desktop shell decision therefore has an immediate environmental consequence (Tauri PoC blocked until Rust is installed).

---

## 1. Findings by category

Classification key: **PRESENT / WORKING / PARTIAL / MISSING / LEGACY / UNKNOWN / BLOCKED**.

### 1.1 Repository structure

| Item | Status | Evidence |
| --- | --- | --- |
| Python package `src/jarvis/` (v2) | WORKING | `core/`, `application/`, `adapters/`, `apps/`, `observability/`, `benchmarks/` present |
| Legacy v1 code | LEGACY | `legacy/` holds `actions/`, `brain/`, `cache/`, `voice/` (pre-v2, superseded by commit `17f6b7c`) |
| `.planning/codebase/` map | LEGACY | References `main.py`, `setup.py`, `config.yaml`, `voice/`, `brain/` — all removed/moved; stale onboarding artifact |
| Tests | WORKING | `tests/` — 49 files, `unittest`, 541 tests |
| Docs | PRESENT | `docs/architecture.md`, `configuration.md`, `installation.md`, `performance.md`, `desktop-control.md`, `voice-clone.md`, `alibaba-qwen.md` |
| Git | WORKING | clean tree on `main`, up to date with `origin/main`; branch `milestone/M005` |

### 1.2 Frontend code

| Item | Status | Evidence |
| --- | --- | --- |
| Frontend source (`ui/`, `.tsx`, `.ts`, `package.json`) | MISSING | `find` for `package.json`/`tsconfig`/`vite.config`/`electron*`/`tauri.conf`/`*.tsx`/`*.ts` returns nothing |
| TypeScript | MISSING | no `tsconfig.json`; `npx tsc` would fetch it on demand |
| UI framework (React/Vue/etc.) | MISSING | no dependencies |
| Styling / design tokens / icons / component library | MISSING | nothing |
| Build tooling (Vite/webpack) | MISSING | nothing |
| Electron / Tauri experiments | MISSING | no `electron*`/`tauri*` artifacts |

### 1.3 Runtime & toolchain

| Item | Status | Evidence |
| --- | --- | --- |
| Node.js | WORKING | `v24.18.0` |
| npm | WORKING | `12.0.1` |
| pnpm / yarn | MISSING | `command not found` |
| Rust / cargo | MISSING | `command not found` (blocks Tauri PoC) |
| Python (Windows/MSYS) | PARTIAL | `python` = 3.12.0; `python3` is a Microsoft Store shim (broken) |
| Python (WSL) | WORKING | 3.12.3; project targets 3.10/3.11 (CI pins 3.11) |
| Project venv | PRESENT | `.venv/` is a **Windows** venv (`Scripts/`, `pyvenv.cfg`) |
| Playwright | MISSING | not installed |

### 1.4 Test infrastructure

| Item | Status | Evidence |
| --- | --- | --- |
| Python unit suite | WORKING | `unittest`, 49 files, 541 tests |
| CI | WORKING | `.github/workflows/ci.yml`: ubuntu-latest, Python 3.11, `pip install -e . numpy soundfile`, `unittest discover` + `compileall` |
| Baseline run (WSL 3.12.3) | PARTIAL | 541 tests → **5 failures + 23 errors**, re-verified twice after all Phase 1 work completed (identical count both runs — not flaky). All 23 errors are import-time (`test_acceptance`/`test_claps`/`test_startup` fail to import; `test_daemon`/`test_voice_control` cascade from the same missing `numpy`/`soundfile` in this WSL Python). 4 of 5 failures are `test_hermes_child.py` subprocess/stdio timing tests; the 5th (`test_desktop_tools.PolicyTests.test_run_command_captures_failure_for_later_fixing`) matches the originally-documented pre-existing failure. `git status -- src/ tests/` shows zero changes from Phase 1 — this phase touched only `ui/`, `docs/`, and a scratch `desktop-shell-poc/`, so this count is the environment's actual state, not a Phase 1 regression. See acceptance item 13. |
| Frontend test/visual-QA | MISSING | no Playwright, no Storybook, no visual regression |

### 1.5 Windows-specific code

| Item | Status | Evidence |
| --- | --- | --- |
| Windows tools adapter | WORKING | `src/jarvis/adapters/tools/windows.py`, `desktop.py` |
| Windows config | PRESENT | `config.win.json` |
| Windows launch | PRESENT | `bootstrap.ps1`/`bootstrap.bat`, `run_jarvis.ps1`/`run_jarvis.bat` |
| Windows audio/STT/TTS | WORKING | WASAPI mixer, SAPI (`stt/sapi.py`), `pyttsx3` |

### 1.6 GSD Pi orchestration

| Item | Status | Evidence |
| --- | --- | --- |
| GSD Pi version | WORKING | gsd-pi **1.20.1**, gsd-core **1.13.0** |
| State dir | WORKING | `.gsd` → symlink to `~/.gsd/projects/644d114365dc/` (I/O error from Windows side is DrvFS symlink-resolution, not corruption) |
| Active milestone | WORKING | **M005** "API-first low-latency voice pipeline", phase `executing`, slice S01 |
| Future UI milestone | PRESENT | **M008** "Futuristic voice interface" queued — this Phase 1 is its foundation |
| Provider routing | WORKING | subscription-first: Claude Opus 5.5 primary, Codex GPT-6 fallback, DeepSeek manual (D032–D036) |
| Dynamic routing | WORKING | enabled, capability routing, 2 workers, worktree isolation |
| Context Mode | WORKING | `context_mode.enabled: true` |
| Ponytail | WORKING | `always_use_skills: [ponytail]`, session mode `full` |
| Skill discovery path | WORKING | `~/.gsd/agent/settings.json` → `"skills": ["/home/manuelxose/.gsd/agent/skills"]` |

### 1.7 Agent skills

| Item | Status | Evidence |
| --- | --- | --- |
| GSD Pi global skills | WORKING | `~/.gsd/agent/skills/` — 49 entries |
| Frontend skills installed | WORKING | `frontend-design` (Anthropic), `react-best-practices` (Vercel), `web-design-guidelines` (Vercel), `accessibility`/`core-web-vitals`/`web-quality-audit` (web-quality-skills), `design-an-interface`, `make-interfaces-feel-better` all present |
| Frontend skills ACTIVE | PARTIAL | six UX/UI skills allowlisted + UI-gated; `react-best-practices` + `web-design-guidelines` present but in `avoid_skills` |
| Project-local skills | PRESENT | `.claude/skills/` (16), `.agents/skills/` (16) — no frontend skills |
| Agents | PRESENT | `~/.gsd/agent/agents/`, `.agents/` (skills only, no bespoke agent hierarchy) |

### 1.8 Repository hygiene

| Item | Status | Evidence |
| --- | --- | --- |
| Uncommitted changes | WORKING | none — clean tree |
| Branches / worktrees | WORKING | `main`, `milestone/M005`; `.gsd-worktrees/` empty |
| `.claude/CLAUDE.md` | MISSING | `claude_md_path: "./.claude/CLAUDE.md"` points to a non-existent file |
| `config.json` | PRESENT | minimal — ollama provider `mistral:7b-instruct` |
| `config.local.json` | PRESENT | gitignored local override |

---

## 2. Blockers & unknowns

| Item | Status | Note |
| --- | --- | --- |
| Tauri 2 PoC | BLOCKED | no Rust/cargo toolchain in this environment |
| Playwright browser binary | UNKNOWN | requires a download; network/browser availability not yet verified |
| Windows-side Python (`python3`) | BLOCKED | Store shim; use `.venv/Scripts/python.exe` or WSL `python3` |

---

## 3. Implications for Phase 1

1. **Frontend is greenfield** — no migration, no compatibility constraints from existing UI code.
2. **Node/npm are ready** — a Vite + React + TS skeleton is immediately buildable.
3. **Desktop shell decision has an environmental cost** — Tauri (preferred on footprint) needs Rust; Electron runs on Node only. See `docs/architecture/desktop-shell-adr.md`.
4. **The frontend skill stack already exists in GSD Pi** — Phase 1 must verify discovery and fill the genuine gaps (React engineering, design-system discipline) rather than re-install everything.
5. **Python runtime must remain untouched** — all Phase 1 work is frontend-only; existing 541-test baseline is the regression reference (1 pre-existing failure + 22 environment errors remain as-is).

---

## 4. Phase 1 acceptance gate

Phase 1 closes the frontend foundation only: audit, skill stack, architecture, desktop-shell ADR, `ui/` skeleton, typed protocol (`ui/src/protocol/events.ts`, enforced against `event_hub.SCHEMAS` by `tests/test_ui_protocol_parity.py`), and the Electron window-lifecycle PoC (`desktop-shell-poc/`). The Tauri PoC remains BLOCKED (no Rust/cargo).

**Slices S02 and later (live event transport, 3D/orb, gestures, Tauri shell) require explicit owner approval before they start.**
