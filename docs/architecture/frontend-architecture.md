# Jarvis Frontend Architecture (Phase 1)

Date: 2026-09-24. Status: decided for Phase 1 scaffolding; component-primitive and state-management choices are deliberately deferred where no concrete requirement exists yet (YAGNI).

## 1. Stack decision

| Concern | Choice | Why |
| --- | --- | --- |
| Build tool | Vite 8 | Section 9 explicitly rejects Next.js — this is a desktop client, not a web app. Vite gives fast HMR and a plain static build any desktop shell can load (`file://` or a local dev server), with zero server-rendering machinery Jarvis doesn't need. |
| Framework | React 19 | Already the assumed target across the whole brief (skill stack, primitives, view-transitions). No alternative evaluated — not a genuine open question here. |
| Language | TypeScript, `strict: true` + `noUncheckedIndexedAccess` | Required by acceptance item 9. Verified: `npx tsc -b` exits 0 on the scaffold. |
| Linter | `oxlint` (Vite's default template choice) | Already present in the scaffold, Rust-based and fast. No ESLint added — would duplicate it (ponytail: reuse what's there). |
| Styling | CSS Modules + CSS custom properties for tokens (`src/design-system/tokens.css`) | No compile-time utility framework yet. Tailwind is explicitly not justified this phase — there is no component volume yet to amortize its setup cost against, and semantic tokens (section 15) are a better fit for a design system that doesn't exist yet than utility classes are. Revisit only if Phase 2 shows real repeated friction. |
| State management | React built-ins only (`useState`/`useReducer`/context) | No global store exists in the codebase to justify Zustand/Jotai/Redux yet. The one genuine future state-management driver — the Jarvis event stream (`protocol/events.ts`) — now has an SSE transport (`services/sse-transport.ts`), but the command center does not consume it yet, so picking a store remains speculative. |
| Component primitives | Base UI (unstyled, evaluated below) for Phase 2 component work; not installed yet in Phase 1 | See §3 — no primitive is installed this phase because there are no interactive components yet to need one. |
| IPC / protocol | `src/protocol/events.ts` — typed discriminated union mirroring `src/jarvis/observability/event_hub.py::SCHEMAS` 1:1 | Establishes the Jarvis UI Bridge boundary (§4). The transport is SSE via `services/sse-transport.ts`, token-protected on 127.0.0.1 (D028/D047); endpoint and token come from the daemon control command `ui`. Verified against the actual Python `SCHEMAS` dict, not invented. |
| Testing (unit) | none added yet | No component logic exists yet to unit-test. Vitest is the obvious choice when it's needed (co-locates with Vite config) — not installed speculatively. |
| Testing (visual QA) | Playwright (`@playwright/test`) | Mandatory per section 18. Verified working: `npx playwright test` boots `vite preview`, renders the app, asserts zero console errors, checks keyboard-reachable heading, and produces/verifies a deterministic screenshot at 1920×1080 and 2560×1440. |

## 2. Project structure

```text
ui/
  src/
    app/            # root component composition (App.tsx) — exists
    design-system/  # tokens, primitives once adopted — tokens.css exists
    protocol/        # typed Jarvis UI Bridge contracts — events.ts exists
    testing/        # Playwright specs — app-shell.spec.ts exists
    shell/          # desktop-shell lifecycle glue — not created yet, no content to put there until the shell ADR's chosen shell is wired up
    features/       # feature-oriented UI modules — not created yet, no features exist
    components/     # shared presentational components — not created yet, App.tsx is still the only component
    hooks/          # shared React hooks — not created yet
    state/          # app state (if/when a store is justified) — not created yet
    services/       # browser-side service wrappers (e.g. transport implementations of JarvisEventTransport; `sse-transport.ts` is the live SSE one)
    styles/         # non-token global CSS — folded into index.css for now; split out when it grows
```

Directories are **materialized on first real content**, not pre-created empty — an empty `services/` folder with nothing in it is boilerplate nobody asked for and git won't track it anyway. This table is the binding contract for where things go when they're written; a PR adding a `components/Foo.tsx` without this structure existing yet should create the directory as part of that change, not before.

Import boundary rule: `app/` may import from anything; `components/`, `features/`, `design-system/`, `protocol/` must not import from `app/` or `shell/` (no upward dependencies). `protocol/` must not import React — it is pure types plus the transport interface, so it stays usable from a non-React context (e.g. a future Node-side preload script) without pulling in the UI framework.

