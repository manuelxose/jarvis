# Jarvis Visual Directions (Phase 2)

Date: 2026-09-24. Method: three genuinely different static mockups (not mood boards) were built as standalone HTML/CSS, rendered at 1920×1080 via Playwright/Chromium, and visually inspected as images — not assumed from description.

Source files: `design-exploration/direction-{a,b,c}-*.html` (throwaway exploration artifacts, not part of the `ui/` build). Screenshots: `docs/design/screenshots/direction-{a,b,c}-*.png`.

## Direction A — Cinematic Command

Dark near-black (`#05070a`) with a radial cyan/violet glow behind a large circular "visual core," glassy panels with a glowing accent border, uppercase tracked headers, generous whitespace.

- **Strengths**: strongest distinctive identity (design objective A); the circular glowing core is an obvious, coherent visual-core placeholder; cinematic composition reads immediately as "advanced personal computing environment," not an admin dashboard.
- **Weaknesses**: lower information density — the log/metrics area is sparse relative to the Command Center's required seven workspaces; heavy glow risks visual fatigue over a long programming session (design objective B, section 21's "avoid excessive backdrop filters"); large typography wastes vertical space technical users will want for logs/tables.

## Direction B — Precision Workstation

Near-black graphite (`#0a0b0c`), monospace-first typography throughout, 3px radius, 1px hairline borders, teal/amber accents used only for status, terminal-style timestamped log for the conversation view.

- **Strengths**: excellent density and readability for long technical sessions; monospace numerics (CPU/RAM/GPU, timestamps) are genuinely easier to scan than proportional type; lowest visual noise; cheapest to render (no gradients/glow).
- **Weaknesses**: reads as a generic terminal/log-viewer, not a distinctive product identity — fails design objective A on its own; the compact circular "core" indicator is too small to serve as a credible placeholder for the future holographic orb.

## Direction C — Adaptive Operating Environment

Graphite with a subtle diagonal gradient (`#0e1013` → `#12151a`), rounded modular cards (14px radius), a blob-shaped visual-core placeholder, state-driven accent color (cyan=listening, violet=speaking, amber=processing, red=error), progressive-disclosure card layout.

- **Strengths**: best balance across all four design objectives — distinctive without being overwhelming, dense enough for daily use, the modular card grid maps directly onto the seven required Command Center workspaces (§6) without redesign, and the state-driven accent gives a free, non-decorative way to communicate assistant state (useful for the overlay's nine required states).
- **Weaknesses**: on its own, the core placeholder (a flat gradient blob) is less cinematic than A's glowing ring; metrics/logs in proportional type are slightly harder to scan than B's monospace.

## Evaluation

| Criterion | A Cinematic | B Precision | C Adaptive |
| --- | --- | --- | --- |
| Visual identity | Strong | Weak | Strong |
| Readability (long session) | Medium | Strong | Strong |
| Usability / density | Weak | Strong | Strong |
| Technical feasibility (CSS only, no new deps) | Yes | Yes | Yes |
| Consistency across Overlay + Command Center | Medium (core doesn't scale down well) | Medium (log view doesn't compress to a compact overlay well) | Strong (blob + card scale down cleanly to the overlay's compact pill) |
| Component reuse potential | Medium | Strong | Strong |
| Accessibility (contrast, non-color state) | Needs work (glow reduces contrast margins) | Strong | Strong (state uses color + label + icon shape, not color alone) |
| Windows desktop suitability | Medium (glow costs paint) | Strong | Strong |
| Future extensibility (orb slot, 3D later) | Strong (already circular/radial) | Weak (core too minor) | Strong (blob shape is an explicit low-fidelity stand-in, swappable for a WebGL canvas later without a layout change) |

## Selected direction: Adaptive Command (C base, A core treatment, B data typography)

Not a merge of all three — three specific, justified borrowings:

1. **Base layout and state system: Direction C.** Modular rounded cards map directly onto the seven Command Center workspaces (§6); the adaptive per-state accent (cyan/violet/amber/red) becomes the actual `--state-*` design tokens and satisfies the nine required overlay/assistant states without inventing a second color system.
2. **Visual-core treatment: Direction A's glow, applied to C's blob shape.** The reserved visual-core placeholder needs to read as "this is where the real holographic core will render," which A's radial glow communicates far better than C's flat blob alone. C's blob shape is kept (it scales down cleanly into the overlay's compact pill); A's glow is applied on top, capped to the core element only — not the whole chrome — so it doesn't reintroduce A's fatigue/contrast risk.
3. **Data typography: Direction B's monospace discipline.** All numeric/timestamp/log/status-label text (metrics, agent rows, development panel, conversation timestamps) uses `--font-mono` with tabular numerals. Prose (conversation message bodies, panel descriptions) stays in `--font-sans`. This is the one rule carried from B wholesale — it directly serves design objective B ("long programming sessions... readability").

This is implemented in `ui/src/design-system/tokens.css` (§ below) and is the single design authority for both the Command Center and Overlay prototypes — no divergence between them.

Rejected wholesale: A's large-scale glow-everywhere chrome (fatigue risk, §15 performance budget), B's flat terminal identity (fails distinctiveness objective), a naive merge of all three card systems (would violate "do not merge every idea into one overcrowded interface").
