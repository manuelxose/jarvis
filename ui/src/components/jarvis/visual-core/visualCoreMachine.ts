/** Renderer-neutral conceptual inputs, NOT JarvisEvent wire extensions.
 * A trusted normalizer must supply session authority and core+transit age.
 * Only local now/receivedAt share a clock; no remote absolute clock is used.
 */
export const VISUAL_STATES = ['IDLE', 'LISTENING', 'THINKING', 'SPEAKING', 'EXECUTING', 'SUCCESS', 'ERROR', 'DISCONNECTED', 'STARTING', 'STOPPING'] as const
export type VisualState = typeof VISUAL_STATES[number]
export const PRIORITY: readonly VisualState[] = ['DISCONNECTED', 'STOPPING', 'ERROR', 'STARTING', 'SPEAKING', 'EXECUTING', 'THINKING', 'LISTENING', 'SUCCESS', 'IDLE']
export const LEVEL_MAX_AGE_MS = 250
export const SNAPSHOT_TIMEOUT_MS = 2000
export const LIVENESS_MS = 45000
export const SUCCESS_HOLD_MS = 900
export const ERROR_HOLD_MS = 1500
export const MAX_FENCES = 128
const MAX_ID = 256
const MAX_APPS = 32
export type Connection = 'connected' | 'connecting' | 'disconnected' | 'auth-error' | 'incompatible'
export type Reason = 'invalid-input' | 'stale-sequence' | 'sequence-gap' | 'stale-generation/session' | 'terminal-fence' | 'fence-overflow' | 'awaiting-snapshot' | 'snapshot-timeout' | 'stale/unavailable-output' | 'disconnected' | 'level-invalid' | 'outcome-unconfirmed'
export type StartupPhase = 'idle' | 'acknowledged' | 'initializing' | 'announcing' | 'ready' | 'degraded' | 'failed' | 'cancelled'
/** Snapshot sample age is measured at snapshot capture; add outer snapshot age.
 * Normalized output events instead carry total capture+transit age in Envelope.
 */
export interface Level { readonly level: number; readonly peak: number; readonly ageMs: number }
export interface Playback { readonly id: string; readonly turn: string; readonly confirmed: true }
export interface Fences {
  readonly cancelled: boolean
  readonly stopped: boolean
  readonly cleanedUp: boolean
  readonly turnEnded: boolean
  readonly overflow: boolean
  /** Shared bound across these arrays. Never evict a live-generation tombstone. */
  readonly playbacks: readonly string[]
  readonly commands: readonly string[]
  readonly candidates: readonly string[]
}
export interface Facts {
  readonly turn: string | null
  readonly thinking: boolean
  readonly executing: string | null
  readonly listening: boolean
  readonly generated: boolean
  readonly playback: Playback | null
  readonly startup: StartupPhase
  readonly partial: boolean
  readonly apps: readonly { readonly id: string; readonly status: 'pending' | 'ready' | 'failed' }[] | null
  readonly failedTurn: boolean
  readonly persistentError: boolean
}
interface Envelope { readonly session: string; readonly generation: number; readonly seq: number; readonly ageMs: number }
type TurnEvent = { readonly turn: string } & (
  | { readonly kind: 'turn-start' }
  | { readonly kind: 'turn-end'; readonly ok: boolean }
  | { readonly kind: 'execution-start'; readonly command: string }
  | { readonly kind: 'execution-end'; readonly command: string; readonly ok: boolean }
  | { readonly kind: 'generated'; readonly active: boolean }
  | { readonly kind: 'cancel-turn' }
  | { readonly kind: 'playback-start'; readonly playback: string; readonly confirmed: true }
  | { readonly kind: 'playback-end'; readonly playback: string; readonly outcome: 'ended' | 'cancelled' | 'failed' }
  | { readonly kind: 'output'; readonly playback: string; readonly level: number; readonly peak: number }
)
export type NormalizedEvent = Envelope & (TurnEvent
  | { readonly kind: 'capture'; readonly active: boolean }
  | { readonly kind: 'microphone'; readonly level: number; readonly peak: number }
  | { readonly kind: 'candidate'; readonly candidate: string; readonly active: boolean; readonly remainingMs: number }
  | { readonly kind: 'startup'; readonly phase: StartupPhase; readonly partial: boolean }
  | { readonly kind: 'stop' | 'recover' | 'heartbeat' })
