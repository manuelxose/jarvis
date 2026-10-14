# Native desktop visual verification

Run date: **2026-10-14**. Phase 2 comparison generated at **2026-10-14T15:59:39.657Z** (S09) and re-measured in S10 T04 with identical ratios. Windows baseline comparison added in S10.

**Native verdict against Phase 2 references: FAIL — 32 references: 0 PASS, 6 FAIL, 26 BLOCKED.** Same-platform Windows baseline comparison: 6 PASS (ratio 0), 26 BLOCKED, proving render stability only (see below).
The machine-readable source is [desktop-visual-evidence.json](desktop-visual-evidence.json), copied byte-for-byte from the final harness report. A passing evidence contract test validates truthful reporting, not visual parity.

## Method and artifacts

- Build: `npm --prefix ui run build` (TypeScript and Vite, exit 0).
- Capture: `npm --prefix desktop run visual:native` from WSL, launching the active worktree through `powershell.exe`. CLI-only main-process probe; no new preload keys, IPC, renderer-state injection, or UI redesign.
- References: all PNGs in `ui/src/testing/command-center.spec.ts-snapshots/` and `ui/src/testing/overlay.spec.ts-snapshots/`. These are Phase 2 Linux Chromium images at 1920×1080 and 2560×1440. There is no 4K reference in this inventory.
- Reference filename mapping for every table row: `<host>-<state>-desktop-<resolution>-linux.png`, where resolution is `1080p` for 1920×1080 and `1440p` for 2560×1440, in the corresponding host snapshot directory above. Each exact repository-relative reference is also recorded in JSON.
- Content size must exactly match the reference; framed Command Center sizing accounts for non-client borders when choosing a monitor. No crop or resize. `webContents.capturePage()` follows loaded fonts, two animation frames, reduced motion, renderer mount/mode checks, and zoom 1. Device scale factor is forced to **1**.
- Frozen constants: **PER_CHANNEL_TOLERANCE = 51**, **MAX_DIFF_RATIO = 0.01**. The tolerance is the normalized channel-space analogue of Playwright's default 0.2 (`0.2 × 255 = 51`), **not mathematical equivalence to Playwright's perceptual YIQ metric**. A pixel differs if any BGRA channel, including alpha, has delta >51; ratio = differing pixels / total pixels. PASS requires ratio ≤0.01. Constants were fixed before native execution and were not adjusted after results.
- References decode with Electron `nativeImage`; diff masks retain the full image size and mark differing pixels red. Size mismatch is never silently cropped. Transparent native captures are not composited onto a synthetic reference background.
- Run directory: **`desktop-native-visual-X9s2t1/`** (git-ignored). It contains `report.json`, `stdout.log`, `stderr.log`, `owned.json` and `state/` captures/diffs. Captures are `capture-command-center-<WxH>.png`, `capture-overlay-<WxH>.png` or `capture-overlay-expanded-<WxH>.png`. Diffs are `diff-<scenario-id>.png`. JSON contains repository-relative artifact pointers (top-level separators normalized to `/`; raw host records preserve Windows separators). PNGs/logs remain local run artifacts, not checkout test dependencies.

## Host facts

Real host: **Microsoft Windows 11 Home**, version **10.0.26200**, build **26200**; PowerShell **ConstrainedLanguage**. Electron runtime **44.4.5**. The controller runs in WSL; Electron reports `platform: win32` and owns its PID tree.

| Display id | Bounds x,y | Size | Work area | Reported scale |
| --- | --- | --- | --- | --- |
| 2380241315 | -1920,1173 | 1920×1080 | 1920×1032 | 1 |
| 922014266 | 3840,0 | 1920×1080 | 1920×1032 | 1 |
| 3361103286 | 0,0 | 3840×2160 | 3840×2088 | 1 |

All six reachable captures fit at exact reference dimensions. Final Electron exit **0**; host error **null**; owned-process remaining list **empty**; cleanup errors **empty**; independent recovery remaining/errors lists **empty**. The harness command exit **1** is the truthful aggregate FAIL, not a launch or cleanup failure.

## Per-reference results

Reason codes: **D** = measured difference exceeds fixed 0.01 maximum; **W** = non-Overview workspace not selectable by this CLI capture contract without forbidden renderer injection; **S** = browser simulation state unavailable through native mode controls without renderer injection or live Core/state integration. A dash means no capture/ratio, not zero difference.

