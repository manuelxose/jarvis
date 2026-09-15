# Jarvis v2 roadmap

| Phase | Scope | Depends on |
| --- | --- | --- |
| Phase 00 | Legacy extraction and planning evidence | Approved design |
| Phase 01 | Greenfield foundation: runtime lifecycle, configuration, state, events, concurrency, and supervisor boundary | Phase 00 |
| Phase 02 | Provider bake-off | Phase 01 |
| Phase 03 | Voice vertical slice | Phases 01–02 |
| Phase 04 | Wake, clap, and barge-in | Phase 03 |
| Phase 05 | Hermes integration | Phase 01 |
| Phase 06 | Memory | Phase 01 |
| Phase 07 | Provider routing and failover | Phases 02–03 |
| Phase 08 | Voice quality | Phases 03–04 |
| Phase 09 | Tools and integrations | Phases 01, 05 |
| Phase 10 | Startup | Phases 04–07, 09 |
| Phase 11 | Performance | Phases 03–10 |
| Phase 12 | Reliability | Phases 05–11 |
| Phase 13 | Production acceptance | Phases 08–12 |

## Current gate

Phase 00 is complete when the committed evidence and v2 GSD scope identify the legacy baseline without importing private data or retaining the legacy `main.py` architecture. **Phase 01 — Foundation** is the first implementation phase and may begin after that gate.

Each implementation phase requires targeted tests, relevant full checks, runtime validation where applicable, diff review, updated project state, a coherent commit, and acceptance evidence; code alone does not complete a phase.
