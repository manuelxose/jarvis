# Phase 3 report: Windows desktop shell and native overlay (M010)

Date: 2026-10-14. Source of truth for every verdict below: [phase-3-acceptance.json](phase-3-acceptance.json), checked by `desktop/scripts/acceptance-ledger.test.cjs` and `desktop/scripts/phase-3-report.test.cjs`.

## Summary

Phase 3 delivers an Electron desktop shell for Jarvis on Windows: a Command Center window (windowed, maximized, fullscreen), a transparent click-through overlay, a mode manager, persisted window state, a Core bridge, a tray, global shortcuts and an opt-in launch-at-sign-in.

Result: **not a clean pass.** Criteria: 10 PASS, 1 FAIL, 2 BLOCKED. Scenarios: 12 PASS, 1 FAIL, 2 BLOCKED. Native visual parity with the Phase 2 references **FAILS** on all 6 comparable captures and the other 26 references are BLOCKED; a same-platform Windows baseline comparison PASSes 6/6 but proves only Windows render stability, so C11 and S14 stay FAIL. The Phase 2 Playwright suite is now green (92/92 after the S10 assertion fix). Nothing BLOCKED or FAIL has been promoted.

## Brief mapping

The Phase 3 brief (§21 criteria and its 15 acceptance scenarios) was **not found** in the repository. Criteria C01-C13 and scenarios S01-S15 are **reconstructed** from the M010 roadmap success criteria and slice demos, so their ids and wording are not the brief's. They need re-mapping if the brief is recovered. Scenario ids S01-S15 here are ledger ids, not slice numbers.

## Architecture recap

- Decision D041 (MEM132): ship on Electron; no Rust/Tauri toolchain. See [architecture/desktop-shell-adr.md](architecture/desktop-shell-adr.md) and [architecture/windows-desktop.md](architecture/windows-desktop.md).
- Three parts are kept separate: the **Desktop Host** (`desktop/src`, Electron main process: windows, tray, shortcuts, persistence, Core bridge), **Jarvis Core** (the Python daemon, unchanged, reached over loopback `/events` SSE and `/control`), and the **Frontend** (`ui/`, Phase 2 design system, loaded from `ui/dist`).
- Window modes and transitions: [architecture/window-state-machine.md](architecture/window-state-machine.md).
- The renderer has context isolation, no Node, a sandbox, a narrow preload API and an authenticated IPC surface: [desktop-security-review.md](desktop-security-review.md). The Core token stays in the main process: [desktop-core-bridge.md](desktop-core-bridge.md).

## Verdict table: criteria

