# Desktop Overlay — UX & States (Phase 2)

Prototype entry point: `ui/overlay.html`, source: `ui/src/features/overlay/Overlay.tsx`. Same design tokens and component library as the Command Center — verified by construction (both import from `ui/src/design-system/` and `ui/src/components/`, no overlay-specific token overrides exist).

## States implemented

| State | Layout | Notes |
| --- | --- | --- |
| Compact idle | Pill: `VisualCorePlaceholder` (22px) + `StatusIndicator` | Smallest footprint — the default resting state |
| Listening | Same pill, `listening` state (cyan, animated core under normal motion) | |
| Speaking | 320px card: core + status + one-line caption | Caption is a single response line, not a full transcript — that's what Expanded is for |
| Processing | Compact pill, `processing` state (spinning ring shape, not just color) | |
| Expanded conversation | 360px card, scrollable message list (`ConversationMessage`), reuses the exact same component the Command Center's Conversation workspace uses | Demonstrates design-system consistency requirement (section 3.C) directly, not just by claim |
| Notification | 320px card, `notification` state + one-line event text | For "agent finished a task" class events, distinct from Speaking (assistant voice) and Error |
| Error | 320px card, `error` state (triangle shape + red) + actionable text ("say 'cancel' to stop") | Never color-only — see `StatusIndicator` |
| Disconnected | 320px card, `EmptyState` ("Jarvis is offline") | Distinct from Error — no active problem being retried, just no connection |

Compact and Expanded are container-size states; Listening/Speaking/Processing/Error/Offline are content states. The nine states listed in the brief (compact, expanded, notification, listening, speaking, processing, executing, error, offline) collapse to these eight prototype cards because "executing" and "processing" render identically in `StatusIndicator` today (both map to `--accent-processing`, distinguished only by label) — documented as a known simplification, not a missed requirement: see `phase-2-review.md`'s limitations.

## Compact ↔ expanded relationship

The compact pill and the expanded card are not two different components — they are the same `FloatingContainer` + `VisualCorePlaceholder` + `StatusIndicator` primitives at different sizes and with different optional children (caption vs. full message list). This is deliberate: it's what makes "smooth transitions between states" (section 12) a CSS/layout-size problem for a later phase, not a component-swap problem.

## Overlay ↔ Command Center relationship

The overlay has no navigation chrome of its own by design — production behavior (out of scope this phase, section 12 explicitly excludes global shortcuts/always-on-top/window management) is: a click/shortcut on the overlay brings the Command Center window forward, already true of the underlying Electron PoC's lifecycle (`desktop-shell-poc/main.js`, Phase 1). This phase does not wire that interaction — it is a `shell/` boundary concern per `docs/architecture/frontend-architecture.md`, not a component concern.

## Desktop placement

Section 6 asks for "layouts for screen corners, common workspaces, and different monitor sizes." This phase does not implement anchor/positioning logic (that's a native-window concern, owned by whichever shell eventually hosts `overlay.html` — Electron or Tauri, per the Phase 1 ADR) — the web content itself is corner-agnostic (the `FloatingContainer` has no hardcoded position; `Overlay.tsx`'s `.desktop` wrapper right-aligns purely for this in-browser demo). Documented as deferred to the desktop-shell integration phase, not silently skipped.

## Verification

`ui/src/testing/overlay.spec.ts`: all 8 states screenshot-tested at both 1080p/1440p, zero console errors, state-switcher buttons keyboard-reachable, zero critical/serious axe violations (`@axe-core/playwright`). See `phase-2-review.md` for the full run.
