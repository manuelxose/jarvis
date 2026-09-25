# Jarvis Design System (Phase 2)

Date: 2026-09-24. Direction: **Adaptive Command** (see `docs/design/visual-directions.md`). Source of truth for values: `ui/src/design-system/tokens.css` — this document explains the *why*, the CSS is the *what*; if they disagree, the CSS wins.

## Visual principles

1. **State drives color, not decoration.** The four accent colors (`--accent-listening`, `--accent-speaking`, `--accent-processing`, plus `--state-danger`/`--state-offline`) are the only saturated colors in the system. Everything else is graphite/near-black surfaces and off-white text. No decorative gradients outside the visual-core placeholder.
2. **Glow is reserved, not ambient.** `--glow-core` is applied to exactly one element family (`VisualCorePlaceholder`) and active-state rings. Panels, buttons, and chrome never glow — this was Direction A's rejected failure mode (see visual-directions.md).
3. **Numerics are monospace.** Any value that is measured, timestamped, or counted (CPU%, log lines, agent names, dev status) renders in `--font-mono` with tabular figures. Prose stays `--font-sans`. This is a hard rule, not a suggestion — it is what makes the interface scannable in a long technical session.
4. **State is never color-only.** `StatusIndicator` (`ui/src/components/foundations/StatusIndicator.tsx`) pairs every state with a distinct shape (ring, square, triangle, spinner, slashed circle) and a text label. Verified by the axe `color-contrast`/`color` rule set in the Playwright suite (zero critical/serious violations, see `phase-2-review.md`).

## Tokens

Full source: `ui/src/design-system/tokens.css`. Categories present, all real (no Phase 1 placeholders remain):

| Category | Tokens | Notes |
| --- | --- | --- |
| Surfaces | `--surface-app`, `--surface-app-gradient`, `--surface-panel`, `--surface-panel-soft`, `--surface-floating`, `--surface-interactive-hover/active`, `--surface-selected`, `--surface-scrim` | Three-tier elevation: app → panel → floating |
| Text | `--text-primary`, `--text-secondary`, `--text-disabled`, `--text-on-accent` | `--text-secondary` (#8a8f9a) fails 4.5:1 on tinted surfaces — see the `.message.user .meta` override in `ConversationMessage.module.css` for the pattern to follow when reusing it on a non-neutral background |
| Accent (state) | `--accent-listening`, `--accent-speaking`, `--accent-processing`, `--accent-primary` (alias) | Borrowed from Direction C, see visual-directions.md |
| Semantic state | `--state-success/warning/danger/info/offline` | Paired with shape in `StatusIndicator`, never used alone |
| Borders | `--border-subtle`, `--border-strong`, `--border-accent`, `--border-danger` | |
| Typography | `--font-sans`, `--font-mono`, `--text-xs`…`--text-2xl`, `--weight-*`, `--leading-*`, `--tracking-*`, `--font-feature-numeric` | See "Data typography rule" above |
| Geometry | `--space-1`…`--space-10` (4px base), `--radius-sm/md/lg/full`, `--panel-min-width`, `--layout-grid-columns`, `--layout-topbar-height` | |
| Depth | `--elevation-0/1/2/3`, `--glow-core`, `--glow-core-inset` | Glow capped per principle 2 above |
| Interaction | `--focus-ring-*`, `--disabled-opacity`, `--pressed-scale` | `:focus-visible` is defined globally in `tokens.css`, not per-component |
| Motion | `--motion-duration-fast/base/slow`, `--motion-ease-standard/decelerate/accelerate` | Collapse to `0ms` under `prefers-reduced-motion: reduce`; component-level keyframe animations (spinner, pulse, shimmer) each carry their own reduced-motion override (see `StatusIndicator.module.css`, `VisualCorePlaceholder.module.css`, `LoadingIndicator.module.css`) since CSS `animation-duration` tokens alone don't disable keyframe `animation-name` |
| Z-index | `--z-base/panel/floating/overlay/toast` | |

## Typography

- UI/prose: `--font-sans` (system font stack — no webfont dependency, matches Phase 1's decision to avoid a font-loading cost on a desktop shell).
- Data/mono: `--font-mono` (`ui-monospace`/Cascadia Code/SFMono/Consolas stack).
- Scale: `--text-xs` (11px, uppercase labels) → `--text-2xl` (28px, headline). No decorative display font — readability was weighted over "futuristic" typography per design objective B.

## Component states

Every interactive component in `ui/src/components/` implements, where applicable: default, hover, focus-visible (token-driven outline, never removed), active/pressed (`--pressed-scale`), disabled (`--disabled-opacity` + `pointer-events` via `:disabled`), loading (`Spinner`/`Skeleton`), and error (danger border/text + icon shape, not color alone). Concrete state matrix per component family is in `docs/design/component-inventory.md`.

## Responsive desktop behavior

Verified via Playwright (`ui/src/testing/command-center.spec.ts`):

- 1920×1080 and 2560×1440 — both are dedicated Playwright projects (`desktop-1080p`, `desktop-1440p`), every screenshot captured at both.
- 4K (3840×2160) — not separately captured; the layout is percentage/flex based with no fixed-pixel breakpoints above 1280px, so it scales, but this is not yet evidenced by a dedicated screenshot (documented gap, see `phase-2-review.md`).
- Windows display scaling — not testable in this WSL environment (no Windows host); the CSS uses relative units (`rem`/token-based `px` that inherit root font-size) so OS-level scaling should reflow correctly, but this is unverified, not claimed as tested.
- Narrow desktop window — `AppFrame.module.css`'s `@media (max-width: 1280px)` collapses the right rail; verified by `command-center.spec.ts`'s "narrow desktop window collapses the right rail without clipping content" test (asserts `scrollWidth <= clientWidth`).
