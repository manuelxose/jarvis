import type { Facts, Fences, Level, MachineInput, NormalizedSnapshot, StartupPhase } from '../components/jarvis/visual-core/visualCoreMachine.ts'

/*
 * Strict wire -> machine mapping for `core.signal` / `core.snapshot`. Anything else
 * (legacy speech.*, voice.state, unknown kinds, wrong types) is null; the machine
 * still validates ranges and ordering. ageMs is the producer age only: the envelope
 * ts/mono_ms are never compared with local clocks. Playback-start latency_ms is
 * diagnostic and intentionally dropped.
 */

type Rec = Record<string, unknown>
const isRec = (v: unknown): v is Rec => typeof v === 'object' && v !== null && !Array.isArray(v)
const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const str = (v: unknown): v is string => typeof v === 'string' && v.length > 0
const bool = (v: unknown): v is boolean => typeof v === 'boolean'
const strList = (v: unknown): v is string[] => Array.isArray(v) && v.every(x => typeof x === 'string')
const STARTUP: readonly string[] = ['idle', 'acknowledged', 'initializing', 'announcing', 'ready', 'degraded', 'failed', 'cancelled']
const OUTCOMES: readonly string[] = ['ended', 'cancelled', 'failed']

function normalizeSignal(e: Rec): MachineInput | null {
  if (!str(e.session) || !num(e.generation) || !num(e.seq) || !num(e.age_ms) || !str(e.kind)) return null
  const base = { session: e.session, generation: e.generation, seq: e.seq, ageMs: e.age_ms }
  switch (e.kind) {
    case 'turn-start':
    case 'cancel-turn':
      return str(e.turn) ? { ...base, kind: e.kind, turn: e.turn } : null
    case 'turn-end':
      return str(e.turn) && bool(e.ok) ? { ...base, kind: 'turn-end', turn: e.turn, ok: e.ok } : null
    case 'playback-start':
      return str(e.turn) && str(e.playback) && e.confirmed === true ? { ...base, kind: 'playback-start', turn: e.turn, playback: e.playback, confirmed: true } : null
    case 'playback-end':
      return str(e.turn) && str(e.playback) && typeof e.outcome === 'string' && OUTCOMES.includes(e.outcome)
        ? { ...base, kind: 'playback-end', turn: e.turn, playback: e.playback, outcome: e.outcome as 'ended' | 'cancelled' | 'failed' }
        : null
    case 'output':
      return str(e.turn) && str(e.playback) && num(e.level) && num(e.peak) ? { ...base, kind: 'output', turn: e.turn, playback: e.playback, level: e.level, peak: e.peak } : null
    case 'capture':
      return bool(e.active) ? { ...base, kind: 'capture', active: e.active } : null
    case 'microphone':
      return num(e.level) && num(e.peak) ? { ...base, kind: 'microphone', level: e.level, peak: e.peak } : null
    case 'candidate':
      return str(e.candidate) && bool(e.active) && num(e.remaining_ms) && e.remaining_ms >= 0
        ? { ...base, kind: 'candidate', candidate: e.candidate, active: e.active, remainingMs: e.remaining_ms }
        : null
    case 'startup':
      return typeof e.phase === 'string' && STARTUP.includes(e.phase) && bool(e.partial)
        ? { ...base, kind: 'startup', phase: e.phase as StartupPhase, partial: e.partial }
        : null
    case 'stop':
      return { ...base, kind: 'stop' }
    default:
      return null
  }
}

function level(v: unknown): Level | null | undefined {
  if (v === null || v === undefined) return null
  return isRec(v) && num(v.level) && num(v.peak) && num(v.age_ms) ? { level: v.level, peak: v.peak, ageMs: v.age_ms } : undefined
}

function facts(f: unknown): Facts | null {
  if (!isRec(f)) return null
  const { playback, apps } = f
  let pb: Facts['playback'] = null
  if (playback !== null && playback !== undefined) {
    if (!isRec(playback) || !str(playback.id) || !str(playback.turn) || playback.confirmed !== true) return null
    pb = { id: playback.id, turn: playback.turn, confirmed: true }
  }
  let appList: Facts['apps'] = null
  if (apps !== null && apps !== undefined) {
    if (!Array.isArray(apps) || !apps.every(a => isRec(a) && str(a.id) && (a.status === 'pending' || a.status === 'ready' || a.status === 'failed'))) return null
    appList = apps.map(a => ({ id: a.id as string, status: a.status as 'pending' | 'ready' | 'failed' }))
  }
  const nullableStr = (v: unknown) => v === null || v === undefined || str(v)
  if (!nullableStr(f.turn) || !nullableStr(f.executing)) return null
  if (!bool(f.thinking) || !bool(f.listening) || !bool(f.generated) || !bool(f.partial) || !bool(f.failed_turn) || !bool(f.persistent_error)) return null
  if (typeof f.startup !== 'string' || !STARTUP.includes(f.startup)) return null
  return {
    turn: (f.turn as string | null | undefined) ?? null, thinking: f.thinking, executing: (f.executing as string | null | undefined) ?? null,
    listening: f.listening, generated: f.generated, playback: pb, startup: f.startup as StartupPhase, partial: f.partial,
    apps: appList, failedTurn: f.failed_turn, persistentError: f.persistent_error,
  }
}

function fences(f: unknown): Fences | null {
  if (!isRec(f)) return null
  if (!bool(f.cancelled) || !bool(f.stopped) || !bool(f.cleaned_up) || !bool(f.turn_ended) || !bool(f.overflow)) return null
  if (!strList(f.playbacks) || !strList(f.commands) || !strList(f.candidates)) return null
  return { cancelled: f.cancelled, stopped: f.stopped, cleanedUp: f.cleaned_up, turnEnded: f.turn_ended, overflow: f.overflow, playbacks: f.playbacks, commands: f.commands, candidates: f.candidates }
}

function normalizeSnapshot(e: Rec): NormalizedSnapshot | null {
  if (!str(e.session) || !num(e.generation) || !num(e.watermark) || !num(e.age_ms)) return null
  const replaces = e.replaces_session ?? null
  if (replaces !== null && !str(replaces)) return null
  const f = facts(e.facts)
  const fe = fences(e.fences)
  const output = level(e.output)
  const microphone = level(e.microphone)
  if (!f || !fe || output === undefined || microphone === undefined) return null
  let candidate: NormalizedSnapshot['candidate'] = null
  if (e.candidate !== null && e.candidate !== undefined) {
    if (!isRec(e.candidate) || !str(e.candidate.id) || !num(e.candidate.remaining_ms)) return null
    candidate = { id: e.candidate.id, remainingMs: e.candidate.remaining_ms }
  }
  const success = e.success_remaining_ms ?? 0
  const error = e.error_remaining_ms ?? 0
  if (!num(success) || !num(error)) return null
  return {
    kind: 'snapshot', session: e.session, replacesSession: replaces, generation: e.generation, watermark: e.watermark, ageMs: e.age_ms,
    facts: f, fences: fe, output, microphone, candidate, successRemainingMs: success, errorRemainingMs: error,
  }
}

export function normalizeCoreEvent(event: unknown): MachineInput | null {
  if (!isRec(event)) return null
  if (event.name === 'core.signal') return normalizeSignal(event)
  if (event.name === 'core.snapshot') return normalizeSnapshot(event)
  return null
}