## 3. Component primitive evaluation (decision deferred, not skipped)

| Library | Accessibility | Styling freedom | Bundle | Desktop fit | Verdict |
| --- | --- | --- | --- | --- | --- |
| Base UI (Radix/Floating UI/MUI teams) | Strong, actively maintained | Fully unstyled | Small, tree-shakeable per-component | Good — dialogs, menus, popovers, tooltips all composable with a custom visual system | **Lead candidate for Phase 2**, not installed now |
| Radix UI (`radix-ui` unified package) | Strong, mature | Fully unstyled | Similar to Base UI | Same profile as Base UI, slightly larger legacy API surface | Acceptable fallback if a primitive Base UI lacks |
| React Aria Components (Adobe) | Very strong, most battle-tested | More opinionated slot/render-prop API | Heavier | Excellent a11y, but its opinionated styling hooks fight a from-scratch visual identity (section 10: "visual identity should not" come from a library) | Rejected for Phase 1+2 default; keep in mind for one specific primitive if Base UI/Radix prove insufficient (e.g. complex date/color pickers) |
| Material UI / Ant Design / Bootstrap | Strong | Low — imports a visual identity | N/A | Explicitly rejected by section 10 | Rejected |

No primitive library is installed in Phase 1: there is no interactive component (dialog, menu, popover) in the skeleton yet to need one, and installing one speculatively would violate the "every dependency must solve a concrete requirement" rule. This table exists so Phase 2's first interactive component doesn't re-litigate the comparison — it picks Base UI and moves.

## 4. Architectural boundaries (mandatory, section 12)

