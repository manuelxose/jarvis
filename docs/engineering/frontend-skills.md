# Jarvis UI — Frontend Skill Stack (Phase 1)

Date: 2026-09-24
Method: skills already installed in `~/.gsd/agent/skills/` were read directly (frontmatter + body). Skills not installed locally (Vercel `react-view-transitions`, Microsoft `frontend-design-review`, Addy Osmani `frontend-ui-engineering`) were verified against their live upstream repositories via fetch — not assumed from the task brief.

## 1. Comparison matrix

| Skill | Source | Version/Revision | Responsibility | Status | Reason |
| --- | --- | --- | --- | --- | --- |
| `frontend-design` | github.com/anthropics/skills | local copy, license `LICENSE.txt` (proprietary Anthropic terms, not MIT) | Visual Design | **ADOPT** (active) | Already installed and in the GSD `always`/`prefer` allowlist for UI work; directly matches section 5's stated purpose (art direction, avoiding generic AI aesthetics). No overlap — nothing else in the stack does art direction. |
| `design-an-interface` | local skill (`codebase-design` family) | n/a | UX / API & module shaping | **ADOPT** (active) | Already installed and preferred. Covers interface/module shaping (multiple-design comparison), not pure visual — complements `frontend-design` rather than duplicating it. |
| `make-interfaces-feel-better` | local skill | n/a | UX polish / Motion detail | **ADOPT** (active) | Already installed and preferred. Covers typography, surfaces, animation micro-detail (`typography.md`, `surfaces.md`, `animations.md`, `performance.md`) — the concrete "how" layer under `frontend-design`'s "what" layer. |
| `accessibility` | web-quality-skills, MIT, v1.0 | local copy | Accessibility | **ADOPT** (active) | Already installed and preferred. WCAG 2.1 / POUR-based; directly required by acceptance item 19 ("Keyboard and focus behavior must work"). |
| `core-web-vitals` | web-quality-skills, MIT, v1.0 | local copy | Performance | **ADOPT** (active) | Already installed and preferred. LCP/INP/CLS — directly required by the performance budget (section 21). |
| `web-quality-audit` | web-quality-skills, MIT, v1.0 | local copy | Visual QA / Performance / A11y (Lighthouse-style) | **ADOPT** (active) | Already installed and preferred. Closest existing skill to "rendered verification," though it audits Lighthouse-style categories, not pixel/regression diffing — Playwright fills that specific gap (section 18), not a skill. |
| `react-best-practices` | github.com/vercel-labs/agent-skills, MIT, v1.0.0 (Vercel Engineering) | local copy present | Frontend Engineering (React perf/architecture) | **REFERENCE ONLY** (installed, not activated) | Present on disk but listed in this repo's `avoid_skills` policy (`GSD Skill Preferences`), and the situational "UI work → use" list deliberately excludes it. Preserving existing GSD config per section 4 — not silently flipping policy. Its 57 rules (bundle imports, waterfalls, rerender control) were read directly and used as input to `docs/architecture/frontend-architecture.md` even though the skill itself stays inactive. |
| `web-design-guidelines` | github.com/vercel-labs/agent-skills, MIT, v1.0.0 (Vercel) | local copy present | Accessibility / UX review | **REFERENCE ONLY** (installed, not activated) | Same as above — in `avoid_skills`, deliberately excluded from the situational UI-work list despite being frontend-relevant. Overlaps `accessibility` + `web-quality-audit`; kept disabled to avoid redundant activation, consulted manually for its interaction-quality rules (focus, forms, keyboard) folded into `docs/frontend/frontend-quality-gates.md`. |
| `react-view-transitions` | github.com/vercel-labs/agent-skills, MIT | not installed | Motion (shared-element transitions, `prefers-reduced-motion`) | **REJECT (for this phase)** | Confirmed real via live fetch: implements the View Transition API with reduced-motion guidance. Section 17 explicitly forbids implementing complex motion in Phase 1. Revisit when a motion-implementation phase is scoped — do not install prematurely (YAGNI). |
| `frontend-design-review` (Microsoft) | github.com/microsoft/skills, MIT, path `.github/skills/frontend-design-review/` | not installed | Visual QA / Design-system compliance | **REJECT** | Confirmed real via live fetch (175-skill monorepo, this one language-agnostic/"Core"). Purpose overlaps `frontend-design` + `web-quality-audit` + `accessibility` almost entirely ("design system compliance, quality pillars, accessibility, creative aesthetics"). Adding it would duplicate three already-active skills — violates "prefer a small number of excellent complementary skills." |
| `frontend-ui-engineering` (Addy Osmani) | github.com/addyosmani/agent-skills, MIT | not installed | Frontend Engineering (component architecture, WCAG 2.1 AA, responsive design) | **ADOPT-CANDIDATE, not yet installed** | Confirmed real via live fetch: "Component architecture, design systems, responsive design, WCAG 2.1 AA accessibility." This is the one genuine coverage gap — `react-best-practices` is policy-disabled and nothing else in the active stack covers React/TS component-architecture discipline specifically. Installing a new *global* skill (`~/.gsd/agent/skills/`) is an out-of-repo, shared-state change — not made unilaterally in this phase. Recommended for explicit approval before Phase 2 component work begins. |

