# M011/S08 performance and accessibility report

Scope: **synthetic fixture measurements in headless Chromium under WSL2**. Nothing here is native Windows CPU/RAM, microphone, playback or two-clap proof; that stays with S09.

## Environment

- Linux 5.15 (WSL2), Node v24.13.0, Playwright 1.63.0, headless Chromium (Playwright-managed), Vite dev server on localhost:5173 started by `ui/playwright.config.ts`.
- Performance sampling runs only on project `desktop-1080p` (1920x1080); frame cost does not depend on viewport. Accessibility/visual specs run on both desktop projects.
- No daemon, secrets, audio or provider data. Attachments contain synthetic counters only.

## Commands

```
node ui/node_modules/@playwright/test/cli.js test --config ui/playwright.config.ts visual-core.spec.ts visual-core-accessibility.spec.ts visual-core-performance.spec.ts --workers=1
npm --prefix ui run test:transport
npm --prefix ui run build
npm --prefix ui run lint
```

Browser run: 136 tests planned, **103 passed, 0 failed, 0 flaky, 33 skipped** (performance cases skipped on the second project by design), 337.5 s.

## Method

`ui/src/testing/visual-core-performance.spec.ts` + the fixture-only `?perf=1` mode of `ui/src/visual-core-fixtures-main.tsx`. Warmup 1 s, then 10 s measured sample per case, actual elapsed time recorded. Frame intervals come from browser rAF deltas; CDP `Performance.getMetrics` deltas (script/task duration, layout, style recalc, heap, nodes) around a forced GC. Unavailable CDP metrics are reported as `"unavailable"`, never 0.

## Results: 10 s samples (p50 / p95 frame interval ms; reference 16.7 ms)

All 16 cases: 600-601 samples, elapsed 10.0-10.02 s, 0 intervals over 1.5x reference, 0 semantic React commits (including startup), 1 subscriber, DOM nodes constant within each case.

| Presentation | Motion | State | p50 / p95 | Renderer RAF | Script s | Task s |
|---|---|---|---|---|---|---|
| compact-overlay | full | idle | 16.7 / 16.8 | 601 | 0.077 | 0.381 |
| compact-overlay | full | listening | 16.7 / 16.7 | 601 | 0.092 | 0.383 |
| compact-overlay | full | speaking | 16.7 / 16.7 | 601 | 0.097 | 0.317 |
| compact-overlay | full | startup | 16.7 / 16.7 | 601 | 0.085 | 0.209 |
| compact-overlay | disabled | idle | 16.7 / 16.8 | 0 | 0.034 | 0.124 |
| compact-overlay | disabled | listening | 16.7 / 16.8 | 0 | 0.056 | 0.158 |
| compact-overlay | disabled | speaking | 16.7 / 16.7 | 0 | 0.056 | 0.154 |
| compact-overlay | disabled | startup | 16.7 / 16.8 | 0 | 0.040 | 0.134 |
| command-center | full | idle | 16.7 / 16.7 | 601 | 0.074 | 0.373 |
| command-center | full | listening | 16.7 / 16.7 | 602 | 0.099 | 0.419 |
| command-center | full | speaking | 16.7 / 16.8 | 601 | 0.114 | 0.361 |
| command-center | full | startup | 16.7 / 16.7 | 601 | 0.094 | 0.222 |
| command-center | disabled | idle | 16.7 / 16.7 | 0 | 0.040 | 0.140 |
| command-center | disabled | listening | 16.7 / 16.8 | 0 | 0.058 | 0.163 |
| command-center | disabled | speaking | 16.7 / 16.7 | 0 | 0.058 | 0.158 |
| command-center | disabled | startup | 16.7 / 16.7 | 0 | 0.040 | 0.132 |

Observations: full mode runs about 60 renderer rAF callbacks/s and costs roughly 2-3x the task time of disabled mode; the disabled residue is the page's own sampling probe plus synthetic telemetry. Heap start/end per case is in the attachments (about 2.6-3.0 MB, trend within GC noise; not used as a leak signal). Frame p50/p95 sit at vsync because headless Chromium is vsync-bound; they show no jank, not headroom.

## Results: lifecycle and extended run

- 100 repeated mount/dispose cycles per presentation (alternating cancel / disconnect / stale-session frames, 3.3 s each): DOM nodes 14 at cycles 10, 50 and 100; subscribers, driver timers and pending service timers 0 at every checkpoint; renderer rAF outstanding 0; listeners 2 (same as baseline). CDP nodes 31 -> 31. Heap 2.25 -> 2.64 MB (compact), 2.28 -> 2.68 MB (command-center), reported as trend only.
- Sustained 60 s command-center run with synthetic 30 Hz speaking telemetry and a remount at slice 3 (61.2 s elapsed, 3600 samples): p50 16.7 ms, p95 16.7-16.8 ms per 10 s slice, 0 semantic commits per slice with about 303 envelopes per slice, 48 DOM nodes constant, heap per slice 2.76 -> ~2.9 MB (small monotone drift of tens of KB per slice, inside this short run; not extrapolated).
- The 30 Hz plus bounded 10x burst test asserts envelopes do not fan out into semantic renders or timers.

## Enforced invariants vs diagnostic reference

Enforced (test fails): at least 100 intervals sampled, elapsed >= planned minus 200 ms, one subscriber, at most one pending service timer, zero semantic commits per envelope (startup at most 12), envelopes > 200 for speaking/listening, disabled mode: zero renderer rAF, zero SVG writes, zero outstanding rAF; full mode: more than 100 rAF callbacks and SVG writes with exactly one outstanding; disposal leaves no timers/subscribers; no post-unmount DOM writes.

Diagnostic only (recorded, not gated): p50/p95 versus the 16.7 ms 60 fps reference, script/task deltas, heap and DOM trends. No pass threshold was derived from these results.

## Accessibility (`visual-core-accessibility.spec.ts`, 38 tests)

- All ten states expose a visible text label plus a glyph, so identification is not colour-only, in compact-overlay and command-center.
- Label/detail text updates immediately and the SVG keeps its accessible name; a single separate polite live region per core announces state.
- Announcement policy: coalesced at 500 ms; the latest value wins, and terminal error/disconnected/startup outcomes arriving during the debounce are preserved; identical repeats do not re-announce; pending timers are cancelled on state replacement, source replacement and unmount; announcement writes do not scale with envelopes.
- Disabled and reduced motion: zero renderer rAF callbacks, no CSS animation/transition, axe clean; semantics and startup phase/app outcomes stay complete. Runtime `prefers-reduced-motion` toggles (including mid-speech) switch mode without remount; explicit disabled keeps precedence per `resolveMotionMode`.
- Missing source and disconnected/error fall back to text.

## Not measured / blocked

- Native idle CPU < 2% and RAM < 75 MB: **UNMEASURED/BLOCKED**. No native process measurement was taken here; `powershell.exe` exists in this WSL host but Windows-only acceptance (microphone, playback, two-clap, native CPU/RAM) is S09. Short headless samples must not be read as native guarantees.
- Real audible/device timing: see `audio-visualization.md` evidence status (BLOCKED).
- Chromium-reported metrics were all available in this run; if CDP is unavailable the spec writes `"unavailable"`.
- Graphify: `graphify-out/graph.json` does not exist, so no refresh was done.
