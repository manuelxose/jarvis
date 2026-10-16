# Phase 5 handoff (documentation only)

Lists existing seams that Phase 5 can build on. No hooks, endpoints or implementation were added in Phase 4. Each seam below was checked in source.

## Existing conversation events

- Event hub names published today (`src/jarvis/application/turn_manager.py`): `agent.started`, `agent.completed`, `speech.started`, `speech.completed`; voice manager: `voice.state`, `voice.loading`, `voice.ready`, `voice.evicted` (`src/jarvis/application/voice_manager.py`); daemon: `activation.*`, `startup.completed`, `system.metrics` (`src/jarvis/apps/daemon.py`).
- UI event types for these are declared in `ui/src/protocol/events.ts` (`speech.*`, `agent.started`, `agent.progress`, `agent.completed`).
- Visual signals (`core.signal`, `core.snapshot`) are presentation facts, not transcripts; see [audio-visualization.md](audio-visualization.md).

## Existing memory APIs

`src/jarvis/adapters/memory/service.py` (`MemoryService`: `recall`, `remember`, `record_turn`, `consolidate`, `redact`, `promote`, `delete`, `inspect`, `cleanup`, preferences) over `src/jarvis/adapters/memory/store.py` (`MemoryStore`: `add`, `recall`, `list`, `correct`, `delete`, `promote`). No network endpoint exposes memory today.

## Existing conversation UI seams

`ui/src/components/jarvis/ConversationMessage.tsx` (`ConversationMessageData`), `StreamingResponsePlaceholder.tsx`, `AgentActivity.tsx` (`AgentActivityEntry`), `TaskProgress.tsx`, and the Command Center feature at `ui/src/features/command-center`.

## Existing notification and activity infrastructure

`ui/src/components/interaction/Notification.tsx` (`NotificationProvider`, `useNotify`, `NotificationViewport`) and `AgentActivityList`.

## Future integration requirements (not implemented)

1. A transcript/message event carrying turn ids, so UI messages can be tied to the `turn` ids already used by `CoreSignals` fences.
2. A read API for memory records with redaction and ownership checks before any UI exposure.
3. Mapping of activity entries to the existing `agent.*` events, preserving the fail-closed fence behavior.
4. Phase 4 blockers ([phase-4-report.md](phase-4-report.md)) should be cleared before relying on real-runtime behavior.
