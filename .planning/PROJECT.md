# Jarvis v2

## Active scope

Jarvis v2 is a greenfield, **Windows-first** Python voice-assistant rebuild. It is one runtime with a supervised, managed Hermes local child process. The provider policy is **hybrid cloud-first**: measured cloud providers are preferred when they meet latency and quality goals, with local fallback. Persistent memory stays local and user-controlled: it is inspectable, correctable, deletable, and retention-configurable.

The approved design is [docs/superpowers/specs/2026-09-16-jarvis-v2-design.md](../docs/superpowers/specs/2026-09-16-jarvis-v2-design.md). The active implementation scope is [docs/superpowers/plans/2026-09-16-jarvis-v2-foundation.md](../docs/superpowers/plans/2026-09-16-jarvis-v2-foundation.md); its foundation phase follows the completed legacy-extraction evidence handoff.

## Product commitments

- The primary experience metric is `speech_end_to_first_audio_ms`, reported at p50 and p95.
- Read-only and reversible actions run immediately; destructive or externally visible actions require explicit confirmation.
- Hermes is managed by Jarvis for lifecycle, health, restart, shutdown, and degraded-mode recovery.
- Voice support includes Spanish, English, and mixed Spanish/English technical vocabulary.

## Legacy boundary

The existing Python/Windows assistant remains in place as reference material only. It supplies verified requirements, hardware assumptions, integration evidence, and acceptance scenarios, but its `main.py` composition is not extended or migrated incrementally. Private logs, voice/audio recordings, conversation data, memory databases, secrets, and environment files are excluded from the rebuild evidence.
