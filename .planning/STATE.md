# Jarvis v2 state

## Current status

- **Workflow:** Jarvis v2 foundation plan.
- **Phase:** Phase 00 legacy extraction complete; Phase 01 — Foundation verified.
- **Approved design:** `docs/superpowers/specs/2026-09-16-jarvis-v2-design.md` at commit `1c0165999bdaa4a557883f2f90b3b3c33e48bddd`.
- **Plan:** `docs/superpowers/plans/2026-09-16-jarvis-v2-foundation.md`.
- **Evidence:** `.planning/research/legacy-extraction.md` records verified source and Graphify findings only.
- **Foundation gate:** `.planning/phases/01-foundation/VERIFICATION.md` records the committed Tasks 1–6, local CI-equivalent checks, degraded-mode CLI smoke check, and local-only Graphify refresh.

## Decisions and constraints

- Launch is Windows-first; provider policy is hybrid cloud-first with local fallback; persistent memory is local and user-controlled; Hermes is a managed local child process.
- The legacy code and assets remain in place as reference. The legacy `main.py` composition is replaced in v2 rather than incrementally extended.
- `graphify-out/` supplied evidence and remains local-only; it is not staged.
- Secrets, environment files, private logs, binary audio, conversation data, and memory data were not read or copied.

## Deferred phase decisions

- Phase 02 selects providers only after the provider bake-off and measured latency/quality results.
- Phase 05 defines the Hermes adapter protocol, child-process command, and recovery details.
- Phase 06 decides whether measured recall justifies embedding/vector retrieval beyond the initial SQLite FTS5 path.
- Phase 07 defines provider-routing and failover policy from measured capabilities and failure behavior.

## Next command

`gsd plan-phase 02`
