# Desktop automated QA results (M010/S08/T02)

Date: 2026-10-14. Host: WSL2 Linux. These are automated checks only; none is Windows hardware acceptance (see `desktop-windows-qa-matrix.md`).

| Layer | Command | Exit | Result |
|-------|---------|------|--------|
| desktop | `npm --prefix desktop test` | 0 | 211 pass / 0 fail |
| desktop | `npm --prefix desktop run test:all-native` (`node --test scripts/*.test.cjs`) | 0 | 147 pass / 0 fail |
| desktop | `npm run test:native-smoke` | 0 | 41 pass / 0 fail |
| desktop | `npm run test:native-core-lifecycle` | 0 | 10 pass / 0 fail |
| desktop | `npm run test:native-tray` | 0 | 8 pass / 0 fail |
| desktop | `node --test scripts/native-overlay.test.cjs scripts/native-command-center.test.cjs scripts/native-window-state.test.cjs` | 0 | 88 pass / 0 fail |
| ui | `npm --prefix ui run build` (tsc -b + vite) | 0 | built |
| ui | `npm --prefix ui run lint` (oxlint) | 0 | 0 errors; react fast-refresh warnings only (pre-existing) |
| ui | `npm --prefix ui run test:transport` | 0 | 36 pass / 0 fail |
| ui | `npm --prefix ui run test:desktop-command-center` (Playwright) | 0 | 39 pass / 0 fail |
| python | `pytest tests/test_contracts.py tests/test_ui_protocol_parity.py tests/test_hermes_protocol.py -q` (shared `../../.venv`, `PYTHONPATH=src`) | 0 | 10 pass, 16 subtests |
| python | `desktop/scripts/windows-overlay-probe.test.py` | 0 | 21 pass |
| python | `desktop/scripts/windows-window-state-probe.test.py` | 0 | 6 pass |

No command was BLOCKED.

## Fix made

`desktop/scripts/native-overlay.test.cjs` resolved its import and Python probe paths relative to the repo root (`./desktop/...`), so it failed when run from `desktop/` (`test:all-native`, per-script runs). Paths are now `__dirname`-relative; it passes from both working directories.

## New script

`desktop/package.json`: `test:all-native` runs every `desktop/scripts/*.test.cjs`.

## Final run (M010/S09/T03, 2026-10-14)

Re-run on the current head. Brief §21 was not found; the acceptance ledger ([`phase-3-acceptance.json`](phase-3-acceptance.json)) is reconstructed from the roadmap and slice demos.

| Layer | Command | Exit | Result |
|-------|---------|------|--------|
| desktop | `npm --prefix desktop test` | 0 | 231 pass / 0 fail |
| desktop | `npm --prefix desktop run test:all-native` | 0 | 181 pass / 0 fail (includes visual-evidence and acceptance-ledger contracts) |
| ui | `npm --prefix ui run build` | 0 | built |
| ui | `npm --prefix ui run lint` | 0 | 0 errors, 2 warnings |
| ui | `npm --prefix ui run test:transport` | 0 | 36 pass / 0 fail |
| ui | Playwright visual suite | n/a | reused from T02 (unchanged frontend); see `desktop-visual-verification.md` |
| python | `pytest tests/test_contracts.py tests/test_ui_protocol_parity.py tests/test_hermes_protocol.py` plus untouched-voice `tests/test_claps.py test_claps_cli.py test_startup.py test_startup_cli.py test_tts_fallback.py test_tts_provider.py test_tts_routing.py` (`../../.venv`, `PYTHONPATH=src`) | 0 | 128 pass, 33 subtests |
| live | `npm --prefix desktop run qa:matrix -- --evidence docs/desktop-qa-evidence.json` (powershell.exe bridge, `JARVIS_WIN_PYTHON` set) | 1 (aggregate BLOCKED) | 7 PASS, 2 BLOCKED, 0 FAIL (tray re-run with a Core on 47811 and re-scored); see `desktop-windows-qa-matrix.md` |

The matrix result changed from S08 (7 PASS): tray-shortcuts-login is BLOCKED because no Core was listening on the default port. One intermediate matrix run reported a transient window-state FAIL (`window-state-exited-before-probe`) that did not reproduce standalone or on rerun.