export interface NormalizedSnapshot {
  readonly kind: 'snapshot'
  readonly session: string
  /** Trusted authority chain: null for initial/same-session install, current
   * session ID for replacement. Never inferred from arbitrary session traffic.
   */
  readonly replacesSession: string | null
  readonly generation: number
  readonly watermark: number
  readonly ageMs: number
  readonly facts: Facts
  readonly fences: Fences
  readonly output: Level | null
  readonly microphone: Level | null
  readonly candidate: { readonly id: string; readonly remainingMs: number } | null
  readonly successRemainingMs: number
  readonly errorRemainingMs: number
}
export type MachineInput = NormalizedEvent | NormalizedSnapshot | { readonly kind: 'connection'; readonly status: Connection } | { readonly kind: 'tick' }
interface ReceivedLevel extends Level { readonly receivedAt: number }
export interface VisualCoreMachine {
  readonly now: number
  readonly connection: Connection
  readonly session: string | null
  readonly generation: number
  readonly watermark: number
  readonly awaitingSince: number | null
  /** True from (re)connect until a snapshot installs: a restarted Core announces a new session it cannot link to the old one. */
  readonly rejoining: boolean
  readonly lastLiveAt: number | null
  readonly facts: Facts
  readonly fences: Fences
  readonly output: ReceivedLevel | null
  readonly microphone: ReceivedLevel | null
  readonly candidate: { readonly id: string; readonly until: number } | null
  readonly successUntil: number
  readonly errorUntil: number
  readonly reason: Reason | null
  /** Saturating scalar only, never an event log or payload. */
  readonly rejected: number
}
export const emptyFacts = (): Facts => ({ turn: null, thinking: false, executing: null, listening: false, generated: false, playback: null, startup: 'idle', partial: false, apps: null, failedTurn: false, persistentError: false })
export const emptyFences = (): Fences => ({ cancelled: false, stopped: false, cleanedUp: false, turnEnded: false, overflow: false, playbacks: [], commands: [], candidates: [] })
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const age = (v: unknown): v is number => finite(v) && v >= 0 && v <= Number.MAX_SAFE_INTEGER
const clock = (v: unknown): v is number => age(v) && v < Number.MAX_SAFE_INTEGER - LIVENESS_MS
const integer = (v: unknown): v is number => age(v) && Number.isSafeInteger(v)
const id = (v: unknown): v is string => typeof v === 'string' && v.trim().length > 0 && v.length <= MAX_ID
const bool = (v: unknown): v is boolean => typeof v === 'boolean'
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const oneOf = <T extends string>(v: unknown, options: readonly T[]): v is T => typeof v === 'string' && options.includes(v as T)
const phases: readonly StartupPhase[] = ['idle', 'acknowledged', 'initializing', 'announcing', 'ready', 'degraded', 'failed', 'cancelled']
const connections: readonly Connection[] = ['connected', 'connecting', 'disconnected', 'auth-error', 'incompatible']
const clamp = (v: number) => Math.max(0, Math.min(1, v))
const hold = (v: unknown, max: number): v is number => age(v) && v <= max
const level = (v: unknown): v is Level => record(v) && finite(v.level) && finite(v.peak) && age(v.ageMs)
const ids = (v: unknown): v is string[] => Array.isArray(v) && v.length <= MAX_FENCES && Array.from(v).every(id) && new Set(v).size === v.length
function validFences(v: unknown): v is Fences {
  return record(v) && ['cancelled', 'stopped', 'cleanedUp', 'turnEnded', 'overflow'].every(k => bool(v[k])) && ids(v.playbacks) && ids(v.commands) && ids(v.candidates) && v.playbacks.length + v.commands.length + v.candidates.length <= MAX_FENCES && (!v.cleanedUp || v.stopped === true)
}
function validFacts(v: unknown): v is Facts {
  if (!record(v) || !(v.turn === null || id(v.turn)) || !(v.executing === null || id(v.executing)) || !phases.includes(v.startup as StartupPhase)) return false
  if (!['thinking', 'listening', 'generated', 'partial', 'failedTurn', 'persistentError'].every(k => bool(v[k]))) return false
  if (v.playback !== null && !(record(v.playback) && id(v.playback.id) && id(v.playback.turn) && v.playback.confirmed === true && v.playback.turn === v.turn)) return false
  if ((v.thinking || v.generated || v.executing !== null || v.playback !== null) && v.turn === null) return false
  return v.apps === null || (Array.isArray(v.apps) && v.apps.length <= MAX_APPS && Array.from(v.apps).every(a => record(a) && id(a.id) && oneOf(a.status, ['pending', 'ready', 'failed'])) && new Set(v.apps.map(a => a.id)).size === v.apps.length)
}
function validSnapshot(v: Record<string, unknown>): v is Record<string, unknown> & NormalizedSnapshot {
  return id(v.session) && (v.replacesSession === null || id(v.replacesSession)) && integer(v.generation) && integer(v.watermark) && age(v.ageMs) && v.ageMs <= LIVENESS_MS && validFacts(v.facts) && validFences(v.fences) && (v.output === null || level(v.output)) && (v.microphone === null || level(v.microphone)) && (v.output === null || v.facts.playback !== null) && (v.facts.startup !== 'cancelled' || v.fences.stopped) && (v.candidate === null || (record(v.candidate) && id(v.candidate.id) && hold(v.candidate.remainingMs, SNAPSHOT_TIMEOUT_MS))) && hold(v.successRemainingMs, SUCCESS_HOLD_MS) && hold(v.errorRemainingMs, ERROR_HOLD_MS)
}
const eventKinds = ['turn-start', 'turn-end', 'execution-start', 'execution-end', 'generated', 'cancel-turn', 'playback-start', 'playback-end', 'output', 'capture', 'microphone', 'candidate', 'startup', 'stop', 'recover', 'heartbeat'] as const
function validEvent(v: Record<string, unknown>): v is Record<string, unknown> & NormalizedEvent {
  if (!id(v.session) || !integer(v.generation) || !integer(v.seq) || !age(v.ageMs) || !oneOf(v.kind, eventKinds)) return false
  if (['turn-start', 'turn-end', 'execution-start', 'execution-end', 'generated', 'cancel-turn', 'playback-start', 'playback-end', 'output'].includes(v.kind) && !id(v.turn)) return false
  switch (v.kind) {
    case 'turn-end': return bool(v.ok)
    case 'execution-start': return id(v.command)
    case 'execution-end': return id(v.command) && bool(v.ok)
    case 'generated': case 'capture': return bool(v.active)
    case 'playback-start': return id(v.playback) && v.confirmed === true
    case 'playback-end': return id(v.playback) && oneOf(v.outcome, ['ended', 'cancelled', 'failed'])
    case 'output': return id(v.playback) && finite(v.level) && finite(v.peak)
    case 'microphone': return finite(v.level) && finite(v.peak)
    case 'candidate': return id(v.candidate) && bool(v.active) && hold(v.remainingMs, SNAPSHOT_TIMEOUT_MS)
    case 'startup': return oneOf(v.phase, phases) && bool(v.partial)
    default: return true
  }
}
export function initialVisualCore(now = 0): VisualCoreMachine {
  if (!clock(now)) throw new RangeError('Invalid local monotonic clock')
  return { now, connection: 'disconnected', session: null, generation: 0, watermark: -1, awaitingSince: null, rejoining: false, lastLiveAt: null, facts: emptyFacts(), fences: emptyFences(), output: null, microphone: null, candidate: null, successUntil: 0, errorUntil: 0, reason: 'disconnected', rejected: 0 }
}
function reject(s: VisualCoreMachine, reason: Reason): VisualCoreMachine {
  return { ...s, reason, rejected: Math.min(Number.MAX_SAFE_INTEGER, s.rejected + 1) }
}
function awaitSnapshot(s: VisualCoreMachine, reason: Reason): VisualCoreMachine {
  return { ...s, awaitingSince: s.awaitingSince ?? s.now, output: null, microphone: null, reason }
}
function fence(s: VisualCoreMachine, key: 'playbacks' | 'commands' | 'candidates', value: string): VisualCoreMachine {
  if (s.fences[key].includes(value)) return s
  if (s.fences.playbacks.length + s.fences.commands.length + s.fences.candidates.length >= MAX_FENCES) {
    return { ...s, fences: { ...s.fences, overflow: true }, facts: emptyFacts(), candidate: null, output: null, microphone: null, successUntil: 0, errorUntil: 0, reason: 'fence-overflow' }
  }
  return { ...s, fences: { ...s.fences, [key]: [...s.fences[key], value] } }
}
const activeStartup = (f: Facts) => ['acknowledged', 'initializing', 'announcing'].includes(f.startup)
const blocked = (f: Fences) => f.cancelled || f.stopped || f.overflow
function advance(s: VisualCoreMachine, now: number): VisualCoreMachine {
  let n: VisualCoreMachine = { ...s, now }
  if (n.candidate && now >= n.candidate.until) {
    n = fence(n, 'candidates', n.candidate.id)
    n = { ...n, candidate: null }
  }
  if (n.output && n.output.ageMs + now - n.output.receivedAt > LEVEL_MAX_AGE_MS) n = { ...n, output: null, reason: 'stale/unavailable-output' }
  if (n.microphone && n.microphone.ageMs + now - n.microphone.receivedAt > LEVEL_MAX_AGE_MS) n = { ...n, microphone: null }
  if (n.connection === 'connected' && n.awaitingSince === null && n.lastLiveAt !== null && now - n.lastLiveAt > LIVENESS_MS) n = awaitSnapshot(n, 'awaiting-snapshot')
  if (n.awaitingSince !== null && now - n.awaitingSince >= SNAPSHOT_TIMEOUT_MS) n = { ...n, reason: 'snapshot-timeout' }
  return n
}
function sample(v: Level, receivedAt: number, extraAge = 0): ReceivedLevel {
  return { level: clamp(v.level), peak: clamp(v.peak), ageMs: v.ageMs + extraAge, receivedAt }
}
function mergeFences(a: Fences, b: Fences): Fences {
  return { cancelled: a.cancelled || b.cancelled, stopped: a.stopped || b.stopped, cleanedUp: a.cleanedUp || b.cleanedUp, turnEnded: a.turnEnded || b.turnEnded, overflow: a.overflow || b.overflow, playbacks: [...new Set([...a.playbacks, ...b.playbacks])], commands: [...new Set([...a.commands, ...b.commands])], candidates: [...new Set([...a.candidates, ...b.candidates])] }
}
function install(s: VisualCoreMachine, v: NormalizedSnapshot, receivedAt: number): VisualCoreMachine {
  if (s.connection !== 'connected') return reject(s, 'disconnected')
  const same = v.session === s.session
  if ((!same && v.replacesSession !== s.session && !(s.rejoining && v.replacesSession === null)) || (same && v.replacesSession !== null)) return reject(awaitSnapshot(s, 'awaiting-snapshot'), 'stale-generation/session')
  if (same && (v.watermark < s.watermark || v.generation < s.generation || (v.generation > s.generation && v.watermark === s.watermark))) return reject(awaitSnapshot(s, 'awaiting-snapshot'), 'stale-sequence')
  if (v.ageMs + s.now - receivedAt > LIVENESS_MS) return reject(awaitSnapshot(s, 'awaiting-snapshot'), 'invalid-input')
  const sameGeneration = same && v.generation === s.generation
  if (sameGeneration && s.facts.turn !== null && v.facts.turn !== null && v.facts.turn !== s.facts.turn) return reject(awaitSnapshot(s, 'awaiting-snapshot'), 'stale-generation/session')
  let fences = mergeFences(sameGeneration ? s.fences : emptyFences(), v.fences)
  // A snapshot removing/replacing known work is also a terminal observation.
  // Preserve those identities even when the producer omits redundant old fences.
  if (sameGeneration) {
    const retired = { ...emptyFences(),
      turnEnded: s.facts.turn !== null && v.facts.turn === null,
      playbacks: s.facts.playback && s.facts.playback.id !== v.facts.playback?.id ? [s.facts.playback.id] : [],
      commands: s.facts.executing && s.facts.executing !== v.facts.executing ? [s.facts.executing] : [],
      candidates: s.candidate && s.candidate.id !== v.candidate?.id ? [s.candidate.id] : [],
    }
    fences = mergeFences(fences, retired)
  }
  if (sameGeneration) fences = { ...fences, stopped: fences.stopped || s.fences.stopped, cleanedUp: fences.cleanedUp || s.fences.cleanedUp }
  if (fences.playbacks.length + fences.commands.length + fences.candidates.length > MAX_FENCES) fences = { ...fences, playbacks: s.fences.playbacks, commands: s.fences.commands, candidates: s.fences.candidates, overflow: true }
  // Project validated fields only: unknown extras must not retain payloads or throw
  // structuredClone errors (e.g. a function hidden in an otherwise valid object).
  const source = v.facts
  const f: Facts = { turn: source.turn ?? (sameGeneration ? s.facts.turn : null), thinking: source.thinking, executing: source.executing, listening: source.listening, generated: source.generated, playback: source.playback ? { id: source.playback.id, turn: source.playback.turn, confirmed: true } : null, startup: source.startup, partial: source.partial, apps: source.apps?.map(a => ({ id: a.id, status: a.status })) ?? null, failedTurn: source.failedTurn, persistentError: source.persistentError || (sameGeneration && s.facts.persistentError) }
  if ((blocked(fences) && (f.thinking || f.executing || f.listening || f.generated || f.playback || activeStartup(f) || v.candidate || v.successRemainingMs || v.errorRemainingMs || f.persistentError)) || (fences.turnEnded && (f.thinking || f.executing || f.generated)) || (f.playback && fences.playbacks.includes(f.playback.id)) || (f.executing && fences.commands.includes(f.executing)) || (v.candidate && fences.candidates.includes(v.candidate.id))) return reject(awaitSnapshot(s, 'awaiting-snapshot'), 'terminal-fence')
  // An equal-watermark snapshot cannot refresh the same measurement or holds.
  const equal = sameGeneration && v.watermark === s.watermark
  if (equal && ((f.thinking && !s.facts.thinking) || (f.generated && !s.facts.generated) || (f.executing !== null && f.executing !== s.facts.executing) || (f.playback !== null && f.playback.id !== s.facts.playback?.id) || (activeStartup(f) && f.startup !== s.facts.startup) || (v.candidate !== null && v.candidate.id !== s.candidate?.id))) return reject(awaitSnapshot(s, 'awaiting-snapshot'), 'stale-sequence')
  let output = v.output ? sample(v.output, receivedAt, v.ageMs) : null
  let microphone = v.microphone ? sample(v.microphone, receivedAt, v.ageMs) : null
  if (equal) {
    output = s.output && output ? { ...output, ageMs: Math.max(output.ageMs, s.output.ageMs + receivedAt - s.output.receivedAt) } : null
    microphone = s.microphone && microphone ? { ...microphone, ageMs: Math.max(microphone.ageMs, s.microphone.ageMs + receivedAt - s.microphone.receivedAt) } : null
  }
  return advance({ ...s, session: v.session, generation: v.generation, watermark: v.watermark, awaitingSince: null, rejoining: false, lastLiveAt: receivedAt - v.ageMs, facts: { ...f, failedTurn: f.failedTurn || (sameGeneration && s.facts.failedTurn) }, fences, candidate: v.candidate ? { id: v.candidate.id, until: Math.min(sameGeneration && s.candidate?.id === v.candidate.id ? s.candidate.until : Infinity, receivedAt + Math.max(0, v.candidate.remainingMs - v.ageMs)) } : null, output: blocked(fences) ? null : output, microphone: blocked(fences) ? null : microphone, successUntil: equal ? Math.min(s.successUntil, receivedAt + Math.max(0, v.successRemainingMs - v.ageMs)) : receivedAt + Math.max(0, v.successRemainingMs - v.ageMs), errorUntil: equal ? Math.min(s.errorUntil, receivedAt + Math.max(0, v.errorRemainingMs - v.ageMs)) : receivedAt + Math.max(0, v.errorRemainingMs - v.ageMs), reason: null }, s.now)
}
/** Pure unknown-input boundary. Caller supplies local clocks, never Date.now(). */
export function transitionVisualCore(previous: VisualCoreMachine, input: unknown, now: number, receivedAt = now): VisualCoreMachine {
  if (!clock(now) || now < previous.now || !clock(receivedAt) || receivedAt > now || receivedAt < previous.now) return reject({ ...previous, output: null, microphone: null }, 'invalid-input')
  let s = advance(previous, now)
  if (!record(input)) return reject(s, 'invalid-input')
  if (input.kind === 'tick') return s
  if (input.kind === 'connection') {
    if (!oneOf(input.status, connections)) return reject(s, 'invalid-input')
    if (input.status === s.connection) return s // Duplicate connect cannot extend snapshot deadline.
    s = { ...s, connection: input.status, rejoining: input.status === 'connected', output: null, microphone: null }
    return input.status === 'connected' ? awaitSnapshot(s, 'awaiting-snapshot') : { ...s, awaitingSince: null, reason: 'disconnected' }
  }
  if (input.kind === 'snapshot') return validSnapshot(input) ? install(s, input, receivedAt) : reject(awaitSnapshot(s, 'awaiting-snapshot'), 'invalid-input')
  if (!validEvent(input)) {
    // Observe a well-formed session envelope even if its payload is unusable.
    // This never refreshes liveness/levels or applies lifecycle facts.
    if (id(input.session) && input.session === s.session && integer(input.generation) && integer(input.seq) && age(input.ageMs) && oneOf(input.kind, eventKinds) && s.connection === 'connected' && input.seq > s.watermark) {
      const gap = input.seq !== s.watermark + 1
      s = { ...s, watermark: input.seq }
      if (gap) s = awaitSnapshot(s, 'sequence-gap')
    }
    return reject(s, 'invalid-input')
  }
  const e = input
  if (s.connection !== 'connected') return reject(s, 'disconnected')
  if (e.session !== s.session) return reject(s, 'stale-generation/session')
  if (e.seq <= s.watermark) return reject(s, 'stale-sequence')
  const gap = e.seq !== s.watermark + 1
  s = { ...s, watermark: e.seq }
  if (gap) s = awaitSnapshot(s, 'sequence-gap')
  // Reconciliation suppresses activation, not safety: preserve observed current
  // cancellation/end/stop fences even before the missing snapshot arrives.
  const safety = e.generation === s.generation && (e.kind === 'stop' || (e.kind === 'startup' && e.phase === 'cancelled') || (e.kind === 'cancel-turn' && (s.facts.turn === null || s.facts.turn === e.turn)) || (e.kind === 'playback-end' && s.facts.playback?.id === e.playback && s.facts.turn === e.turn))
  if (s.awaitingSince !== null && !safety) return reject(s, gap ? 'sequence-gap' : 'awaiting-snapshot')
  if (e.generation < s.generation) return reject(s, 'stale-generation/session')
  // Only lifecycle establishment may advance generation. Stray future audio is not authority.
  if (e.generation > s.generation) {
    if (!['turn-start', 'startup', 'capture', 'stop'].includes(e.kind) || s.fences.stopped) return reject(s, 'stale-generation/session')
    const keep = e.kind !== 'startup' && (s.facts.startup === 'ready' || s.facts.startup === 'degraded')
    const carried: Facts = keep ? { ...emptyFacts(), startup: s.facts.startup, partial: s.facts.partial, apps: s.facts.apps } : emptyFacts()
    s = { ...s, generation: e.generation, facts: carried, fences: emptyFences(), output: null, microphone: null, candidate: null, successUntil: 0, errorUntil: 0 }
  }
  s = { ...s, lastLiveAt: Math.max(s.lastLiveAt ?? -Infinity, receivedAt - e.ageMs), reason: null }
  // Session stop must also fence an already cancelled turn or overflowing generation.
  if (s.fences.stopped && (e.kind === 'stop' || (e.kind === 'startup' && e.phase === 'cancelled'))) return reject(s, 'terminal-fence')
  if (e.kind === 'stop' || (e.kind === 'startup' && e.phase === 'cancelled')) return { ...s, facts: { ...emptyFacts(), startup: e.kind === 'startup' ? 'cancelled' : 'idle', apps: s.facts.apps, partial: s.facts.partial }, fences: { ...s.fences, stopped: true, cleanedUp: false }, candidate: null, output: null, microphone: null, successUntil: 0, errorUntil: 0 }
  // Heartbeats remain valid for liveness even behind a terminal fence.
  if (e.kind === 'heartbeat') return s
  if (blocked(s.fences)) return reject(s, s.fences.overflow ? 'fence-overflow' : 'terminal-fence')
  if ('turn' in e && s.facts.turn !== null && e.turn !== s.facts.turn) return reject(s, 'stale-generation/session')
  if (e.kind === 'cancel-turn') return { ...s, facts: { ...emptyFacts(), apps: s.facts.apps, partial: s.facts.partial }, fences: { ...s.fences, cancelled: true }, candidate: null, output: null, microphone: null, successUntil: 0, errorUntil: 0 }
  if (s.fences.turnEnded && 'turn' in e && !(s.facts.playback?.id === ('playback' in e ? e.playback : null) && ['output', 'playback-end'].includes(e.kind))) return reject(s, 'terminal-fence')
  if ('playback' in e && id(e.playback) && s.fences.playbacks.includes(e.playback)) return reject(s, 'terminal-fence')
  if ('command' in e && id(e.command) && s.fences.commands.includes(e.command)) return reject(s, 'terminal-fence')
  const f = s.facts
  switch (e.kind) {
    case 'turn-start': return { ...s, facts: { ...f, turn: e.turn, thinking: true } }
    case 'execution-start':
      if (f.turn !== e.turn || (f.executing !== null && f.executing !== e.command)) return reject(s, 'outcome-unconfirmed')
      return { ...s, facts: { ...f, executing: e.command } }
    case 'turn-end': case 'execution-end': {
      if (f.turn !== e.turn || (e.kind === 'execution-end' && f.executing !== e.command)) return reject(s, 'outcome-unconfirmed')
      s = e.kind === 'execution-end' ? fence(s, 'commands', e.command) : { ...s, fences: { ...s.fences, turnEnded: true } }
      if (s.fences.overflow) return s
      const failed = f.failedTurn || !e.ok
      const successUntil = !failed && e.ok ? receivedAt + SUCCESS_HOLD_MS : 0
      const facts = { ...f, executing: null, thinking: e.kind === 'turn-end' ? false : f.thinking, generated: e.kind === 'turn-end' ? false : f.generated, failedTurn: failed }
      return { ...s, facts, successUntil, errorUntil: !e.ok ? receivedAt + ERROR_HOLD_MS : s.errorUntil, output: !e.ok ? null : s.output, microphone: !e.ok ? null : s.microphone }
    }
    case 'generated': return f.turn === e.turn ? { ...s, facts: { ...f, generated: e.active } } : reject(s, 'outcome-unconfirmed')
    case 'capture': return { ...s, facts: { ...f, listening: e.active }, microphone: e.active ? s.microphone : null }
    case 'startup':
      if (s.candidate) s = fence(s, 'candidates', s.candidate.id)
      if (s.fences.overflow) return s
      return { ...s, facts: { ...f, startup: e.phase, partial: f.partial || e.partial || e.phase === 'degraded', persistentError: f.persistentError || e.phase === 'failed' }, candidate: null, output: e.phase === 'failed' ? null : s.output, microphone: e.phase === 'failed' ? null : s.microphone }
    case 'recover': return { ...s, facts: { ...f, persistentError: false, startup: f.startup === 'failed' ? 'idle' : f.startup }, errorUntil: 0 }
    case 'candidate':
      if (s.fences.candidates.includes(e.candidate)) return reject(s, 'terminal-fence')
      if (!e.active) {
        s = fence(s, 'candidates', e.candidate)
        return { ...s, candidate: s.fences.overflow || s.candidate?.id === e.candidate ? null : s.candidate }
      }
      if (s.candidate && s.candidate.id !== e.candidate) s = fence(s, 'candidates', s.candidate.id)
      if (s.fences.overflow) return s
      return advance({ ...s, candidate: { id: e.candidate, until: Math.min(s.candidate?.id === e.candidate ? s.candidate.until : Infinity, receivedAt + Math.max(0, e.remainingMs - e.ageMs)) } }, now)
    case 'playback-start':
      if (f.turn !== e.turn) return reject(s, 'outcome-unconfirmed')
      if (f.playback?.id === e.playback) return s // Idempotent starts never discard a valid sample.
      if (f.playback) s = fence(s, 'playbacks', f.playback.id)
      if (s.fences.overflow) return s
      return { ...s, facts: { ...f, playback: { id: e.playback, turn: e.turn, confirmed: true } }, output: null }
    case 'playback-end':
      if (f.playback?.id !== e.playback) return reject(s, 'outcome-unconfirmed')
      s = fence(s, 'playbacks', e.playback)
      if (s.fences.overflow) return s
      return { ...s, facts: { ...f, playback: null, persistentError: f.persistentError || e.outcome === 'failed' }, output: null, microphone: null, reason: e.outcome === 'failed' ? 'stale/unavailable-output' : null }
    case 'output': case 'microphone': {
      if (e.kind === 'output' && (f.playback?.id !== e.playback || f.playback.turn !== e.turn)) return reject(s, 'outcome-unconfirmed')
      const value = sample({ level: e.level, peak: e.peak, ageMs: e.ageMs }, receivedAt)
      const key = e.kind === 'output' ? 'output' : 'microphone'
      const old = s[key]
      // A higher sequence with an older measurement must not overwrite a newer one.
      if (old && value.ageMs > old.ageMs + receivedAt - old.receivedAt) return reject(s, 'stale/unavailable-output')
      return advance({ ...s, [key]: value, reason: e.level < 0 || e.level > 1 || e.peak < 0 || e.peak > 1 ? 'level-invalid' : null }, now)
    }
  }
}
export interface VisualCoreView {
  readonly state: VisualState
  readonly label: string
  readonly detail: string | null
  readonly connection: Connection
  readonly awaitingSnapshot: boolean
  readonly reason: Reason | null
  readonly output: number
  readonly peak: number
  readonly microphone: number
  readonly partial: boolean
  readonly startup: StartupPhase
  readonly apps: AppSummary | null
}
export interface AppSummary { readonly ready: number; readonly failed: number; readonly pending: number; readonly total: number }
const startingDetail: Partial<Record<StartupPhase, string>> = { acknowledged: 'Activating', initializing: 'Preparing services and music', announcing: 'Welcome' }
const labels: Record<VisualState, string> = { IDLE: 'Idle', LISTENING: 'Listening', THINKING: 'Thinking', SPEAKING: 'Speaking', EXECUTING: 'Executing', SUCCESS: 'Done', ERROR: 'Error', DISCONNECTED: 'Offline', STARTING: 'Starting', STOPPING: 'Stopping' }
/** Read at an injected local instant. Schedulers must tick at deadlines, not per frame. */
export function deriveVisualCore(s: VisualCoreMachine, now = s.now): VisualCoreView {
  const validClock = clock(now) && now >= s.now
  const offline = !validClock || s.connection !== 'connected' || s.awaitingSince !== null || s.lastLiveAt === null || now - s.lastLiveAt > LIVENESS_MS
  const stop = s.fences.stopped && !s.fences.cleanedUp
  const safe = !offline && !blocked(s.fences) && !s.facts.persistentError && s.facts.startup !== 'failed' && now >= s.errorUntil
  const fresh = safe && s.facts.playback !== null && s.output !== null && s.output.ageMs + now - s.output.receivedAt <= LEVEL_MAX_AGE_MS
  const candidate = s.candidate !== null && now < s.candidate.until
  const active: Record<VisualState, boolean> = { DISCONNECTED: offline, STOPPING: stop, ERROR: !blocked(s.fences) && (s.facts.persistentError || s.facts.startup === 'failed' || now < s.errorUntil), STARTING: !blocked(s.fences) && (candidate || activeStartup(s.facts)), SPEAKING: fresh, EXECUTING: !blocked(s.fences) && s.facts.executing !== null, THINKING: !blocked(s.fences) && (s.facts.thinking || s.facts.generated), LISTENING: !blocked(s.fences) && s.facts.listening, SUCCESS: !blocked(s.fences) && !s.facts.failedTurn && now < s.successUntil, IDLE: true }
  const appList = s.facts.apps
  const apps = appList ? { ready: appList.filter(a => a.status === 'ready').length, failed: appList.filter(a => a.status === 'failed').length, pending: appList.filter(a => a.status === 'pending').length, total: appList.length } : null
  const state = PRIORITY.find(k => active[k]) ?? 'IDLE'
  const timeout = s.awaitingSince !== null && now - s.awaitingSince >= SNAPSHOT_TIMEOUT_MS
  const unavailable = !offline && s.facts.playback !== null && !fresh
  const detail = offline ? (timeout ? 'State unavailable' : 'Awaiting authoritative state') : unavailable ? 'Output telemetry unavailable' : s.facts.generated && !fresh ? 'Speech prepared; output unconfirmed' : state === 'STARTING' ? (candidate && !activeStartup(s.facts) ? 'Confirming activation' : startingDetail[s.facts.startup] ?? null) : s.facts.partial ? 'Ready with limitations' : s.reason === 'outcome-unconfirmed' ? 'Outcome unconfirmed' : null
  return { state, label: state === 'IDLE' && s.facts.startup === 'ready' ? 'Ready' : labels[state], detail, connection: s.connection, awaitingSnapshot: offline, reason: !validClock ? 'invalid-input' : timeout ? 'snapshot-timeout' : s.fences.overflow ? 'fence-overflow' : s.reason ?? (unavailable ? 'stale/unavailable-output' : null), output: fresh ? s.output!.level : 0, peak: fresh ? s.output!.peak : 0, microphone: safe && s.facts.listening && s.microphone && s.microphone.ageMs + now - s.microphone.receivedAt <= LEVEL_MAX_AGE_MS ? s.microphone.level : 0, partial: s.facts.partial, startup: s.facts.startup, apps }
}