| Scenario id (host/state/reference size) | Status | Ratio | Reason |
| --- | --- | ---: | --- |
| command-center-overview-1920x1080 | FAIL | 0.026751543209876542 | D |
| command-center-overview-2560x1440 | FAIL | 0.01505126953125 | D |
| command-center-conversation-1920x1080 | BLOCKED | — | W |
| command-center-conversation-2560x1440 | BLOCKED | — | W |
| command-center-memory-1920x1080 | BLOCKED | — | W |
| command-center-memory-2560x1440 | BLOCKED | — | W |
| command-center-system-1920x1080 | BLOCKED | — | W |
| command-center-system-2560x1440 | BLOCKED | — | W |
| command-center-agents-1920x1080 | BLOCKED | — | W |
| command-center-agents-2560x1440 | BLOCKED | — | W |
| command-center-development-1920x1080 | BLOCKED | — | W |
| command-center-development-2560x1440 | BLOCKED | — | W |
| command-center-media-1920x1080 | BLOCKED | — | W |
| command-center-media-2560x1440 | BLOCKED | — | W |
| command-center-settings-1920x1080 | BLOCKED | — | W |
| command-center-settings-2560x1440 | BLOCKED | — | W |
| overlay-compact-idle-1920x1080 | FAIL | 0.9795428240740741 | D |
| overlay-compact-idle-2560x1440 | FAIL | 0.9884944661458334 | D |
| overlay-listening-1920x1080 | BLOCKED | — | S |
| overlay-listening-2560x1440 | BLOCKED | — | S |
| overlay-speaking-1920x1080 | BLOCKED | — | S |
| overlay-speaking-2560x1440 | BLOCKED | — | S |
| overlay-processing-1920x1080 | BLOCKED | — | S |
| overlay-processing-2560x1440 | BLOCKED | — | S |
| overlay-expanded-1920x1080 | FAIL | 0.931412037037037 | D |
| overlay-expanded-2560x1440 | FAIL | 0.9614236111111111 | D |
| overlay-notification-1920x1080 | BLOCKED | — | S |
| overlay-notification-2560x1440 | BLOCKED | — | S |
| overlay-error-1920x1080 | BLOCKED | — | S |
| overlay-error-2560x1440 | BLOCKED | — | S |
| overlay-disconnected-1920x1080 | BLOCKED | — | S |
| overlay-disconnected-2560x1440 | BLOCKED | — | S |

### Findings and visual sanity check

Representative native 1080p captures were inspected alongside their unchanged Linux references. The Command Center preserves the dark panels, cyan selection marker, violet conversation surface and overall three-column structure. It does **not** prove pixel parity: the reference Overview shows a listening orb, while the native capture shows offline; native connection/actions/mode affordances differ, fonts render differently, and the right rail shows a horizontal scrollbar.

The native compact overlay shows a top-left prototype panel with Expand/Open center controls on a transparent surface. The browser reference has an opaque dark viewport, a top-right idle badge, and bottom-left simulation controls. This is a state/layout/background/alpha mismatch, not merely a cross-platform rasterization finding. Cross-OS font rasterization can contribute to the Command Center differences, but this run does not isolate its contribution. Every measured result above remains **FAIL**.

Diff artifacts for the six failures (relative to the repository root):

- `desktop-native-visual-X9s2t1/state/diff-command-center-overview-1920x1080.png`
- `desktop-native-visual-X9s2t1/state/diff-command-center-overview-2560x1440.png`
- `desktop-native-visual-X9s2t1/state/diff-overlay-compact-idle-1920x1080.png`
- `desktop-native-visual-X9s2t1/state/diff-overlay-compact-idle-2560x1440.png`
- `desktop-native-visual-X9s2t1/state/diff-overlay-expanded-1920x1080.png`
- `desktop-native-visual-X9s2t1/state/diff-overlay-expanded-2560x1440.png`

### BLOCKED states and unblocking

**14 W references:** Conversation, Memory, System, Agents, Development, Media and Settings, at both sizes. A permitted real UI navigation capture workflow would be needed; this CLI accepts only host/size. **12 S references:** listening, speaking, processing, notification, error and disconnected overlays at both sizes. These need reachable live Core/state integration or permitted real UI actions with equivalent state. No mock native connection or renderer injection was introduced. This is a capture-contract/state limitation, not missing Windows bridge access or monitor capacity. An unavailable state is never promoted to PASS.

## Diagnosis (S10 T04)

Diagnostics only; verdicts above still use the frozen metric. Measured with a Node `node:zlib` PNG decode (no new dependency) on the S09 captures against their Phase 2 references, tolerance 51 per channel:

| Capture | Frozen ratio | RGB-only ratio | Pixels differing only in alpha | Capture pixels with alpha=0 |
| --- | ---: | ---: | ---: | ---: |
| overview 1920x1080 | 0.0268 | 0.0268 | 0 | 0 |
| overview 2560x1440 | 0.0151 | 0.0151 | 0 | 0 |
| overlay idle 1920x1080 | 0.9795 | 0.0046 | 0.975 | 0.9751 |
| overlay idle 2560x1440 | 0.9885 | 0.0026 | 0.9859 | 0.9860 |
| overlay expanded 1920x1080 | 0.9314 | 0.0337 | 0.8977 | 0.9056 |
| overlay expanded 2560x1440 | 0.9614 | 0.0190 | 0.9425 | 0.9469 |

