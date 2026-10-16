import {
  deriveVisualCore, initialVisualCore, transitionVisualCore,
  LEVEL_MAX_AGE_MS, LIVENESS_MS, SNAPSHOT_TIMEOUT_MS,
  type AppSummary, type Connection, type Reason, type StartupPhase, type VisualCoreMachine, type VisualState,
} from '../components/jarvis/visual-core/visualCoreMachine.ts'

/*
 * Design choice: a reducer-only interface forces each renderer to own timers and
 * clocks; a per-component hook ties semantics to React and re-renders per sample.
 * A tiny React-independent external store lets two future renderers (one per
 * window, each process-local) subscribe to semantic changes while pulling
 * high-rate levels imperatively via readSignals(), so samples never notify.
 *
 * Boundary: input is `unknown` and validated by the machine. Legacy v1 events
 * (speech.started, voice.state) lack session/generation/seq/ageMs and are
 * rejected; nothing here synthesizes them. No tokens, PCM, commands or network.
 */

/** Semantic, low-frequency state. Replaced (new identity) only when a field changes. */
export interface VisualCoreSemantic {
  readonly state: VisualState
  readonly label: string
  readonly detail: string | null
  readonly connection: Connection
  readonly awaitingSnapshot: boolean
  readonly reason: Reason | null
  readonly partial: boolean
  readonly startup: StartupPhase
  readonly apps: AppSummary | null
}
/** Bounded latest values in 0..1. Never announce these as semantic activity. */
export interface VisualCoreSignals { readonly output: number; readonly peak: number; readonly microphone: number }
export interface VisualCoreDiagnostics { readonly rejected: number; readonly listenerErrors: number; readonly pendingTimers: number; readonly disposed: boolean }

/** Structural contract consumed by renderers. */
export interface VisualCoreSource {
  getSnapshot(): VisualCoreSemantic
  subscribe(listener: () => void): () => void
  readSignals(now?: number): VisualCoreSignals
}
export interface VisualCoreScheduler {
  set(callback: () => void, delayMs: number): unknown
  clear(handle: unknown): void
}
export interface VisualCoreServiceOptions {
  /** Monotonic local milliseconds. Defaults to performance.now(). */
  readonly clock?: () => number
  readonly scheduler?: VisualCoreScheduler
}
export interface VisualCoreService extends VisualCoreSource {
  /** Ingest an unknown normalized event or snapshot. Returns false when rejected or disposed. */
  ingest(input: unknown, receivedAt?: number): boolean
  setConnection(status: Connection): boolean
  /** Advance the machine to the injected clock; the scheduled deadline calls this too. */
  tick(): void
  getDiagnostics(): VisualCoreDiagnostics
  dispose(): void
}

const ZERO: VisualCoreSignals = Object.freeze({ output: 0, peak: 0, microphone: 0 })
const defaultScheduler: VisualCoreScheduler = {
  set: (cb, ms) => setTimeout(cb, ms),
  clear: handle => clearTimeout(handle as ReturnType<typeof setTimeout>),
}

const sameApps = (a: AppSummary | null, b: AppSummary | null) => a === b || (!!a && !!b && a.ready === b.ready && a.failed === b.failed && a.pending === b.pending && a.total === b.total)

function nextDeadline(s: VisualCoreMachine): number {
  let d = Infinity
  const at = (t: number) => { if (t > s.now && t < d) d = t }
  if (s.candidate) at(s.candidate.until)
  at(s.successUntil)
  at(s.errorUntil)
  // Freshness/liveness expire strictly after the cutoff, hence +1 ms.
  if (s.output && s.facts.playback) at(s.output.receivedAt - s.output.ageMs + LEVEL_MAX_AGE_MS + 1)
  if (s.awaitingSince !== null) at(s.awaitingSince + SNAPSHOT_TIMEOUT_MS)
  else if (s.connection === 'connected' && s.lastLiveAt !== null) at(s.lastLiveAt + LIVENESS_MS + 1)
  return d
}

export function createVisualCoreService(options: VisualCoreServiceOptions = {}): VisualCoreService {
  const clock = options.clock ?? (() => performance.now())
  const scheduler = options.scheduler ?? defaultScheduler
  let machine = initialVisualCore(clock())
  const listeners = new Set<() => void>()
  let disposed = false
  let listenerErrors = 0
  let pending: { handle: unknown; at: number } | null = null

  const project = (): VisualCoreSemantic => {
    const v = deriveVisualCore(machine, machine.now)
    return { state: v.state, label: v.label, detail: v.detail, connection: v.connection, awaitingSnapshot: v.awaitingSnapshot, reason: v.reason, partial: v.partial, startup: v.startup, apps: v.apps }
  }
  let snapshot = project()

  const reschedule = () => {
    const at = nextDeadline(machine)
    // An earlier-or-equal pending deadline is kept: it wakes, re-evaluates and reschedules.
    if (pending && pending.at <= at) return
    if (pending) { scheduler.clear(pending.handle); pending = null }
    if (at === Infinity) return
    const handle = scheduler.set(() => { pending = null; tick() }, Math.max(1, at - machine.now))
    pending = { handle, at }
  }

  const publish = () => {
    const next = project()
    const same = next.state === snapshot.state && next.label === snapshot.label && next.detail === snapshot.detail && next.connection === snapshot.connection &&
      next.awaitingSnapshot === snapshot.awaitingSnapshot && next.reason === snapshot.reason && next.partial === snapshot.partial && next.startup === snapshot.startup &&
      sameApps(next.apps, snapshot.apps)
    if (!same) {
      snapshot = Object.freeze(next)
      for (const listener of [...listeners]) {
        if (!listeners.has(listener)) continue
        try { listener() } catch { listenerErrors = Math.min(Number.MAX_SAFE_INTEGER, listenerErrors + 1) }
      }
    }
    if (!disposed) reschedule()
  }

  const apply = (input: unknown, receivedAt?: number): boolean => {
    if (disposed) return false
    const before = machine.rejected
    machine = transitionVisualCore(machine, input, Math.max(clock(), machine.now), receivedAt)
    publish()
    return machine.rejected === before
  }

  function tick() {
    if (disposed) return
    machine = transitionVisualCore(machine, { kind: 'tick' }, Math.max(clock(), machine.now))
    publish()
  }

  snapshot = Object.freeze(snapshot)
  return {
    getSnapshot: () => snapshot,
    subscribe(listener) {
      if (disposed) return () => {}
      listeners.add(listener)
      return () => { listeners.delete(listener) }
    },
    readSignals(now = clock()) {
      if (disposed) return ZERO
      const v = deriveVisualCore(machine, now)
      return v.output === 0 && v.peak === 0 && v.microphone === 0 ? ZERO : { output: v.output, peak: v.peak, microphone: v.microphone }
    },
    ingest(input, receivedAt) {
      // A receivedAt later than the clock cannot be honoured; the machine rejects it.
      return apply(input, receivedAt)
    },
    setConnection: status => apply({ kind: 'connection', status }),
    tick,
    getDiagnostics: () => ({ rejected: machine.rejected, listenerErrors, pendingTimers: pending ? 1 : 0, disposed }),
    dispose() {
      if (disposed) return
      disposed = true
      if (pending) scheduler.clear(pending.handle)
      pending = null
      listeners.clear()
    },
  }
}