## 2. Coverage against the required responsibility categories

| Category | Covered by | Gap |
| --- | --- | --- |
| Visual Design | `frontend-design` | none |
| UX | `design-an-interface`, `make-interfaces-feel-better` | none |
| Frontend Engineering (React/TS) | *(none active)* | `react-best-practices` exists but is policy-disabled; `frontend-ui-engineering` is a real but not-yet-installed candidate. Mitigated this phase by reading `react-best-practices` directly as reference input to the architecture doc. |
| Design System | `frontend-design`, `make-interfaces-feel-better` | tokens/primitives architecture is this phase's deliverable (`docs/architecture/frontend-architecture.md`), not a skill's job |
| Motion | `make-interfaces-feel-better` (micro-detail only) | full transition strategy (`react-view-transitions`) deliberately deferred (section 17) |
| Accessibility | `accessibility` | none |
| Performance | `core-web-vitals` | none |
| Visual QA | `web-quality-audit` (Lighthouse-style) | pixel/regression diffing has no skill — addressed by Playwright infrastructure directly (section 18), not a skill's job |

## 3. GSD Pi discovery verification

- Discovery path: `~/.gsd/agent/settings.json` → `"skills": ["/home/manuelxose/.gsd/agent/skills"]`.
- Active-skill policy lives in this repo's `GSD Skill Preferences` block (`always_use_skills: [ponytail]`, `prefer_skills: [...]`, `avoid_skills: [...]`), not in the skills directory itself — a skill being present on disk does not make it ACTIVE.
- Verified present on disk (ADOPT + REFERENCE ONLY rows above): `frontend-design`, `design-an-interface`, `make-interfaces-feel-better`, `accessibility`, `core-web-vitals`, `web-quality-audit`, `react-best-practices`, `web-design-guidelines` — confirmed by direct `find`/`cat` against `~/.gsd/agent/skills/*/SKILL.md`, not inferred.
- Not present on disk (REJECT / ADOPT-CANDIDATE rows): `react-view-transitions`, `frontend-design-review`, `frontend-ui-engineering` — confirmed absent locally; existence and content confirmed only against the live upstream repo.

## 4. Update strategy

- Skills are a shared, user-level resource (`~/.gsd/agent/skills/`), not project-local — Jarvis does not vendor or fork them.
- Project-specific rules (Jarvis aesthetic direction, Windows/overlay constraints, Command Center density rules, prohibited patterns) belong in a thin companion skill, `jarvis-ui-context`, created once Phase 2 visual decisions exist — not created now, since there is nothing project-specific to say yet beyond what `docs/frontend/frontend-quality-gates.md` and `docs/frontend/future-capabilities.md` already capture as plain docs.
- Do not duplicate general React/UX knowledge already in `react-best-practices`, `frontend-design`, etc. inside any future Jarvis-specific skill.

## 5. Rejected / overlapping candidates summary

| Candidate | Verdict | One-line reason |
| --- | --- | --- |
| `react-view-transitions` | REJECT (this phase) | Motion implementation out of scope until a dedicated phase (section 17). |
| `frontend-design-review` (Microsoft) | REJECT | Duplicates `frontend-design` + `web-quality-audit` + `accessibility`. |
| `react-best-practices` (Vercel) | REFERENCE ONLY | Present but policy-disabled (`avoid_skills`); content read manually instead. |
| `web-design-guidelines` (Vercel) | REFERENCE ONLY | Present but policy-disabled (`avoid_skills`); overlaps `accessibility`/`web-quality-audit`. |
| `frontend-ui-engineering` (Addy Osmani) | ADOPT-CANDIDATE, deferred | Genuinely fills the React/TS engineering gap, but installing a new global skill needs explicit approval — not assumed here. |