Conclusion: the overlay ~0.98 ratio is the native overlay's transparent background (alpha 0 over about 91-99% of the viewport) compared with an opaque reference viewport, not rasterization. Ignoring alpha, overlay idle differs on under 0.5% of pixels. Overview differences are RGB-only (about 1.5-2.7%), consistent with content differences (offline state versus the listening reference, native controls, a horizontal scrollbar) plus font rasterization; this analysis does not separate those two. No metric or threshold was changed in response.

## Windows baselines (S10)

Method: `npm --prefix desktop run visual:native -- --baseline win32` compares fresh native captures with images captured by the same Windows host and Electron runtime (44.4.5, scale 1, same display layout). Frozen constants are unchanged.

Provenance: run A (`desktop-native-visual-no2QYU`, Phase 2 reference set, host exit 0, remaining and cleanup errors empty, identical ratios to S09) was blessed with `npm --prefix desktop run visual:baselines -- --from desktop-native-visual-no2QYU` into `desktop/visual-baselines/` (6 PNGs plus `manifest.json` with sha256, Electron version and displays). The harness writes the evidence report elsewhere when `--evidence` is used, so run A's evidence file was copied into the run directory as `report.json` for the bless step. Each baseline was visually inspected before use: Overview (Command Center, OFFLINE state, "Ready for the next command") at both sizes; overlay idle (top-left panel, IDLE, Expand / Open center on a transparent background) at both sizes; overlay expanded (conversation panel, LISTENING, Collapse / Open center) at both sizes. None is blank or in the wrong mode.

Results ([desktop-visual-evidence-win32.json](desktop-visual-evidence-win32.json), `referenceSet: win32-baseline`, aggregate BLOCKED because 26 references are not capturable by contract):

| Scenario | Status | Ratio |
| --- | --- | ---: |
| command-center-overview-1920x1080 | PASS | 0 |
| command-center-overview-2560x1440 | PASS | 0 |
| overlay-compact-idle-1920x1080 | PASS | 0 |
| overlay-compact-idle-2560x1440 | PASS | 0 |
| overlay-expanded-1920x1080 | PASS | 0 |
| overlay-expanded-2560x1440 | PASS | 0 |

Run history, all against the same unchanged baselines (nothing was re-blessed to chase a result):

1. B1: all six comparisons PASS at ratio 0, then the controller failed with `EPERM` opening `request.json` on the WSL share (`visual-exited-before-response`), so the run is recorded as FAIL (infrastructure).
2. Probe fix: read-time `EPERM`/`EBUSY` on `request.json` are now treated as transient like `ENOENT`; `EACCES` and other errors still fail closed and the 180s controller deadline is unchanged. Covered by `desktop/src/visual-probe.test.js`.
3. B2: completed. 6 PASS, ratio 0. This is the published evidence file.
4. B3 (stability repeat): the host PowerShell reported "Acceso denegado a la ruta de acceso" (access denied) after 4 PASS comparisons; recorded as FAIL (infrastructure), owned processes cleaned.
5. B4 (stability repeat): completed. 6 PASS, ratio 0, host exit 0, no remaining processes or cleanup errors.

A same-platform baseline PASS proves that the Windows render of these three views is stable run to run on this host. It does **not** prove pixel parity with the Phase 2 Linux references; against those, the result remains FAIL (above). The baselines are one host's rendering and would need re-blessing after intentional UI changes.

## Phase 2 Playwright regression (separate result)

S09 recorded 90 passed, 2 failed (`ui/src/testing/controls.spec.ts:121`, 1080p and 1440p) because the test expected `Offline` while the UI renders the typed disconnected label. S10 T01 updated that stale assertion; snapshots were not touched. Fresh run in S10 T04: `npm --prefix ui exec playwright test` **92 passed, 0 failed**, including all 32 screenshot assertions. Browser keyboard, axe and reduced-motion tests passed; this is not a native accessibility audit.

## Harness fixes and contract verification

Minimal probe-only fixes discovered during actual host runs:

1. Use PowerShell `Set-Content -Encoding Ascii` for fixed ASCII request JSON instead of constructing a UTF8Encoding object forbidden by ConstrainedLanguage; use `Wait-Process` instead of the forbidden Process.WaitForExit method.
2. Bound request rename retries to five waits of 100ms for WSL sharing violations. In the probe, tolerate only ENOENT during request read as a transient rename gap; other filesystem/validation failures remain fatal and the 180s controller deadline remains in force.
3. Include non-client frame dimensions when selecting the monitor so the available 4K display serves framed 1080p content rather than incorrectly blocking it on a 1080p screen.

4. (S10) Treat `EPERM`/`EBUSY` during the request read as transient, as described in the run history above.

No tolerance changes, snapshot updates, new dependencies, frontend changes, or security API expansion.

Final contracts (S10 T04 run): `npm --prefix desktop test` **233 passed**, `npm --prefix desktop run test:all-native` **207 passed**, `node --test desktop/scripts/visual-evidence.test.cjs` **6 passed** (validates both the Phase 2 and win32 evidence files, including manifest sha256 matches). The evidence tests consume only tracked JSON and baselines, never ignored run directories.
