# ADR: Windows desktop shell — Tauri 2 vs Electron

Date: 2026-09-24. Status: **decided with an open environmental blocker** (not a paper decision — both options were checked against this environment, one was proof-of-concept'd, the other is currently un-buildable here).

## Context

Jarvis needs a Windows desktop shell that supports a transparent, borderless, always-on-top overlay today, and a full-screen Command Center later, without blocking the future audio-reactive/3D/gesture work (out of scope this phase, section 13). Section 11 requires evaluating Tauri 2 and Electron with evidence, not internet claims about memory usage.

## Environment facts (verified this session)

- Node `v24.13.0`, npm `11.6.2` — present, working.
- `cargo`/`rustc` — **absent** (`command not found`). Installing a system Rust toolchain is a meaningful environment change (new system-wide toolchain, disk, and time) and was not done unilaterally in this phase.
- `electron@44.4.5` installs via npm alone (283 MB binary, ~1 minute over this network) — no additional system toolchain required.

## What was actually tested

A minimal Electron PoC (`desktop-shell-poc/main.js`, scratch dir, not the production shell) was built and run:

```
LIFECYCLE: loadFile resolved
LIFECYCLE: ready-to-show
LIFECYCLE: shown, frame=false transparent=true alwaysOnTop=true
LIFECYCLE: closing
LIFECYCLE: closed
```

This proves, on this machine, right now: a `frame:false, transparent:true, alwaysOnTop:true` `BrowserWindow` loads the built Vite output (`ui/dist/index.html`) and goes through a clean create → show → close → quit lifecycle with exit code 0. It does **not** prove Windows-specific behavior (DPI scaling, Alt+Tab, tray, multi-monitor) — that requires a Windows host, which this WSL session is not.

Tauri 2 was **not** PoC'd — `cargo` is absent, so `cargo tauri build`/`dev` cannot run here. This is a real, reproducible BLOCKED state, not a preference.

## Decision

**Target Tauri 2 for the production shell. Electron is the interim/fallback shell for Phase 1+2 development until a Rust toolchain is installed.**

Rationale for targeting Tauri long-term:
- Smaller binary/memory footprint (Rust backend, system webview vs. bundled Chromium) — this matters per section 21: Jarvis must coexist with VS Code, browsers, terminals, and other dev workloads without contributing a second full Chromium process at idle.
- Rust-side IPC boundary gives a real process-isolation story for section 12's "IPC security" requirement — commands the UI can invoke are explicit Rust functions, not an arbitrary Node `require` surface.
- Tauri 2's window API supports everything section 11 asks for (transparent, borderless, always-on-top, click-through, multi-window) at least as well as Electron's.

Rationale for not blocking on it:
- The Rust toolchain is genuinely missing in the current dev environment and installing it silently is out of scope for an agent-driven phase (it's a several-hundred-MB, minutes-long, system-level change). It needs to be an explicit, confirmed step.
- Electron is fully buildable today with only `npm install`, and the PoC above proves the exact window behaviors (transparent/borderless/always-on-top) this phase needs to validate, so Phase 2 frontend work is not blocked on the shell decision.

## Consequences

- Do not build the "production overlay" against either shell yet (section 11: "Do not yet implement the production overlay" — still true).
- Before Phase 2 desktop-shell work starts, install Rust (`rustup`) and run an equivalent Tauri PoC (`cargo create-tauri-app`, load the same `ui/dist`, verify the same lifecycle + transparent/borderless/always-on-top behaviors) to confirm parity before committing.
- If Tauri's Windows-specific behavior (DPI, tray, Alt+Tab) turns out to have gaps Electron doesn't, that would be grounds to revisit — this decision is `revisable`.
- The `ui/` frontend must stay shell-agnostic: nothing in `ui/src/` should import an Electron- or Tauri-specific API directly. All shell interaction goes through the `shell/` boundary (currently empty — created when a shell is wired up) and the `protocol/` bridge, so swapping shells later is a `shell/` + build-config change, not a rewrite.