- **Jarvis Core** (`src/jarvis/`, Python): owns speech recognition, LLM orchestration, TTS, filesystem/terminal/Windows automation, agent orchestration, memory. The frontend never gets direct access to any of it.
- **Jarvis UI Bridge** (`ui/src/protocol/`): typed event contract only. `events.ts` mirrors `event_hub.py::SCHEMAS`; a future `commands.ts` will mirror whatever command surface Core exposes. No business logic here — types and a transport interface only.
- **Jarvis Desktop** (not yet created — depends on the shell ADR's chosen shell): owns window lifecycle, tray, autostart, native OS integration. See `docs/architecture/desktop-shell-adr.md`.
- **Jarvis Frontend** (`ui/src/app/`, `features/`, `components/`, `hooks/`, `state/`): React application. May subscribe to bridge events, render, request commands through the bridge, manage presentational state. May not read files, spawn processes, or call Windows APIs directly.
- **Jarvis Design System** (`ui/src/design-system/`): tokens and (later) primitives. No app logic.

## 5. Motion architecture (tokens only — section 17)

`tokens.css` defines `--motion-duration-*` and `--motion-ease-standard`, collapsed to `0ms` under `prefers-reduced-motion: reduce`. No animation is implemented this phase. When motion work starts: prefer CSS transitions/animations for anything compositor-friendly (`transform`, `opacity`); reach for `react-view-transitions` (Vercel, evaluated in `docs/engineering/frontend-skills.md`, currently REJECTED for this phase only) for shared-element/state transitions; do not add Framer Motion/Motion unless CSS transitions and View Transitions both prove insufficient for a specific interaction.

## 6. Windows UI targets (section 16)

Primary: 1920×1080, 2560×1440, 4K, high-DPI scaling, multi-monitor. Verified minimum: Playwright's `desktop-1080p` project is the floor; no viewport below 1920×1080 is treated as a target. Mobile breakpoints are not implemented — the app is resilient to smaller desktop windows via the existing flex layout, not via a mobile-first breakpoint system.

## 7. Performance budget (section 21, initial)

| Budget | Target | Verified now? |
| --- | --- | --- |
| JS bundle (initial) | < 250 KB gzipped for the shell | Current build: 68.67 KB gzip JS + 0.62 KB gzip CSS — verified via `npm run build` |
| React rerenders | No component rerenders on an unrelated state change | Not yet applicable — one static component exists |
| Idle CPU | Overlay must not poll; event-driven only via the bridge | Enforced by the `JarvisEventTransport` interface shape (subscribe, not poll) — the SSE transport is push-only (no polling; keepalive every 15 s) |
| Startup | Frontend paints before the desktop shell reports ready | Verified qualitatively in the Electron PoC (`ready-to-show` fires after `loadFile` resolves) |

These are starting numbers, refined once real feature weight (event log, telemetry panels) exists. **M008/S05 hardware gate (2026-09-30): not accepted.** The historical 68.67 KB gzip figure measures the Phase 1 shell, not today's command center. The current Vite build emits 33.10 KB main + 70.99 KB Button + 1.33 KB overlay gzip JS (105.42 KB combined emitted JS, below the 250 KB shell budget; not a network-transfer/initial-load measurement). The Windows bridge identified Windows 11 Home, i7-12700H, RTX 3070 Laptop GPU plus Iris Xe, and Edge 154.0.4258.37, but the actual daemon was absent on the configured control port. Therefore no Windows Edge DPR, renderer identity, frame p50/p95, idle CPU/memory, GPU utilization, or live Activate/Sleep result exists; do not substitute WSL Chromium or fixture data. The repeatable isolated Edge/CDP probe, raw-sample schema, safety limits, and recovery are documented in [Windows UI hardware acceptance](../performance/ui-windows-hardware.md). The existing event-driven idle design remains a design constraint, not a measured CPU pass.

## 8. UI server and shell

- **Same-origin serving.** The loopback event server (`jarvis.observability.event_stream`, port `daemon.ui_events_port`) serves the built `ui/dist` and the SSE stream (`/events`) from one origin, so the shell needs no CORS and no second port. Static paths are traversal-safe (escapes out of the dist dir return 404) and need no token because they carry no secrets; `/events` still requires the token.
- **Build must exist.** Run `npm --prefix ui run build` first. Without `ui/dist` the server logs that the UI is not served and `/` returns 404 (the stream still works).
- **`daemon.ui_dist_dir`.** Optional config string overriding the dist location; default is `<repo root>/ui/dist`. Only used if it is a directory.
- **Token flow.** `jarvis ui` asks the daemon (`ui` control command) for `app_url`, which is `http://127.0.0.1:<port>/#token=<token>`. The token rides in the URL fragment, so it is never sent to the server or logged. On load `ui/src/services/endpoint.ts` reads it, then clears the hash with `history.replaceState` so it leaves the address bar. With no token the shell renders "Offline" and opens no `EventSource`.
- **Launching.** `jarvis ui` opens an Edge `--app=<app_url>` window (`msedge` on PATH, else the standard Program Files paths) and falls back to the default browser. `jarvis ui --print` prints `app_url` instead (the only path that shows the token). If the daemon is down or `daemon.ui_events_port` is 0 the command exits 1 with a hint.
- **Keyboard / a11y contract.** `AppFrame` provides a "Skip to main content" link as the first tab stop (targets `<main id="main" tabIndex={-1}>`), `header`, `nav` (`aria-label="Workspaces"`) and `complementary` landmarks, a `role="status" aria-live="polite"` region announcing connection and assistant-state changes, and visible `:focus-visible` outlines. The connection state is shown as text, never colour alone. `ui/src/testing/live-shell.spec.ts` asserts these plus zero serious/critical axe violations against a live demo stream.

## 9. Jarvis orb

- **Model/renderer split.** `ui/src/components/jarvis/orb/orbModel.ts` is pure (no DOM or React): `foldOrbSignal` folds `voice.state` and `speech.started`/`speech.completed` into `{ state, speaking }`, `targetParams` maps that to `{ hue, energy, turbulence }`, and `stepParams` smooths toward the target. It is unit-tested with `node:test` (`ui/node-tests/orb-model.test.ts`). `JarvisOrb.tsx` only renders those params.
- **Renderer.** Raw WebGL2, one full-screen triangle, one fragment shader, no textures, no npm dependency. The orb reacts to events only; there is no audio analysis (D049). `AssistantStatus` uses it for the large core; the 22px compact indicator stays CSS.
- **Fallback.** If WebGL2 is unavailable, shader compile or link fails, or the context is lost, the orb logs one `console.warn` and renders `VisualCorePlaceholder` inside a `data-renderer="fallback"` wrapper.
- **Reduced motion and visibility.** Under `prefers-reduced-motion: reduce` no `requestAnimationFrame` loop runs; params snap to the target and one frame is drawn per signal change. The loop also stops while `document.hidden` and resumes (with dt reset) on return.
- **Test hooks.** The canvas exposes `data-renderer` (`webgl2` or `fallback`) and `data-orb-state` (the state, or `speaking` while speech is active). `ui/src/testing/orb.spec.ts` uses them against the live demo stream. Command-center screenshots mask the canvas because GPU output is not deterministic.
