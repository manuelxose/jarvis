# Future capabilities — explicitly NOT implemented in Phase 1

Date: 2026-09-24.

This document exists so nobody mistakes architectural room-to-grow for a promise of partial implementation. **None of the following existed in the codebase after Phase 1, except the raw-WebGL2 orb added later in M008/S04 (see the rows below).** No Three.js, React Three Fiber, WebGL scene, particle system, webcam hand-tracking, gesture-recognition, spatial UI, holographic or 3D object manipulation, or Windows-window gesture control was installed, scaffolded, or stubbed this phase.

## What Phase 1 did to avoid blocking these later

| Future capability | How the Phase 1 architecture avoids blocking it | What is deliberately NOT done yet |
| --- | --- | --- |
| Audio-reactive visualizations | `protocol/events.ts` already types `voice.state`/`speech.started`/`system.metrics` events from the real Python `EventHub` schema — a future visualization subscribes to the same bridge everything else uses. | **Partly built (M008/S04):** a raw-WebGL2 orb (`ui/src/components/jarvis/orb/`) reacts to `voice.state` and `speech.started`/`speech.completed` only. Still no audio analysis (no microphone/output amplitude or spectrum, D049). |
| Holographic orb | `design-system/` and `components/` boundaries mean a future orb component is just another consumer of tokens + the event bridge, not a rewrite of the shell. | **Built as a flat shader orb, not a hologram:** `JarvisOrb` is a single full-screen-triangle fragment shader with a CSS fallback. No volumetric or holographic rendering, no post-processing. |
| Richer motion | Motion tokens (`--motion-duration-*`, `--motion-ease-standard`) exist and already respect `prefers-reduced-motion`; `react-view-transitions` was evaluated and documented as the likely tool. | No transitions implemented; the skill is not installed. |
| GPU-rendered elements / WebGL / 3D scenes | The `components/` boundary means a WebGL canvas would be one more component behind the same import rules (no upward dependency into `app/` or `shell/`) — isolated, not entangled with the rest of the UI. | **Raw WebGL2 exists** for the orb only (`ui/src/components/jarvis/orb/JarvisOrb.tsx`, hand-written GLSL, no library). Still zero WebGL/3D npm dependencies: `grep -i "three\|webgl\|r3f"` against `ui/package.json` returns nothing. Three.js, React Three Fiber, 3D scenes and particle systems are not built. |
| Spatial panels / multi-monitor spatial interaction | `shell/` is reserved specifically for desktop-shell-lifecycle concerns (window placement, multi-window), separate from `app/` — a spatial-layout feature extends `shell/`, not the render tree. | `shell/` directory does not exist yet — created when a shell is wired up (see desktop-shell ADR). |
| Gesture input / hand tracking | Section 12's "input abstraction" principle means gesture input would arrive through the same event-driven bridge as keyboard/mouse, not a bespoke path. No input abstraction layer exists yet because there is only one input mode (keyboard/mouse) to abstract over. | No webcam access, no hand-tracking dependency, no gesture recognizer. |

## Explicit non-goals for Phase 1 (repeated from the brief, section 22)

Not built: final dashboard, final overlay, final Command Center, final Jarvis orb, 3D scenes, gesture implementation, elaborate animations, production visual design.

## When to revisit this document

Update it the moment any row above gets a real implementation — at that point the row should move out of this file and into `docs/architecture/frontend-architecture.md` as a decided, built capability, not stay here as "future."
