/**
 * Jarvis UI Bridge — typed event contract.
 *
 * Mirrors `src/jarvis/observability/event_hub.py::SCHEMAS` field-for-field.
 * This file is the frontend half of that contract; it must be updated
 * whenever SCHEMAS changes. The live transport is SSE, implemented in
 * services/sse-transport.ts (see docs/architecture/frontend-architecture.md,
 * "Jarvis UI Bridge").
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

export interface CommandExecuted extends JarvisEventEnvelope {
  readonly name: 'command.executed'
  readonly trace_id: string
  readonly tool: string
  readonly ok: boolean
  readonly elapsed_ms: number
}

export interface TurnCost extends JarvisEventEnvelope {
  readonly name: 'turn.cost'
  readonly trace_id: string
  readonly route: string
  readonly entries: readonly Record<string, unknown>[]
  readonly total_usd: number
}

export interface SystemMetrics extends JarvisEventEnvelope {
  readonly name: 'system.metrics'
  readonly cpu_percent: number
  readonly ram_percent: number
  readonly gpu: Record<string, unknown> | null
}

export interface CoreLevelSample {
  readonly level: number
  readonly peak: number
  readonly age_ms: number
}

/** Session-sequenced visual-core signal; kind-specific fields are optional here and validated by the visual-core normalizer. */
export interface CoreSignal extends JarvisEventEnvelope {
  readonly name: 'core.signal'
  readonly session: string
  readonly generation: number
  readonly seq: number
  readonly age_ms: number
  readonly kind: string
  readonly turn?: string
  readonly ok?: boolean
  readonly playback?: string
  readonly confirmed?: boolean
  readonly latency_ms?: number | null
  readonly outcome?: string
  readonly active?: boolean
  readonly level?: number
  readonly peak?: number
}

export interface CoreSnapshot extends JarvisEventEnvelope {
  readonly name: 'core.snapshot'
  readonly session: string
  readonly generation: number
  readonly watermark: number
  readonly age_ms: number
  readonly facts: Record<string, unknown>
  readonly fences: Record<string, unknown>
  readonly replaces_session?: string | null
  readonly output?: CoreLevelSample | null
  readonly microphone?: CoreLevelSample | null
  readonly candidate?: { readonly id: string; readonly remaining_ms: number } | null
  readonly success_remaining_ms?: number
  readonly error_remaining_ms?: number
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
  | CommandExecuted
  | TurnCost
  | CoreSignal
  | CoreSnapshot
  | SystemMetrics

/** Every event `name` exactly once; plain data so non-React code can validate incoming frames. */
export const JARVIS_EVENT_NAMES = [
  'activation.started',
  'activation.cancelled',
  'activation.confirmed',
  'startup.progress',
  'startup.completed',
  'startup.degraded',
  'voice.state',
  'voice.loading',
  'voice.ready',
  'voice.evicted',
  'speech.started',
  'speech.completed',
  'agent.started',
  'agent.progress',
  'agent.completed',
  'command.executed',
  'turn.cost',
  'core.signal',
  'core.snapshot',
  'system.metrics',
] as const satisfies readonly JarvisEvent['name'][]

/**
 * Transport boundary between Jarvis Core (Python) and Jarvis Frontend (React).
 * The live implementation is services/sse-transport.ts (token-protected SSE on
 * 127.0.0.1). Components must depend on this interface, never on a concrete
 * transport.
 */
export interface JarvisEventTransport {
  subscribe(listener: (event: JarvisEvent) => void): () => void
  readonly connected: boolean
}