| Id | Status | Criterion | Evidence |
|---|---|---|---|
| C01 | PASS | Native Electron shell launches on the real Windows host | [desktop-qa-evidence.json#shell-launch](desktop-qa-evidence.json) |
| C02 | BLOCKED | Overlay is transparent and click-through to windows beneath | [desktop-qa-evidence.json#overlay-transparent-click-through](desktop-qa-evidence.json)<br>[desktop-overlay.md](desktop-overlay.md) |
| C03 | PASS | Command Center windowed/maximized/fullscreen modes work on the host monitors | [desktop-qa-evidence.json#command-center-modes](desktop-qa-evidence.json)<br>[desktop-qa-evidence.json#fullscreen-escape](desktop-qa-evidence.json) |
| C04 | PASS | Mode manager never leaves an invisible blocking window | [desktop-qa-evidence.json#command-center-modes](desktop-qa-evidence.json)<br>[desktop-command-center.md](desktop-command-center.md) |
| C05 | BLOCKED | Window state persists and restores across monitor/DPI changes | [desktop-qa-evidence.json#window-state-restore-multi-monitor](desktop-qa-evidence.json)<br>[desktop-qa-evidence.json#dpi-scaling-topology](desktop-qa-evidence.json) |
| C06 | PASS | Core connection states incl. auth-failure and incompatible-version, no mock connection | [desktop-qa-evidence.json#core-attach-restart](desktop-qa-evidence.json)<br>[desktop-core-bridge.md](desktop-core-bridge.md)<br>[desktop-qa-automated.md](desktop-qa-automated.md) |
| C07 | PASS | Tray, global shortcuts and launch-at-sign-in (default off) | [desktop-qa-evidence.json#tray-shortcuts-login](desktop-qa-evidence.json)<br>[desktop-tray-evidence.json#tray-created](desktop-tray-evidence.json)<br>[desktop-tray-evidence.json#shortcut-registered](desktop-tray-evidence.json)<br>(+2 more in ledger) |
| C08 | PASS | Closing the UI never kills Core | [desktop-qa-evidence.json#core-attach-restart](desktop-qa-evidence.json) |
| C09 | PASS | Security: context isolation, IPC auth, CSP, no shell/filesystem exposure | [desktop-security-review.md](desktop-security-review.md)<br>[desktop-qa-automated.md](desktop-qa-automated.md) |
| C10 | PASS | Typecheck, lint and unit/contract tests green | [desktop-qa-automated.md](desktop-qa-automated.md) |
| C11 | FAIL | Native visual parity with Phase 2 references | [desktop-visual-verification.md](desktop-visual-verification.md)<br>[desktop-visual-evidence.json#command-center-overview-1920x1080](desktop-visual-evidence.json)<br>[desktop-visual-evidence.json#command-center-overview-2560x1440](desktop-visual-evidence.json)<br>(+10 more in ledger) |
| C12 | PASS | Clap/welcome/TTS voice path untouched and tests green | [desktop-qa-automated.md](desktop-qa-automated.md) |
| C13 | PASS | No orphan processes after harness runs | [desktop-qa-evidence.json#no-orphan-processes](desktop-qa-evidence.json) |

## Verdict table: scenarios

| Id | Status | Scenario | Evidence |
|---|---|---|---|
| S01 | PASS | Launch Command Center on the host and quit cleanly | [desktop-qa-evidence.json#shell-launch](desktop-qa-evidence.json) |
| S02 | PASS | Launch overlay on the host and quit cleanly | [desktop-qa-evidence.json#shell-launch](desktop-qa-evidence.json) |
| S03 | BLOCKED | Click through overlay transparent margin into Explorer/VS Code/terminal | [desktop-qa-evidence.json#overlay-transparent-click-through](desktop-qa-evidence.json) |
| S04 | PASS | Command Center windowed, maximized and fullscreen modes | [desktop-qa-evidence.json#command-center-modes](desktop-qa-evidence.json) |
| S05 | PASS | Escape leaves fullscreen | [desktop-qa-evidence.json#fullscreen-escape](desktop-qa-evidence.json) |
| S06 | PASS | Mode switching leaves no invisible blocking window | [desktop-qa-evidence.json#command-center-modes](desktop-qa-evidence.json) |
| S07 | BLOCKED | Restore window state after restart and monitor unplug | [desktop-qa-evidence.json#window-state-restore-multi-monitor](desktop-qa-evidence.json) |
| S08 | PASS | Mixed-DPI three-monitor topology handled | [desktop-qa-evidence.json#dpi-scaling-topology](desktop-qa-evidence.json) |
| S09 | PASS | Core attach, restart and token rotation | [desktop-qa-evidence.json#core-attach-restart](desktop-qa-evidence.json) |
| S10 | PASS | Auth-failure and incompatible-version Core states (automated, no mock connection) | [desktop-core-bridge.md](desktop-core-bridge.md)<br>[desktop-qa-automated.md](desktop-qa-automated.md) |
| S11 | PASS | Tray created, shortcut registered, collision reported, login item round trip | [desktop-tray-evidence.json#tray-created](desktop-tray-evidence.json)<br>[desktop-tray-evidence.json#shortcut-registered](desktop-tray-evidence.json)<br>[desktop-tray-evidence.json#shortcut-collision-reported](desktop-tray-evidence.json)<br>(+1 more in ledger) |
| S12 | PASS | Real keystroke injection toggles the overlay | [desktop-tray-evidence.json#shortcut-injection-toggles-overlay](desktop-tray-evidence.json) |
| S13 | PASS | Exiting the desktop leaves Core running (tray harness) | [desktop-tray-evidence.json#exit-desktop-leaves-core](desktop-tray-evidence.json) |
| S14 | FAIL | Screenshots match Phase 2 references at 1080p and 1440p | [desktop-visual-evidence.json#command-center-overview-1920x1080](desktop-visual-evidence.json)<br>[desktop-visual-evidence.json#command-center-overview-2560x1440](desktop-visual-evidence.json)<br>[desktop-visual-evidence.json#overlay-compact-idle-1920x1080](desktop-visual-evidence.json)<br>(+10 more in ledger) |
| S15 | PASS | No orphan Electron processes after the final run | [desktop-qa-evidence.json#no-orphan-processes](desktop-qa-evidence.json) |

## Automated test totals (final run, 2026-10-14)

Full commands and exit codes: [desktop-qa-automated.md](desktop-qa-automated.md).

| Layer | Result |
|---|---|
| `npm --prefix desktop test` | 233 pass / 0 fail (S10 T04 run) |
| `npm --prefix desktop run test:all-native` | 207 pass / 0 fail (includes the report, visual-evidence and S01 evidence-integrity contracts; S10 T04 run) |
| `npm --prefix ui exec playwright test` (Phase 2 suite) | 92 pass / 0 fail (S10 T01 fixed the stale `Offline` assertion in `controls.spec.ts`; re-run in T04) |
| S01 evidence-integrity re-run (S10 T02) | all five S01 Windows probes exit 0; their fresh outputs are linked from [architecture/windows-desktop.md](architecture/windows-desktop.md) and checked by `desktop/scripts/windows-desktop-evidence.test.cjs` |
| `npm --prefix ui run build` | built |
| `npm --prefix ui run lint` | 0 errors, 2 warnings |
| `npm --prefix ui run test:transport` | 36 pass / 0 fail |
| Python contract and untouched-voice tests | 128 pass, 33 subtests |
| Live matrix `qa:matrix` | 7 PASS, 2 BLOCKED, 0 FAIL; tray re-run with a Core on 47811 and re-scored ([desktop-windows-qa-matrix.md](desktop-windows-qa-matrix.md)) |

One intermediate matrix run reported a transient window-state FAIL (`window-state-exited-before-probe`) that did not reproduce standalone or on rerun. It is disclosed, not hidden.

## Visual verification summary

Details: [desktop-visual-verification.md](desktop-visual-verification.md). Data: [desktop-visual-evidence.json](desktop-visual-evidence.json) (Phase 2 Linux references) and [desktop-visual-evidence-win32.json](desktop-visual-evidence-win32.json) (Windows baselines). Thresholds are fixed (per-channel 51, diff ratio at most 0.01) and were not changed after seeing results.

- Phase 2 comparison, re-run in S10 (identical numbers): 6 FAIL. Overview ratios 0.027 (1080p) and 0.015 (1440p); overlay idle about 0.98 and 0.99; overlay expanded 0.93 and 0.96. Diagnosis: overlay differences are almost entirely alpha (97-99% of idle pixels are fully transparent in the native capture against an opaque reference); RGB-only overlay idle ratios are 0.005 and 0.003, and Overview differences are all RGB (content and rasterization). Not a pass.
- Windows baseline comparison (`--baseline win32`): 6 PASS at ratio 0 in each of two completed runs; two further attempts failed on host/share errors before completing and are disclosed in the verification doc. This shows the Windows render is stable run to run. It is **not** Phase 2 cross-platform pixel parity, so C11 and S14 remain FAIL.
- 26 BLOCKED: non-Overview workspaces cannot be selected, and the non-idle overlay states cannot be reached, through the CLI capture contract without renderer injection or a live Core.
- Phase 2 Playwright suite: 92/92 passed, including all 32 screenshot assertions.

## BLOCKED and FAIL items

| Id | Status | Item | Reason |
|---|---|---|---|
| C02 | BLOCKED | Overlay is transparent and click-through to windows beneath | Edge recipient passes; Explorer, VS Code and terminal recipients are owner-accepted gaps (explorer-launch-exited-before-window, focus-reset-not-observed). |
| C05 | BLOCKED | Window state persists and restores across monitor/DPI changes | Automated restore rows pass; the physical-display-change row needs the owner to unplug a monitor. |
| C11 | FAIL | Native visual parity with Phase 2 references | Phase 2 cross-platform comparison unchanged: all 6 comparable captures exceed the fixed 0.01 diff ratio (overlay mismatch is mostly alpha=0 transparent native background vs opaque reference; Overview 0.027/0.015 is content and rasterization difference); 26 references BLOCKED. Same-platform win32 baseline comparison PASSes 6/6 at ratio 0 but only proves Windows render stability, not Phase 2 parity, so the criterion stays FAIL. |
| S03 | BLOCKED | Click through overlay transparent margin into Explorer/VS Code/terminal | Edge recipient passes; other recipients are owner-accepted environmental gaps. |
| S07 | BLOCKED | Restore window state after restart and monitor unplug | requires-owner-physical-display-change |
| S14 | FAIL | Screenshots match Phase 2 references at 1080p and 1440p | 6 FAIL against Phase 2 Linux references (ratio above 0.01), 26 BLOCKED. The win32-baseline PASS (6/6, ratio 0, run to run) compares Windows to Windows and does not satisfy 'match Phase 2 references'. |

What would unblock them: an owner-performed physical monitor unplug; Explorer, VS Code and terminal windows as click-through recipients; a way to select workspaces and simulate states in the native capture without renderer injection (for example a test-only, CLI-gated hook); and a decision on whether the Linux references or the Windows renderer is authoritative for visual parity.

## Owner decisions (2026-10-14)

The owner reviewed the remaining non-PASS items and accepted them as known Phase 3 limitations. Their ledger status is unchanged, so they are not promoted to PASS:

- C02/S03: Explorer, VS Code and terminal click-through recipients stay owner-accepted gaps (first accepted 2026-10-13). The overlay and the Edge recipient pass.
- C05/S07: the physical monitor-unplug restore is accepted untested. The five automated restore rows pass on the real 3-monitor, mixed-DPI host.
- C11/S14: Phase 2 Linux references are not comparable across platforms (transparent alpha=0 overlay versus opaque reference, rasterization differences). The win32 baselines in `desktop/visual-baselines/` (6/6 at ratio 0) are the Windows visual reference from now on.

## Residual security risks

From [desktop-security-review.md](desktop-security-review.md):

- CSP `style-src 'unsafe-inline'` (required by the React/Vite inline styles); scripts stay `'self'` only.
- CSP `connect-src` allows loopback with wildcard ports (`ws://127.0.0.1:*`, `http://127.0.0.1:*`).
- The build is unsigned and Electron fuses are not flipped.

## Known limitations

- Acceptance criteria are reconstructed, not the brief's.
- Evidence was gathered on one Windows 11 host with three monitors reporting scale 1; non-100% DPI was not exercised physically.
- Fixture Core data in Phase 2 screens is prototype data; production connectivity is covered by the bridge tests and the attach/restart matrix row only.
- Keystroke injection and real "Stop Jarvis Core" were never exercised against a real Core.
- The Electron runtime is cached on local NTFS because Chromium cannot sandbox from a UNC path.

## Recommended follow-ups

1. Recover the Phase 3 brief and re-map the ledger ids.
2. Decide the visual reference platform: the overlay mismatch is a transparent-window vs opaque-reference difference (diagnosed), and Overview differs in content (offline vs listening) and fonts.
3. (Done in S10) the `controls.spec.ts:121` expectation now matches the typed disconnected label.
4. Re-run the tray and matrix harnesses with a live Core and unrestricted PowerShell.
5. Owner-run the monitor-unplug and click-through recipient checks.
6. Sign the build and flip Electron fuses before distribution.

Operator runbook: [desktop-handoff.md](desktop-handoff.md).
