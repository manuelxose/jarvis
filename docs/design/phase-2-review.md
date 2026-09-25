# Phase 2 Review — Visual, Accessibility, Performance

Date: 2026-09-24.

## Visual review

Three directions built as real rendered mockups (`design-exploration/direction-{a,b,c}-*.html`), screenshotted at 1920×1080 (`docs/design/screenshots/`), and visually inspected — not judged from description. Full comparison and rationale: `docs/design/visual-directions.md`. Selected: **Adaptive Command** (Direction C base + A's core glow + B's data typography).

Implementation screenshots (all 8 Command Center workspaces × 2 viewports, all 8 Overlay states × 2 viewports — 32 total) live in `ui/src/testing/{command-center,overlay}.spec.ts-snapshots/`, regenerated deterministically by `npx playwright test --update-snapshots` (reduced-motion forced on for the whole suite, see `playwright.config.ts`).

Visual QA finding fixed during this phase: `mockDevelopment`'s "vscode" key rendered as "Vscode" via naive `text-transform: capitalize` — switched to explicit `{ label, value }` fixtures instead of deriving labels from object keys (`ui/src/mocks/fixtures.ts`).

## Accessibility results

Tooling: `@axe-core/playwright` (new, justified dev dependency — automated WCAG scanning has no existing equivalent in the repo). Run against both prototypes, both viewports, `.include('#root')` scoped for the Command Center to exclude Playwright's own test harness chrome.

Two real violations found and fixed during this phase (not pre-emptive changes — both are logged with before/after):

1. **Color contrast (serious)**: `.message.user .meta` used `--text-secondary` (#8a8f9a) on a `color-mix()`-tinted background, measuring 4.09:1 against the WCAG AA 4.5:1 minimum. Fixed by switching to `--text-primary` on that specific tinted surface (`ConversationMessage.module.css`). Recorded as a reusable rule in `.claude/skills/jarvis-visual-identity/SKILL.md`.
2. **Scrollable region not focusable (serious)**: `AppFrame`'s `.sidebar`/`.center`/`.right` panes have `overflow-y: auto` but were not independently keyboard-focusable. Fixed with `tabIndex={0}` on each (`AppFrame.tsx`), and on the Overlay's expanded-state message list (`Overlay.tsx`).

A third defect was found and fixed independently of axe, by direct HTML inspection: `DropdownMenu`/`Dialog`/`Tooltip` originally wrapped their trigger element in an extra Base UI-rendered `<span>`/element, producing **nested interactive elements** (a real accessibility and semantics bug — two focus stops for one control) and an invalid `type="button"` attribute on a non-button element. Fixed by using Base UI's `render={element}` composition (merges Base UI's props into the caller's real element instead of wrapping it) for `DropdownMenu.tsx`, `Dialog.tsx`, and `Tooltip.tsx`. `ContextMenu`'s default `<div>` wrapper was left as-is — it wraps a region, not a control, so no nesting bug exists there.

Final state: **zero critical or serious axe violations** on both prototypes, both viewports (4/4 accessibility test runs pass — `command-center.spec.ts` ×2 projects, `overlay.spec.ts` ×2 projects).

Manual checks also performed: keyboard reachability of sidebar nav, command palette, and overlay state controls (explicit Playwright `toBeFocused()` assertions, not just axe); realistic long content (full repo path `milestone/M008-futuristic-voice-interface`, multi-line agent detail text with `text-overflow: ellipsis` + `title` tooltip fallback, four concurrent mock agents) — no layout overflow found (`scrollWidth <= clientWidth` asserted explicitly at a 1200px narrow window).

## Performance observations

| Metric | Phase 1 baseline | Phase 2 | Budget (`frontend-architecture.md`) |
| --- | --- | --- | --- |
| Initial JS, Command Center (gzip) | 68.67 KB (single bundle, no splitting) | 126.29 KB (`main` 55.30 KB + shared Base UI/design-system chunk 70.99 KB) | < 250 KB |
| Initial JS, Overlay (gzip) | n/a (didn't exist) | 72.13 KB (`overlay` 1.14 KB + shared chunk) | < 250 KB |
| Initial CSS (gzip) | 0.62 KB | 4.42 KB (main 2.29 KB + shared 2.13 KB) | not budgeted separately |
| Build time | ~120ms | ~270ms | not budgeted |

Both entry points stay well under the 250 KB budget despite adding a full accessible component library (Base UI) and two feature apps. The ~84% growth on the Command Center bundle vs. Phase 1 is the real cost of that library, not bloat — no unused/speculative dependency was added (see `docs/architecture/frontend-architecture.md`'s dependency reasoning, updated implicitly by this phase's `@base-ui/react` and `@axe-core/playwright` additions).

Not measured this phase (explicitly deferred, not silently skipped): React re-render profiling (no state complex enough yet to need it — all workspace state is local `useState`, no shared store), runtime idle-CPU measurement (no live transport exists yet to poll or not-poll), Windows-specific DPI/paint cost (no Windows host available in this WSL session, same limitation as Phase 1's shell ADR).

## Known limitations

- Tauri PoC still blocked on missing Rust toolchain (unchanged from Phase 1 — not this phase's scope to resolve).
- 4K (3840×2160) viewport not screenshot-tested — only 1080p/1440p have Playwright projects. The narrow-window collapse (1280px breakpoint) and flex-based layout should scale up cleanly, but this is unverified, not claimed as tested.
- Windows display-scaling behavior is unverified (no Windows host in this environment).
- `executing` and `processing` assistant states render identically (`StatusIndicator` shares the `--accent-processing` shape+color for both, differing only in label) — a real simplification, documented in `docs/design/desktop-overlay-ux.md`, not a missed requirement.
- Four components are built, type-checked, and lint-clean but not yet consumed by either prototype: `TabGroup`, `Table`, `Dialog`, `Notification` (Toast). None block Phase 3 — see `docs/design/component-inventory.md` for why each exists ahead of its first use.
- `frontend-ui-engineering` skill gap flagged in Phase 1 (`docs/engineering/frontend-skills.md`) is still open — not installed this phase either (still requires explicit approval to install a new *global* skill).
- Occasional single-run screenshot flake observed under >4 parallel Playwright workers on this WSL host (paint-timing, not a design defect — passed in isolation both times it occurred). Mitigated by capping `workers: 4` in `playwright.config.ts`; documented as a `ponytail:` comment there with the escalation path (move to serial) if it recurs.

## Test suite summary

`cd ui && npx playwright test` → **46/46 passed**, 0 flaky, on a clean re-run with `workers: 4` capped. Breakdown: 8 Command Center workspace screenshots × 2 viewports (16), 8 Overlay state screenshots × 2 viewports (16), 2 accessibility scans × 2 viewports (4), 2 console-error smoke tests (2), 1 command palette flow × 2 viewports (2), 1 narrow-window test × 2 viewports (2), 1 keyboard-focus test × 2 viewports (2), 1 keyboard-reachable-controls test × 2 viewports (2).

`npx tsc -b` → exit 0. `npx oxlint` → 0 errors, 2 stylistic warnings (both in `Notification.tsx`, react-refresh granularity — not a correctness issue, documented rather than papered over with file-splitting churn).
