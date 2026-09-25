# Component Inventory (Phase 2)

Status key: **BUILT** (implemented, type-checked, lint-clean, used by a prototype), **DEFERRED** (not built — a concrete reason is given, not silently dropped), **PARTIAL** (built but with a documented gap).

## Foundations — `ui/src/components/foundations/`

| Component | Variants / props | States | Status |
| --- | --- | --- | --- |
| `Button` | `default` \| `primary` \| `danger` | hover, active (pressed-scale), disabled, focus-visible | BUILT |
| `IconButton` | requires `aria-label` | hover, active, disabled, `aria-pressed` (toggle) | BUILT |
| `Badge` | `neutral` \| `success` \| `warning` \| `danger` \| `info` | static | BUILT |
| `Tooltip` | `label` + one focusable child | hover, focus, Esc-dismiss (Base UI) | BUILT |
| `Divider` | horizontal \| vertical | static | BUILT |
| `StatusIndicator` | 8 `AssistantState` values, shape + color + label per state | — | BUILT |
| `Spinner` / `Skeleton` (`LoadingIndicator.tsx`) | width override | reduced-motion (animation off) | BUILT |
| Icons | — | — | DEFERRED — no icon library installed. Current UI uses text/emoji glyphs (⋯, ⌘K, ⏮/⏸/▶/⏭, ⌕) as placeholders. Installing an icon library (e.g. lucide) is a real, justified Phase 3 dependency once the icon *set* is actually designed — adding one now would be guessing at content, not architecture |

## Layout — `ui/src/components/layout/`

| Component | Purpose | Status |
| --- | --- | --- |
| `AppFrame` | Command Center grid shell (topbar/sidebar/center/right), narrow-window collapse at 1280px | BUILT |
| `Sidebar` | Keyboard-reachable primary nav list | BUILT |
| `TopBar` | Title + assistant status + actions slot | BUILT |
| `Panel` | Titled card, the base unit of every workspace | BUILT |
| `TabGroup` | Wraps Base UI `Tabs` | BUILT — not yet consumed by a prototype workspace (no workspace needed in-panel tabs this phase); verified to compile and lint clean, ready for Phase 3 |
| `Toolbar` | `role="toolbar"` row, used by System workspace's state switcher | BUILT |
| `FloatingContainer` | Elevated surface — every Overlay card, and the base for future dialogs/toasts | BUILT |
| Resizable panel layouts | — | DEFERRED — no current workspace needs user-resizable panes; the fixed 3-column grid satisfies every Phase 2 layout. Building a resize system speculatively would be exactly the "abstraction nobody asked for" the project's own engineering rules warn against. Revisit when a workspace genuinely needs it (e.g. a wide log view competing with System charts) |

## Data — `ui/src/components/data/`

| Component | Purpose | Status |
| --- | --- | --- |
| `MetricDisplay` | Labelled value + optional progress bar | BUILT |
| `Table` | Accessible `<table>` wrapper (caption, `scope="col"`) | BUILT — not yet consumed by a prototype workspace; no tabular dataset exists yet that isn't better served by `LogView`/`Timeline`. Kept because Agents/Development are the obvious near-term consumers |
| `LogView` | Timestamped, sourced, monospace log lines; `role="log"`, `aria-live` gated by a `live` prop (mock data stays non-live) | BUILT |
| `ProgressIndicator` | Determinate + indeterminate (`percent: null`) task progress | BUILT |
| `Timeline` | Vertical event list with tone-colored markers | BUILT |
| `StatusCard` | `StatusIndicator` + title/description composed in a `Panel` | BUILT |
| `ChartContainer` / `Sparkline` | Inline SVG trend line — no charting dependency added | BUILT |
| `EmptyState` | Icon-less centered message + optional action | BUILT |

## Interaction — `ui/src/components/interaction/`

| Component | Purpose | Status |
| --- | --- | --- |
| `SearchInput` | Labelled `type="search"` field | BUILT |
| `DropdownMenu` | Wraps Base UI `Menu`; trigger composition merges Base UI's props into the caller's own element (no nested-interactive-element bug — see `phase-2-review.md`) | BUILT |
| `ContextMenu` | Wraps Base UI `ContextMenu` | BUILT — implemented since Base UI ships it at no marginal cost, but no workspace currently right-click-enables it (no requirement named one yet); ready, not wired in |
| `Dialog` | Wraps Base UI `Dialog` — focus-trapped, Esc-dismissable, titled | BUILT — not yet consumed (no confirmation flow exists yet; Phase 2 explicitly excludes "production action confirmations") |
| `CommandPalette` | Global Ctrl/Cmd+K searchable command list | BUILT, wired into Command Center |
| Notification (`Toast`) | Wraps Base UI `Toast` (`NotificationProvider`/`NotificationViewport`/`useNotify`) | BUILT — provider is mounted at the app root; nothing currently calls `useNotify` (no toast-worthy event exists in this design-only phase) |
| Keyboard shortcuts | Ctrl/Cmd+K (palette), Esc (dismiss any Base UI overlay), Tab/Arrow (nav/menu) | Documented behavior, not a standalone component — cross-cutting, covered by each interactive component's own keyboard contract (Base UI) plus `command-center.spec.ts`'s keyboard tests |

## Jarvis-specific — `ui/src/components/jarvis/`

| Component | Purpose | Status |
| --- | --- | --- |
| `VisualCorePlaceholder` | Reserved slot for the future holographic orb — flat CSS blob + glow, swappable for `<canvas>`/R3F later without a layout change | BUILT |
| `AssistantStatus` | Core + status + headline, compact and full variants | BUILT |
| `ConversationMessage` | You/Jarvis/tool message bubble | BUILT |
| `StreamingResponsePlaceholder` | Skeleton-based "Jarvis is responding" placeholder — never fakes partial text | BUILT |
| `AgentActivityList` | Agent name + detail + state row list, with an explicit empty state | BUILT |
| `TaskProgress` | Task title + status badge + progress bar | BUILT |
| `SystemHealthPanel` | Metric rows + sparkline history | BUILT |
| `MediaControl` | Play/pause/next/prev + track label | BUILT |

## Summary

30 components built across 5 families, 0 fabricated/stubbed (every component renders real content in at least one of the two prototypes, or is verified to compile/lint if not yet consumed — see PARTIAL notes above for the 4 "built but not yet wired" cases: `TabGroup`, `Table`, `Dialog`, `ContextMenu`, `Notification`). 2 items deliberately deferred with a stated reason (Icons, Resizable panel layouts) — neither blocks Phase 3 since both are additive, not structural.
