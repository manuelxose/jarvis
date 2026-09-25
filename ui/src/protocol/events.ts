/**
 * Jarvis UI Bridge — typed event contract.
 *
 * Mirrors `src/jarvis/observability/event_hub.py::SCHEMAS` field-for-field.
 * This file is the frontend half of that contract; it must be updated
 * whenever SCHEMAS changes. Phase 1 defines types only — no transport is
 * wired yet (see docs/architecture/frontend-architecture.md, "Jarvis UI
 * Bridge"). Implementing a live transport is Phase 2+ (GSD milestone M008,
 * slice S01 "Event hub and instrumentation contract").
 */

export interface JarvisEventEnvelope {
  readonly v: 1
  readonly ts: number
  readonly mono_ms: number
}

export interface ActivationStarted extends JarvisEventEnvelope {
  readonly name: 'activation.started'
  readonly source: string
}

export interface ActivationCancelled extends JarvisEventEnvelope {
  readonly name: 'activation.cancelled'
  readonly reason: string
}

export interface ActivationConfirmed extends JarvisEventEnvelope {
  readonly name: 'activation.confirmed'
  readonly source: string
  readonly confidence?: number | null
}

export interface StartupProgress extends JarvisEventEnvelope {
  readonly name: 'startup.progress'
  readonly phase: string
}

export interface StartupCompleted extends JarvisEventEnvelope {
  readonly name: 'startup.completed'
  readonly welcome_source: string
  readonly timings_ms: Record<string, number>
}

export interface StartupDegraded extends JarvisEventEnvelope {
  readonly name: 'startup.degraded'
  readonly issues: readonly string[]
  readonly phase: string
}

export interface VoiceState extends JarvisEventEnvelope {
  readonly name: 'voice.state'
  readonly state: string
  readonly reason: string
}

export interface VoiceLoading extends JarvisEventEnvelope {
  readonly name: 'voice.loading'
  readonly reason: string
}

export interface VoiceReady extends JarvisEventEnvelope {
  readonly name: 'voice.ready'
  readonly load_ms?: number | null
}

export interface VoiceEvicted extends JarvisEventEnvelope {
  readonly name: 'voice.evicted'
  readonly reason: string
}

export interface SpeechStarted extends JarvisEventEnvelope {
  readonly name: 'speech.started'
  readonly trace_id: string
  readonly source: 'live' | 'cache'
}

export interface SpeechCompleted extends JarvisEventEnvelope {
  readonly name: 'speech.completed'
  readonly trace_id: string
  readonly cancelled: boolean
}

export interface AgentStarted extends JarvisEventEnvelope {
  readonly name: 'agent.started'
  readonly trace_id: string
  readonly route: string
}

export interface AgentProgress extends JarvisEventEnvelope {
  readonly name: 'agent.progress'
  readonly trace_id: string
  readonly detail: string
}

export interface AgentCompleted extends JarvisEventEnvelope {
  readonly name: 'agent.completed'
  readonly trace_id: string
  readonly route: string
  readonly ok: boolean
  readonly elapsed_ms: number
}

export interface SystemMetrics extends JarvisEventEnvelope {
  readonly name: 'system.metrics'
  readonly cpu_percent: number
  readonly ram_percent: number
  readonly gpu: Record<string, unknown> | null
}

/** Discriminated union over every event `name` the Python EventHub can publish. */
export type JarvisEvent =
  | ActivationStarted
  | ActivationCancelled
  | ActivationConfirmed
  | StartupProgress
  | StartupCompleted
  | StartupDegraded
  | VoiceState
  | VoiceLoading
  | VoiceReady
  | VoiceEvicted
  | SpeechStarted
  | SpeechCompleted
  | AgentStarted
  | AgentProgress
  | AgentCompleted
  | SystemMetrics

/**
 * Transport boundary between Jarvis Core (Python) and Jarvis Frontend (React).
 * No implementation in Phase 1 — the desktop shell ADR determines whether
 * this is backed by a WebSocket, a shell-native IPC channel, or stdio.
 * Components must depend on this interface, never on a concrete transport.
 */
export interface JarvisEventTransport {
  subscribe(listener: (event: JarvisEvent) => void): () => void
  readonly connected: boolean
}
