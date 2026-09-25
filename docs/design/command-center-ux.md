# Command Center — Information Architecture & UX (Phase 2)

Prototype entry point: `ui/index.html` (built app root), source: `ui/src/features/command-center/`.

## Navigation model

Single-window app shell (`AppFrame`, `ui/src/components/layout/AppFrame.tsx`): persistent top bar, persistent left sidebar (primary navigation, 8 workspaces), a scrollable center workspace, and a persistent right rail (system + agent snapshot — always visible context, not a workspace).

This is a flat, single-level hierarchy by design — section 6 explicitly asks for progressive disclosure and warns against showing every information area at once. The eight workspaces (Overview, Conversation, Memory, System, Agents, Development, Media, Settings) are the top-level disclosure boundary; each workspace discloses further detail internally (e.g. System's per-metric history sparkline is one click deeper than the CPU/RAM/GPU summary).

Two navigation paths to the same destination, deliberately:

1. **Sidebar click** — primary, always-visible, mouse-first.
2. **Command palette (Ctrl/Cmd+K)** — keyboard-first, fuzzy-filtered "Go to <workspace>" commands (`ui/src/components/interaction/CommandPalette.tsx`). Verified in `command-center.spec.ts`.

## Workspaces

| Workspace | Purpose | Key components |
| --- | --- | --- |
| Overview | Landing view: assistant status hero + a 2-up glance at Conversation and Development | `AssistantStatus`, `Panel` |
| Conversation | Full message history, search, streaming placeholder | `ConversationMessage`, `SearchInput`, `StreamingResponsePlaceholder`, `EmptyState` |
| Memory | Fact count, last-write time, searchable recent facts — explicitly separated from "current conversation context" (section 6 requirement) | `SearchInput`, `EmptyState` |
| System | CPU/RAM/GPU with sparkline history; a connection-state toggle demonstrates connected/loading/error/disconnected | `SystemHealthPanel`, `ChartContainer`, `Skeleton`, `EmptyState` |
| Agents | Running agent list + task progress list | `AgentActivityList`, `TaskProgress` |
| Development | GSD Pi milestone/worktree/branch/VS Code status + activity timeline | `Timeline` |
| Media | Playback transport (mock, interactive play/pause) | `MediaControl` |
| Settings | Appearance — states the single-direction/single-density decision explicitly rather than exposing unbuilt options | `Badge` |

## Right rail (persistent context)

System (CPU/RAM/GPU value only, no chart — the System *workspace* is where history lives) and Agents (same list as the Agents workspace, compact). This rail is the one piece of "always know what Jarvis is doing" state that doesn't require navigating away from whatever workspace the user is in — directly serves design objective B (low interaction friction).

## Assistant state

The top bar's `StatusIndicator` and Overview's `AssistantStatus` both read from one `assistantState` value owned by `CommandCenter.tsx`. A "Simulate assistant state" menu in the top bar (▾ icon) exists for this design-review prototype only — it is not a production control; the production path swaps this for the Jarvis UI Bridge event stream (`ui/src/protocol/events.ts`, transport not implemented this phase) without changing any component's props.

## Data states

Per section 11, mock data is explicit and never claims to be live: `ui/src/mocks/fixtures.ts` is the single mock source, imported only by workspace components, never by design-system or foundation components (so swapping in a real provider later is a `features/` change, not a `components/` rewrite). Loading/error/disconnected/empty states are implemented and demonstrable, not just designed on paper — System workspace's four-way toggle and Conversation's search-to-empty path are both real, clickable, and covered by the screenshot suite.

## Keyboard and flow

- Sidebar items are real `<button>`s with `aria-current="page"` — Tab-reachable, not `div onClick`.
- Ctrl/Cmd+K opens the command palette from anywhere; Arrow keys move selection, Enter activates, Esc closes (Base UI Dialog default).
- Scrollable regions (`.center`, `.sidebar`, `.right`) carry `tabIndex={0}` so keyboard users can scroll them without a pointer (WCAG 2.1.1, caught by the automated axe pass — see `phase-2-review.md`).
