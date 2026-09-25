# Jarvis Frontend Quality Gates

Date: 2026-09-24. These gates apply to every future frontend PR, starting Phase 2. Phase 1 verified the tooling exists and runs (see `docs/engineering/jarvis-ui-phase-1-audit.md` §4/25) — it did not need to satisfy gates like "no monolithic components," since there is exactly one trivial component.

| Gate | Rule | Enforced by |
| --- | --- | --- |
| Type safety | No avoidable `any`; `strict: true` stays on | `tsc -b` in CI (to be wired), currently run manually — passes on the Phase 1 skeleton |
| Accessibility | Keyboard reachability and focus visibility for every interactive element; WCAG 2.1 AA | `accessibility` skill during development; Playwright asserts role/keyboard reachability per component |
| Performance | No unnecessary rerender loops; React DevTools profiler check before merging a component with derived state | `core-web-vitals` skill; bundle-size check against the budget in `docs/architecture/frontend-architecture.md` §7 |
| Visual | Rendered output reviewed via Playwright screenshot, not source-read alone | `@playwright/test`, `web-quality-audit` skill |
| Layout | No accidental overflow at 1920×1080 through 4K | Playwright viewport matrix (`playwright.config.ts` projects) |
| Motion | Respect `prefers-reduced-motion`; only animate compositor-friendly properties (`transform`, `opacity`) | `--motion-duration-*` tokens collapse to `0ms` under reduced-motion (verified in `tokens.css`); manual review until motion work starts |
| Architecture | No component imports Node/Electron/Tauri/Python APIs directly — only through `protocol/` and `shell/` | Code review; import-boundary rule in `docs/architecture/frontend-architecture.md` §2 |
| Components | No monolithic components — a component doing layout + data-fetching + business logic gets split | Code review, `design-an-interface` skill for shaping the split |
| Design system | No hardcoded colors/spacing in component CSS — reference a `--token`, add one if missing | Code review against `design-system/tokens.css` |
| Testing | Every interactive component gets at least one Playwright assertion (renders, keyboard path, no console errors) | Pattern established in `src/testing/app-shell.spec.ts` |

## What "PASS" looks like for a PR

1. `npm run build` exits 0 (runs `tsc -b` then `vite build`).
2. `npx oxlint` exits 0.
3. `npx playwright test` exits 0 against updated/reviewed snapshots.
4. No new hardcoded color/spacing/font value in component CSS.
5. No new import of `electron`, `node:*`, or a Python-facing API from inside `app/`, `features/`, `components/`, `hooks/`, `state/`, or `design-system/`.

## Anti-patterns (section 20) — explicitly rejected here, not just listed

These are checked in review, not automated (no linter reliably detects "looks like a generic AI dashboard"):

generic admin-dashboard templates; endless/nested cards; arbitrary glassmorphism; gradient abuse; cyan glow on every component; excessive corner rounding; oversized hero typography on a desktop tool; meaningless charts; fake telemetry, terminal output, or agent activity; animation that slows interaction; icon-only controls without a discoverable label; low-contrast "sci-fi" type; hardcoded pixel positioning; inaccessible custom controls; effects that compromise readability.

`tokens.css` intentionally ships with neutral placeholder values (not cyan, not glowing) specifically so nobody backfills these anti-patterns by defaulting to "futuristic == cyan glow" before Phase 2's actual art direction exists.
