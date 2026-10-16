import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { describe, it } from 'node:test'
import {
  deriveVisualCore as view, emptyFacts, emptyFences, initialVisualCore,
  transitionVisualCore as step, VISUAL_STATES, PRIORITY, MAX_FENCES,
  LEVEL_MAX_AGE_MS, SNAPSHOT_TIMEOUT_MS, LIVENESS_MS, SUCCESS_HOLD_MS, ERROR_HOLD_MS,
  type Facts, type NormalizedSnapshot, type VisualCoreMachine,
} from '../src/components/jarvis/visual-core/visualCoreMachine.ts'

type Spec = typeof import('../../docs/design/reactive-core-spec.json')
const spec: Spec = JSON.parse(readFileSync(new URL('../../docs/design/reactive-core-spec.json', import.meta.url), 'utf8'))
const snapshot = (overrides: Partial<NormalizedSnapshot> = {}): NormalizedSnapshot => ({
  kind: 'snapshot', session: 's1', replacesSession: null, generation: 1, watermark: 0, ageMs: 0,
  facts: emptyFacts(), fences: emptyFences(), output: null, microphone: null, candidate: null,
  successRemainingMs: 0, errorRemainingMs: 0, ...overrides,
})
const live = (facts: Partial<Facts> = {}, overrides: Partial<NormalizedSnapshot> = {}, now = 10000) =>
  step(step(initialVisualCore(now), { kind: 'connection', status: 'connected' }, now), snapshot({ ...overrides, facts: { ...emptyFacts(), ...facts } }), now)
const event = (s: VisualCoreMachine, payload: Record<string, unknown>, now = s.now, receivedAt = now) =>
  step(s, { session: s.session, generation: s.generation, seq: s.watermark + 1, ageMs: 0, ...payload }, now, receivedAt)
const turn = (s = live()) => event(s, { kind: 'turn-start', turn: 't1' })
const playing = (s = turn()) => event(s, { kind: 'playback-start', turn: 't1', playback: 'p1', confirmed: true })
const speaking = (s = playing(), value = 0.5) => event(s, { kind: 'output', turn: 't1', playback: 'p1', level: value, peak: value })
const tick = (s: VisualCoreMachine, dt: number) => step(s, { kind: 'tick' }, s.now + dt)
const state = (s: VisualCoreMachine) => view(s).state
const zero = (s: VisualCoreMachine) => { assert.equal(view(s).output, 0); assert.equal(view(s).peak, 0); assert.equal(view(s).microphone, 0) }

describe('pure visual core: active facts, not historical priority', () => {
  it('reaches exactly the tracked ten states with production transitions', () => {
    const cases = [live(), event(live(), { kind: 'capture', active: true }), turn(), speaking(),
      event(turn(), { kind: 'execution-start', turn: 't1', command: 'c1' }),
      event(turn(), { kind: 'turn-end', turn: 't1', ok: true }),
      event(turn(), { kind: 'turn-end', turn: 't1', ok: false }), initialVisualCore(),
      event(live(), { kind: 'startup', phase: 'acknowledged', partial: false }),
      event(live(), { kind: 'stop' })]
    assert.deepEqual(cases.map(state), VISUAL_STATES)
    assert.deepEqual(VISUAL_STATES, spec.states.map(s => s.id))
    assert.deepEqual(PRIORITY, spec.priority.order)
    assert.equal(LEVEL_MAX_AGE_MS, spec.lifecycle.freshness.levelMaxAgeMs)
    assert.equal(SNAPSHOT_TIMEOUT_MS, spec.lifecycle.snapshot.timeoutMs)
    assert.equal(LIVENESS_MS, spec.lifecycle.freshness.connectionLivenessBudgetMs)
    assert.equal(SUCCESS_HOLD_MS, spec.states.find(s => s.id === 'SUCCESS')!.motion.holdMs)
    assert.equal(ERROR_HOLD_MS, spec.states.find(s => s.id === 'ERROR')!.motion.holdMs)
  })

  it('resolves each overlapping priority then clears only matching work', () => {
    let s = event(live(), { kind: 'capture', active: true })
    s = turn(s)
    s = event(s, { kind: 'execution-start', turn: 't1', command: 'c1' })
    assert.equal(state(s), 'EXECUTING')
    s = speaking(playing(s))
    assert.equal(state(s), 'SPEAKING')
    s = event(s, { kind: 'startup', phase: 'announcing', partial: true })
    assert.equal(state(s), 'STARTING')
    assert.equal(view(s).output, 0.5, 'confirmed welcome can modulate without hiding phase')
    s = event(s, { kind: 'execution-end', turn: 't1', command: 'c1', ok: false })
    assert.equal(state(s), 'ERROR'); zero(s)
    assert.equal(s.facts.executing, null)
    assert.equal(s.facts.thinking, true)
    s = tick(s, 1500)
    assert.equal(state(s), 'STARTING')
    s = event(s, { kind: 'startup', phase: 'ready', partial: false })
    assert.equal(state(s), 'THINKING')
    s = event(s, { kind: 'turn-end', turn: 't1', ok: true })
    assert.equal(state(s), 'LISTENING', 'aggregate success cannot erase tool failure')
    s = event(s, { kind: 'capture', active: false })
    assert.equal(state(s), 'IDLE')
    assert.equal(s.facts.partial, true)
    s = event(s, { kind: 'stop' })
    assert.equal(state(s), 'STOPPING'); zero(s)
    s = step(s, { kind: 'connection', status: 'disconnected' }, s.now)
    assert.equal(state(s), 'DISCONNECTED'); zero(s)
  })

  it('holds use receipt time, expire at exact cutoffs while hidden and never replay duplicates', () => {
    for (const [ok, hold, expected] of [[true, SUCCESS_HOLD_MS, 'SUCCESS'], [false, ERROR_HOLD_MS, 'ERROR']] as const) {
      let s = event(turn(), { kind: 'turn-end', turn: 't1', ok, ageMs: 100 }, 10100, 10090)
      assert.equal(state(s), expected)
      assert.equal(state(tick(s, hold - 11)), expected)
      const until = ok ? s.successUntil : s.errorUntil
      s = event(s, { kind: 'turn-end', turn: 't1', ok }, 10101)
      assert.equal(ok ? s.successUntil : s.errorUntil, until)
      assert.equal(state(step(s, { kind: 'tick' }, 10090 + hold)), 'IDLE')
    }
    let s = event(turn(), { kind: 'turn-end', turn: 't1', ok: true })
    s = event(s, { kind: 'capture', active: true })
    s = tick(s, SUCCESS_HOLD_MS)
    s = event(s, { kind: 'capture', active: false })
    assert.equal(state(s), 'IDLE')
  })

  it('uncorrelated outcomes do not guess error/success or clear current work', () => {
    const s = event(turn(), { kind: 'execution-start', turn: 't1', command: 'c1' })
    for (const payload of [
      { kind: 'turn-end', turn: 'foreign', ok: false },
      { kind: 'execution-end', turn: 't1', command: 'wrong', ok: true },
      { kind: 'playback-end', turn: 't1', playback: 'absent', outcome: 'failed' },
    ]) {
      const n = event(s, payload)
      assert.equal(state(n), 'EXECUTING'); assert.deepEqual(n.facts, s.facts)
    }
  })

  it('persistent startup/device errors require recovery or an authoritative newer generation', () => {
    let s = event(speaking(), { kind: 'playback-end', turn: 't1', playback: 'p1', outcome: 'failed' })
    assert.equal(state(tick(s, 2000)), 'ERROR'); zero(s)
    s = event(s, { kind: 'recover' })
    assert.equal(state(s), 'THINKING')
    s = event(s, { kind: 'startup', phase: 'failed', partial: true })
    assert.equal(state(tick(s, 2000)), 'ERROR')
    s = event(s, { kind: 'turn-start', turn: 't2', generation: 2 })
    assert.equal(state(s), 'THINKING'); assert.equal(s.facts.persistentError, false)
  })
})

describe('identity, ordering and bounded terminal fences', () => {
  it('drops equal/decreasing sequence without freshness or watermark changes', () => {
    const s = speaking()
    for (const seq of [s.watermark, s.watermark - 1, 0]) {
      const n = event(s, { kind: 'output', turn: 't1', playback: 'p1', level: 0.9, peak: 1, seq }, s.now + 200)
      assert.equal(n.watermark, s.watermark); assert.deepEqual(n.output, s.output)
      assert.equal(n.reason, 'stale-sequence')
      assert.equal(state(tick(n, 51)), 'THINKING')
    }
  })

  it('sequence gaps suppress speech until snapshot catches up to observed watermark', () => {
    let s = speaking()
    s = event(s, { kind: 'output', turn: 't1', playback: 'p1', level: 1, peak: 1, seq: 8 })
    assert.equal(s.watermark, 8); assert.equal(s.reason, 'sequence-gap'); zero(s)
    assert.equal(state(s), 'DISCONNECTED')
    s = event(s, { kind: 'heartbeat' })
    assert.equal(s.watermark, 9)
    s = step(s, snapshot({ watermark: 8 }), s.now)
    assert.equal(state(s), 'DISCONNECTED')
    s = step(s, snapshot({ watermark: 9, facts: { ...emptyFacts(), turn: 't1', thinking: true } }), s.now)
    assert.equal(state(s), 'THINKING')
    s = event(s, { kind: 'turn-start', turn: 't1', seq: 9 })
    assert.equal(s.reason, 'stale-sequence')
  })

  it('terminal observations during snapshot reconciliation cannot be lost or resurrected', () => {
    for (const payload of [
      { kind: 'cancel-turn', turn: 't1' },
      { kind: 'stop' },
      { kind: 'startup', phase: 'cancelled', partial: false },
      { kind: 'playback-end', turn: 't1', playback: 'p1', outcome: 'ended' },
    ]) {
      const original = speaking()
      let s = event(original, { ...payload, seq: original.watermark + 2 })
      assert.equal(state(s), 'DISCONNECTED'); zero(s)
      assert.ok(s.fences.cancelled || s.fences.stopped || s.fences.playbacks.includes('p1'))
      s = step(s, snapshot({ watermark: s.watermark, facts: original.facts, output: { level: 1, peak: 1, ageMs: 0 } }), s.now)
      assert.equal(state(s), 'DISCONNECTED'); assert.equal(s.reason, 'terminal-fence'); zero(s)
      s = step(s, snapshot({ watermark: s.watermark + 1, facts: s.facts, fences: s.fences }), s.now)
      assert.notEqual(state(s), 'SPEAKING'); zero(s)
    }
  })

  it('a restarted Core (new session, nothing to replace) installs after reconnect, but never on a healthy link', () => {
    let s = speaking()
    s = step(s, snapshot({ session: 's9', replacesSession: null, watermark: 1 }), s.now)
    assert.equal(s.session, 's1'); assert.equal(s.reason, 'stale-generation/session') // healthy link: foreign session rejected
    s = step(s, { kind: 'connection', status: 'disconnected' }, s.now)
    s = step(s, { kind: 'connection', status: 'connected' }, s.now)
    s = step(s, snapshot({ session: 's2', replacesSession: null, generation: 1, watermark: 0 }), s.now)
    assert.equal(s.session, 's2'); assert.equal(state(s), 'IDLE'); zero(s)
    s = step(s, snapshot({ session: 's3', replacesSession: null, watermark: 5 }), s.now)
    assert.equal(s.session, 's2') // rejoin window closed once a snapshot installed
  })
  it('foreign-session traffic never installs authority or poisons the current-session watermark', () => {
    let s = speaking()
    const wm = s.watermark
    s = event(s, { kind: 'turn-start', session: 'foreign', seq: 999, generation: 99, turn: 'evil' })
    assert.equal(s.session, 's1'); assert.equal(s.watermark, wm)
    s = step(s, snapshot({ session: 's2', replacesSession: 's1', generation: 0, watermark: 0 }), s.now)
    assert.equal(state(s), 'IDLE'); zero(s)
    s = event(s, { kind: 'output', session: 's1', seq: 999, turn: 't1', playback: 'p1', level: 1, peak: 1 })
    assert.equal(s.watermark, 0); assert.equal(s.session, 's2'); zero(s)
    s = step(s, snapshot({ session: 's1', replacesSession: null, watermark: 999 }), s.now)
    assert.equal(s.session, 's2'); assert.equal(state(s), 'DISCONNECTED')
  })

  it('a newer generation resets old work and samples; old/future audio cannot establish one', () => {
    let s = speaking()
    s = event(s, { kind: 'turn-start', generation: 2, turn: 't2' })
    assert.equal(s.generation, 2); assert.equal(s.facts.playback, null); zero(s)
    for (const generation of [1, 3]) {
      s = event(s, { kind: 'output', turn: 't1', playback: 'p1', generation, level: 1, peak: 1 })
      assert.equal(s.generation, 2); assert.equal(state(s), 'THINKING'); zero(s)
    }
    s = event(s, { kind: 'turn-start', turn: 'another', generation: 2 })
    assert.equal(s.facts.turn, 't2')
  })

  it('mid-playback cancellation fences the entire turn, including new playback IDs and outcomes', () => {
    let s = event(speaking(), { kind: 'cancel-turn', turn: 't1' })
    assert.equal(state(s), 'IDLE'); zero(s)
    for (const payload of [
      { kind: 'output', turn: 't1', playback: 'p1', level: 1, peak: 1 },
      { kind: 'playback-start', turn: 't1', playback: 'p2', confirmed: true },
      { kind: 'turn-end', turn: 't1', ok: true },
      { kind: 'turn-end', turn: 't1', ok: false },
      { kind: 'turn-start', turn: 'other' },
    ]) {
      const wm = s.watermark
      s = event(s, payload)
      assert.equal(s.watermark, wm + 1, 'fenced valid envelopes still observed')
      assert.equal(s.reason, 'terminal-fence'); assert.equal(state(s), 'IDLE'); zero(s)
    }
    s = step(s, snapshot({ watermark: s.watermark, facts: { ...emptyFacts(), turn: 't1', thinking: true } }), s.now)
    assert.equal(state(s), 'DISCONNECTED'); assert.equal(s.reason, 'terminal-fence')
    s = step(s, snapshot({ watermark: s.watermark }), s.now)
    assert.equal(s.fences.cancelled, true, 'snapshot omission cannot erase a tombstone')
    s = event(s, { kind: 'turn-start', generation: 2, turn: 't2' })
    assert.equal(state(s), 'THINKING'); assert.equal(s.fences.cancelled, false)
    s = event(s, { kind: 'cancel-turn', generation: 1, turn: 't1' })
    assert.equal(s.facts.turn, 't2'); assert.equal(s.fences.cancelled, false)
  })

  it('session stop survives higher generation snapshots and clears only through authoritative cleanup', () => {
    let s = event(speaking(), { kind: 'stop' })
    assert.equal(state(tick(s, 10000)), 'STOPPING'); zero(s)
    s = event(s, { kind: 'turn-start', generation: 2, turn: 't2' })
    assert.equal(state(s), 'STOPPING')
    s = step(s, snapshot({ watermark: s.watermark, generation: 2, facts: { ...emptyFacts(), turn: 't2', thinking: true } }), s.now)
    assert.equal(state(s), 'DISCONNECTED')
    s = step(s, snapshot({ watermark: s.watermark + 1, generation: 2, fences: { ...emptyFences(), stopped: true, cleanedUp: true } }), s.now)
    assert.equal(state(s), 'IDLE'); zero(s)
    s = event(s, { kind: 'turn-start', generation: 3, turn: 't3' })
    assert.equal(state(s), 'IDLE'); assert.equal(s.fences.stopped, true)
    s = step(s, snapshot({ session: 's2', replacesSession: 's1' }), s.now)
    assert.equal(s.fences.stopped, false)
    assert.equal(state(turn(s)), 'THINKING')
  })

  it('a higher-generation snapshot clears the previous stop fence (new activation after cleanup)', () => {
    let s = event(speaking(), { kind: 'stop' })
    s = step(s, snapshot({ watermark: s.watermark + 1, generation: 2, fences: { ...emptyFences(), stopped: true, cleanedUp: true } }), s.now)
    assert.equal(s.fences.stopped, true)
    s = step(s, snapshot({ watermark: s.watermark + 1, generation: 3 }), s.now)
    assert.equal(s.fences.stopped, false); assert.equal(state(s), 'IDLE')
  })

  it('candidate expiry is scoped, deadline bounded, and cannot stop unrelated work', () => {
    let s = event(turn(), { kind: 'candidate', candidate: 'a', active: true, remainingMs: 100 })
    assert.equal(state(s), 'STARTING')
    s = event(s, { kind: 'candidate', candidate: 'a', active: true, remainingMs: 2000 }, s.now + 99)
    assert.equal(state(s), 'STARTING')
    s = tick(s, 1)
    assert.equal(state(s), 'THINKING'); assert.equal(s.facts.turn, 't1')
    assert.equal(s.fences.stopped, false); assert.deepEqual(s.fences.candidates, ['a'])
    s = event(s, { kind: 'candidate', candidate: 'a', active: true, remainingMs: 100 })
    assert.equal(state(s), 'THINKING'); assert.equal(s.reason, 'terminal-fence')
    s = event(s, { kind: 'candidate', candidate: 'b', active: true, remainingMs: 0 })
    assert.equal(state(s), 'THINKING'); assert.ok(s.fences.candidates.includes('b'))
    s = event(s, { kind: 'candidate', candidate: 'c', active: true, remainingMs: 100 })
    s = event(s, { kind: 'candidate', candidate: 'unrelated', active: false, remainingMs: 0 })
    assert.equal(s.candidate?.id, 'c')
  })

  it('ended/replaced playbacks cannot resurrect, while another same-generation chunk may start', () => {
    let s = speaking()
    s = event(s, { kind: 'playback-start', turn: 't1', playback: 'p2', confirmed: true })
    zero(s); assert.ok(s.fences.playbacks.includes('p1'))
    s = event(s, { kind: 'output', turn: 't1', playback: 'p1', level: 1, peak: 1 })
    zero(s)
    s = event(s, { kind: 'output', turn: 't1', playback: 'p2', level: 0.4, peak: 0.8 })
    assert.equal(state(s), 'SPEAKING')
    s = event(s, { kind: 'playback-end', turn: 't1', playback: 'p2', outcome: 'cancelled' })
    zero(s)
    s = event(s, { kind: 'playback-start', turn: 't1', playback: 'p2', confirmed: true })
    assert.equal(s.reason, 'terminal-fence'); zero(s)
    s = event(s, { kind: 'playback-start', turn: 't1', playback: 'p3', confirmed: true })
    assert.equal(s.facts.playback?.id, 'p3')
  })

  it('fence storage fails closed at the shared generation bound, never evicts safety data', () => {
    let s = turn()
    for (let i = 0; i < MAX_FENCES + 1; i++) {
      s = event(s, { kind: 'candidate', candidate: `a${i}`, active: false, remainingMs: 0 })
    }
    assert.equal(s.fences.candidates.length, MAX_FENCES)
    assert.equal(s.fences.overflow, true); zero(s)
    assert.ok(s.fences.candidates.includes('a0'))
    s = event(s, { kind: 'playback-start', turn: 't1', playback: 'late', confirmed: true })
    assert.equal(s.facts.playback, null); assert.equal(s.reason, 'fence-overflow')
    s = step(s, snapshot({ watermark: s.watermark }), s.now)
    assert.equal(s.fences.overflow, true)
    s = event(s, { kind: 'turn-start', generation: 2, turn: 't2' })
    assert.equal(state(s), 'THINKING'); assert.equal(s.fences.candidates.length, 0)
  })
})

describe('authoritative snapshot and clock domains', () => {
  it('snapshot omission cannot erase a turn identity or revive a removed playback/command', () => {
    let s = event(speaking(), { kind: 'execution-start', turn: 't1', command: 'c1' })
    s = step(s, snapshot({ watermark: s.watermark + 1 }), s.now)
    assert.equal(state(s), 'IDLE'); assert.equal(s.facts.turn, 't1')
    assert.equal(s.fences.turnEnded, true)
    assert.ok(s.fences.playbacks.includes('p1')); assert.ok(s.fences.commands.includes('c1'))
    for (const payload of [
      { kind: 'turn-start', turn: 't1' }, { kind: 'turn-start', turn: 't2' },
      { kind: 'execution-start', turn: 't1', command: 'c1' },
      { kind: 'playback-start', turn: 't1', playback: 'p1', confirmed: true },
    ]) { s = event(s, payload); assert.equal(state(s), 'IDLE'); zero(s) }
    s = step(s, snapshot({ generation: 2, watermark: s.watermark, facts: { ...emptyFacts(), turn: 't2', thinking: true } }), s.now)
    assert.equal(state(s), 'DISCONNECTED', 'generation advance needs advancing session watermark')
  })

  it('equal-watermark snapshots cannot invent activity, or re-age a candidate deadline', () => {
    let s = live()
    s = step(s, snapshot({ facts: { ...emptyFacts(), turn: 't1', thinking: true } }), s.now)
    assert.equal(state(s), 'DISCONNECTED')
    s = event(live(), { kind: 'candidate', candidate: 'a', active: true, remainingMs: 100 })
    const original = s.candidate!.until
    s = step(s, snapshot({ watermark: s.watermark + 1, candidate: { id: 'a', remainingMs: 2000 } }), s.now + 99)
    assert.equal(s.candidate?.until, original)
    assert.equal(state(tick(s, 1)), 'IDLE')
    s = event(live(), { kind: 'candidate', candidate: 'a', active: true, remainingMs: 100 })
    s = event(s, { kind: 'startup', phase: 'ready', partial: false })
    s = event(s, { kind: 'candidate', candidate: 'a', active: true, remainingMs: 100 })
    assert.equal(state(s), 'IDLE'); assert.equal(s.reason, 'terminal-fence')
  })

  it('snapshot fence unions overflow closed without evicting existing terminal identities', () => {
    const names = Array.from({ length: MAX_FENCES }, (_, i) => `p${i}`)
    let s = live({}, { fences: { ...emptyFences(), playbacks: names } })
    s = step(s, snapshot({ watermark: 1, fences: { ...emptyFences(), playbacks: ['new'] } }), s.now)
    assert.equal(s.fences.overflow, true); assert.equal(view(s).reason, 'fence-overflow')
    assert.deepEqual(s.fences.playbacks, names); zero(s)
    s = event(s, { kind: 'stop' })
    assert.equal(state(s), 'STOPPING', 'session stop still applies during overflow')
  })

  it('stop after turn cancellation fences the session, including duplicate stop after cleanup', () => {
    let s = event(speaking(), { kind: 'cancel-turn', turn: 't1' })
    s = event(s, { kind: 'stop' })
    assert.equal(state(s), 'STOPPING')
    s = step(s, snapshot({ watermark: s.watermark + 1, fences: { ...emptyFences(), stopped: true, cleanedUp: true } }), s.now)
    assert.equal(state(s), 'IDLE')
    s = event(s, { kind: 'stop' })
    assert.equal(state(s), 'IDLE'); assert.equal(s.fences.stopped, true)
    s = event(s, { kind: 'turn-start', generation: 2, turn: 't2' })
    assert.equal(state(s), 'IDLE')
  })

  it('connect alone and invalid/missing snapshots stay Offline through the exact timeout', () => {
    let s = step(initialVisualCore(), { kind: 'connection', status: 'connected' }, 0)
    assert.equal(state(s), 'DISCONNECTED')
    s = event(s, { kind: 'turn-start', session: 'uninstalled', turn: 't1' })
    assert.equal(s.session, null)
    s = step(s, { kind: 'connection', status: 'connected' }, 1999)
    assert.notEqual(view(s).detail, 'State unavailable')
    s = step(s, { kind: 'snapshot' }, 2000)
    assert.equal(view(s).detail, 'State unavailable'); zero(s)
    s = step(s, snapshot(), 2001)
    assert.equal(state(s), 'IDLE')
  })

  it('reconnect restores phase/apps/fences atomically; no action or control outputs exist', () => {
    let s = event(live(), { kind: 'startup', phase: 'announcing', partial: true })
    s = step(s, { kind: 'connection', status: 'disconnected' }, s.now)
    s = step(s, { kind: 'connection', status: 'connected' }, s.now + 10)
    assert.equal(state(s), 'DISCONNECTED')
    const snap = snapshot({ watermark: 20, facts: { ...emptyFacts(), startup: 'announcing', partial: true, apps: [{ id: 'editor', status: 'pending' }, { id: 'browser', status: 'failed' }] } })
    s = step(s, snap, s.now)
    assert.equal(state(s), 'STARTING'); assert.equal(s.facts.startup, 'announcing'); zero(s)
    assert.deepEqual(s.facts.apps, snap.facts.apps)
    s = event(s, { kind: 'startup', phase: 'acknowledged', partial: false, seq: 19 })
    assert.equal(s.facts.startup, 'announcing')
    assert.equal('actions' in s, false)
  })

  it('snapshot transit and authoritative sample age accumulate without comparing remote clock origins', () => {
    const facts = { ...emptyFacts(), turn: 't1', thinking: true, playback: { id: 'p1', turn: 't1', confirmed: true as const } }
    const snap = snapshot({ facts, ageMs: 100, output: { level: 0.7, peak: 1, ageMs: 150 } })
    const base = step(initialVisualCore(1_000_000), { kind: 'connection', status: 'connected' }, 1_000_000)
    const s = step(base, snap, 1_000_010, 1_000_000)
    assert.equal(state(s), 'THINKING'); zero(s)
    assert.equal(s.output, null)
    const boundary = step(base, snap, 1_000_000)
    assert.equal(state(boundary), 'SPEAKING')
    assert.equal(view(boundary).output, 0.7)
    assert.equal(state(tick(boundary, 1)), 'THINKING')
  })

  it('same-watermark snapshots cannot refresh ages/holds; rollback or identity reset stays disconnected', () => {
    let s = speaking()
    const old = s
    s = step(s, snapshot({ watermark: s.watermark, facts: s.facts, output: { level: 0.9, peak: 1, ageMs: 0 } }), s.now + 200)
    assert.equal(s.output?.ageMs, 200)
    assert.equal(state(tick(s, 51)), 'THINKING')
    for (const snap of [snapshot({ watermark: old.watermark - 1 }), snapshot({ generation: 0, watermark: old.watermark + 1 }), snapshot({ watermark: old.watermark + 1, facts: { ...old.facts, turn: 'different', playback: null } }), snapshot({ ageMs: LIVENESS_MS + 1 })]) {
      const n = step(old, snap, old.now)
      assert.equal(n.session, old.session); assert.equal(n.generation, old.generation)
      assert.equal(state(n), 'DISCONNECTED'); zero(n)
    }
    const done = event(turn(), { kind: 'turn-end', turn: 't1', ok: true })
    const restored = step(done, snapshot({ watermark: done.watermark, facts: done.facts, fences: done.fences, successRemainingMs: 900 }), done.now + 800)
    assert.equal(restored.successUntil, done.successUntil)
    assert.equal(state(tick(restored, 100)), 'IDLE')
  })

  it('liveness boundary requires reconciliation, while silence alone does not', () => {
    const s = turn()
    assert.equal(state(tick(s, LIVENESS_MS)), 'THINKING')
    let n = tick(s, LIVENESS_MS + 1)
    assert.equal(state(n), 'DISCONNECTED'); zero(n)
    n = event(n, { kind: 'heartbeat' })
    assert.equal(state(n), 'DISCONNECTED')
    n = step(n, snapshot({ watermark: n.watermark, facts: s.facts }), n.now)
    assert.equal(state(n), 'THINKING')
  })
})

describe('unknown-input validation and measured-output qualification', () => {
  it('raw v1 speech/voice events never become normalized output', () => {
    for (const input of [
      { v: 1, ts: 1, mono_ms: 1, name: 'speech.started', trace_id: 't1', source: 'cache' },
      { v: 1, ts: 1, mono_ms: 1, name: 'speech.completed', trace_id: 't1', cancelled: false },
      { v: 1, ts: 1, mono_ms: 1, name: 'voice.state', state: 'speaking' },
    ]) {
      const s = step(turn(), input, 10000)
      assert.equal(state(s), 'THINKING'); zero(s); assert.equal(s.reason, 'invalid-input')
    }
    let s = event(turn(), { kind: 'generated', turn: 't1', active: true })
    assert.equal(state(s), 'THINKING'); assert.equal(view(s).detail, 'Speech prepared; output unconfirmed')
    s = speaking(playing(s))
    s = event(s, { kind: 'generated', turn: 't1', active: false })
    assert.equal(state(s), 'SPEAKING', 'generated completion cannot end actual output')
  })

  it('device confirmation, identity and fresh finite measurement are all required; zero is valid', () => {
    let s = turn()
    s = event(s, { kind: 'output', turn: 't1', playback: 'p1', level: 1, peak: 1 })
    assert.equal(state(s), 'THINKING'); zero(s)
    s = event(s, { kind: 'playback-start', turn: 't1', playback: 'p1', confirmed: false })
    assert.equal(s.facts.playback, null)
    s = playing(s)
    assert.equal(state(s), 'THINKING'); zero(s)
    s = speaking(s, 0)
    assert.equal(state(s), 'SPEAKING'); zero(s)
    s = event(s, { kind: 'microphone', level: 1, peak: 1 })
    assert.equal(view(s).output, 0)
    s = event(s, { kind: 'playback-end', turn: 't1', playback: 'p1', outcome: 'ended' })
    assert.equal(state(s), 'THINKING'); zero(s)
  })

  it('malformed levels never refresh last-valid output, liveness, or crash; finite extremes clamp', () => {
    const original = speaking()
    let s = original
    for (const value of [NaN, Infinity, -Infinity, '0.5', null, undefined]) {
      s = event(s, { kind: 'output', turn: 't1', playback: 'p1', level: value, peak: 1 }, s.now + 20)
      assert.deepEqual(s.output, original.output); assert.equal(s.lastLiveAt, original.lastLiveAt)
      assert.equal(s.reason, 'invalid-input')
    }
    assert.equal(state(tick(s, 131)), 'THINKING')
    for (const [level, expected] of [[-0.2, 0], [1.7, 1]]) {
      const n = event(original, { kind: 'output', turn: 't1', playback: 'p1', level, peak: level })
      assert.equal(view(n).output, expected); assert.equal(view(n).peak, expected); assert.equal(n.reason, 'level-invalid')
    }
  })

  it('delayed amplitude is not made fresh on receipt, and cannot replace newer data', () => {
    const s = speaking()
    let n = event(s, { kind: 'output', turn: 't1', playback: 'p1', level: 1, peak: 1, ageMs: 201 }, s.now + 100)
    assert.deepEqual(n.output, s.output)
    n = event(n, { kind: 'output', turn: 't1', playback: 'p1', level: 1, peak: 1, ageMs: 251 }, s.now + 251)
    assert.equal(state(n), 'THINKING'); zero(n)
    assert.equal(state(tick(s, 250)), 'SPEAKING')
    assert.equal(state(tick(s, 251)), 'THINKING')
  })

  const badValues = ['', ' ', 'x'.repeat(257), null, 5]
  for (const field of ['session', 'turn', 'playback']) {
    for (const value of badValues) it(`rejects invalid ${field} ${String(value).slice(0, 12)}`, () => {
      const s = speaking()
      const n = event(s, { kind: 'output', turn: 't1', playback: 'p1', level: 1, peak: 1, [field]: value })
      assert.deepEqual(n.output, s.output); assert.equal(n.reason, 'invalid-input')
    })
  }
  for (const field of ['generation', 'seq', 'ageMs']) {
    for (const value of [-1, NaN, Infinity, -Infinity, Number.MAX_SAFE_INTEGER + 1, '1', null]) it(`rejects invalid ${field} ${value}`, () => {
      const s = speaking()
      const n = event(s, { kind: 'output', turn: 't1', playback: 'p1', level: 1, peak: 1, [field]: value })
      assert.deepEqual(n.output, s.output); assert.equal(n.reason, 'invalid-input')
    })
  }

  it('rejects fractional sequence/generation, unknown kinds, booleans, and clock reversals', () => {
    const s = speaking()
    for (const input of [null, [], false, {}, { kind: 'warp' },
      { kind: 'turn-start', session: 's1', generation: 1.5, seq: 4, ageMs: 0, turn: 't1' },
      { kind: 'turn-start', session: 's1', generation: 1, seq: 4.5, ageMs: 0, turn: 't1' },
      { kind: 'capture', session: 's1', generation: 1, seq: 4, ageMs: 0, active: 'yes' },
    ]) { const n = step(s, input, s.now); assert.deepEqual(n.facts, s.facts); assert.deepEqual(n.output, s.output) }
    for (const time of [NaN, Infinity, -1, s.now - 1]) {
      const n = step(s, { kind: 'tick' }, time)
      assert.equal(n.reason, 'invalid-input'); assert.equal(n.now, s.now); zero(n)
      assert.equal(view(s, time).state, 'DISCONNECTED')
    }
    assert.equal(step(s, { kind: 'tick' }, s.now, s.now + 1).reason, 'invalid-input')
    assert.throws(() => initialVisualCore(NaN), RangeError)
  })

  it('malformed snapshot fields fail atomically, including sparse arrays and missing terminal fields', () => {
    const s = speaking()
    const bad = [
      { facts: { ...s.facts, thinking: 'yes' } }, { facts: { ...s.facts, turn: null } },
      { facts: { ...s.facts, apps: new Array(1) } }, { facts: { ...s.facts, apps: [{ id: 'a', status: 'unknown' }] } },
      { fences: { ...emptyFences(), playbacks: new Array(1) } }, { fences: { ...emptyFences(), cancelled: undefined } },
      { fences: { ...emptyFences(), playbacks: ['p', 'p'] } }, { fences: { ...emptyFences(), cleanedUp: true } },
      { ageMs: NaN }, { ageMs: -1 }, { output: { level: NaN, peak: 1, ageMs: 0 } },
      { output: { level: 1, peak: Infinity, ageMs: 0 } }, { output: { level: 1, peak: 1, ageMs: -1 } },
      { candidate: { id: 'a', remainingMs: 2001 } }, { successRemainingMs: 901 }, { errorRemainingMs: 1501 },
    ]
    for (const overrides of bad) {
      const n = step(s, { ...snapshot({ watermark: 20, facts: s.facts }), ...overrides }, s.now)
      assert.equal(state(n), 'DISCONNECTED'); assert.equal(n.watermark, s.watermark)
      assert.deepEqual(n.facts, s.facts); zero(n)
    }
  })

  it('projects snapshot fields, retains no unknown payloads and never aliases caller-owned data', () => {
    const snap = snapshot({ facts: { ...emptyFacts(), apps: [{ id: 'editor', status: 'pending' }] } })
    Reflect.set(snap.facts, 'irrelevant', () => 'not cloneable')
    const s = step(live(), snap, 10000)
    assert.equal(state(s), 'IDLE'); assert.equal('irrelevant' in s.facts, false)
    Reflect.set(snap.facts.apps![0]!, 'status', 'failed')
    assert.equal(s.facts.apps![0]!.status, 'pending')
  })

  it('keeps capture samples distinct from output, including expiry and nonfinite microphone data', () => {
    let s = event(live(), { kind: 'capture', active: true })
    s = event(s, { kind: 'microphone', level: 0.8, peak: 0.9 })
    assert.equal(state(s), 'LISTENING'); assert.equal(view(s).output, 0); assert.equal(view(s).microphone, 0.8)
    const original = s.microphone
    s = event(s, { kind: 'microphone', level: Infinity, peak: 1 }, s.now + 250)
    assert.deepEqual(s.microphone, original); assert.equal(view(s).microphone, 0.8)
    s = tick(s, 1)
    assert.equal(state(s), 'LISTENING'); zero(s)
    s = event(s, { kind: 'capture', active: false })
    assert.equal(state(s), 'IDLE')
  })

  it('all transport failures immediately zero both channels and need a new snapshot', () => {
    for (const status of ['connecting', 'disconnected', 'auth-error', 'incompatible']) {
      const s = speaking(event(event(turn(), { kind: 'capture', active: true }), { kind: 'microphone', level: 0.8, peak: 1 }))
      const n = step(s, { kind: 'connection', status }, s.now)
      assert.equal(state(n), 'DISCONNECTED'); zero(n)
      assert.equal(n.output, null); assert.equal(n.microphone, null)
      const connected = step(n, { kind: 'connection', status: 'connected' }, n.now)
      assert.equal(state(connected), 'DISCONNECTED'); zero(connected)
    }
  })

  it('accepts zero and max safe identities/order but never unsafe increment or history growth', () => {
    const base = live({}, { generation: 0, watermark: 0 }, 0)
    assert.equal(state(base), 'IDLE')
    let s = event(base, { kind: 'turn-start', turn: 't0', generation: 0, seq: 1 })
    assert.equal(state(s), 'THINKING')
    s = step(s, snapshot({ generation: Number.MAX_SAFE_INTEGER, watermark: Number.MAX_SAFE_INTEGER }), 0)
    assert.equal(s.generation, Number.MAX_SAFE_INTEGER); assert.equal(s.watermark, Number.MAX_SAFE_INTEGER)
    const n = event(s, { kind: 'turn-start', turn: 'overflow' })
    assert.equal(n.reason, 'invalid-input'); assert.equal(n.watermark, s.watermark)
  })

  it('100000 same-playback samples retain one slot, fixed facts and no growing history', () => {
    let s = playing()
    const facts = s.facts
    const keys = Object.keys(s)
    for (let i = 0; i < 100000; i++) s = event(s, { kind: 'output', turn: 't1', playback: 'p1', level: i % 2, peak: 1 })
    assert.equal(s.facts, facts); assert.deepEqual(Object.keys(s), keys)
    assert.equal(s.output?.level, 1); assert.equal(s.fences.playbacks.length, 0)
    assert.equal(s.rejected, 0); assert.equal(state(s), 'SPEAKING')
  })
})

// Tracked design oracle -> actual production transitions. Gate/envelope arithmetic
// belongs to S05, not a second implementation hidden in these tests.
describe('machine-owned adversarial traces use tracked JSON expectations', () => {
  for (const id of ['duplicate-seq', 'out-of-order']) it(`${id}: tracked stimuli preserve latest measurement`, () => {
    const trace = spec.traces.find(t => t.id === id)!
    let s = live({ turn: 't1', thinking: true, playback: { id: 'p1', turn: 't1', confirmed: true } }, { watermark: trace.given!.seq!, output: { level: trace.given!.level!, peak: 1, ageMs: 0 } })
    const original = s.output
    const accepted: number[] = []
    for (const stimulus of trace.steps) {
      const prior = s.output
      s = event(s, { kind: 'output', turn: 't1', playback: 'p1', seq: stimulus.seq!, level: stimulus.level!, peak: 1 }, s.now + 10)
      if (s.output !== prior) accepted.push(stimulus.seq!)
    }
    assert.deepEqual(accepted, trace.expect.acceptedSequences)
    assert.deepEqual(s.output, original)
    assert.equal(view(s).output, trace.expect.level)
  })

  it('invalid-level: tracked nonfinite encodings reject and finite extremes clamp', () => {
    const trace = spec.traces.find(t => t.id === 'invalid-level')!
    let s = speaking()
    const values: number[] = []
    for (const stimulus of trace.steps) {
      const value = stimulus.levelEncoding ? ({ NaN, '+Infinity': Infinity, '-Infinity': -Infinity })[stimulus.levelEncoding as 'NaN' | '+Infinity' | '-Infinity'] : stimulus.level!
      s = event(s, { kind: 'output', turn: 't1', playback: 'p1', level: value, peak: 1 })
      if (!stimulus.levelEncoding) values.push(view(s).output)
    }
    assert.equal(s.rejected, trace.expect.rejectedCount)
    assert.deepEqual(values, trace.expect.boundedValues)
  })

  const traces: Record<string, () => VisualCoreMachine> = {
    'generated-not-output': () => event(turn(), { kind: 'generated', turn: 't1', active: true }),
    'stale-level': () => tick(speaking(), 251),
    'disconnect-during-speaking': () => event(step(speaking(), { kind: 'connection', status: 'disconnected' }, 10000), { kind: 'output', turn: 't1', playback: 'p1', level: 0.8, peak: 1 }),
    'cancel-late-amplitude': () => {
      let s = speaking(live({ turn: 't1', thinking: true, playback: { id: 'p1', turn: 't1', confirmed: true } }, { watermark: 7 }))
      s = event(s, { kind: 'cancel-turn', turn: 't1' })
      s = event(s, { kind: 'output', turn: 't1', playback: 'p1', level: 0.8, peak: 1 })
      s = event(s, { kind: 'playback-start', turn: 't1', playback: 'p2', confirmed: true })
      return event(s, { kind: 'turn-end', turn: 't1', ok: true })
    },
    'session-replacement': () => event(step(speaking(), snapshot({ session: 'b', replacesSession: 's1', watermark: 1 }), 10000), { kind: 'output', session: 's1', seq: 999, turn: 't1', playback: 'p1', level: 0.9, peak: 1 }),
    'reconnect-snapshot': () => {
      let s = event(live(), { kind: 'startup', phase: 'announcing', partial: false })
      s = step(s, { kind: 'connection', status: 'disconnected' }, s.now)
      s = step(s, { kind: 'connection', status: 'connected' }, s.now)
      s = step(s, snapshot({ watermark: 20, facts: { ...emptyFacts(), startup: 'announcing' } }), s.now)
      return event(s, { kind: 'startup', phase: 'acknowledged', partial: false, seq: 19 })
    },
    'snapshot-missing': () => tick(step(initialVisualCore(), { kind: 'connection', status: 'connected' }, 0), 2001),
    'startup-muted': () => event(live(), { kind: 'startup', phase: 'announcing', partial: false }),
    'startup-interrupted': () => event(event(live(), { kind: 'startup', phase: 'cancelled', partial: false }), { kind: 'output', turn: 't1', playback: 'p1', level: 0.7, peak: 1 }),
    'candidate-expired': () => event(event(live(), { kind: 'candidate', candidate: 'a', active: true, remainingMs: 100 }), { kind: 'candidate', candidate: 'a', active: false, remainingMs: 0 }),
    'startup-failed': () => event(live(), { kind: 'startup', phase: 'failed', partial: false }),
    'command-failed-turn-handled': () => event(event(event(turn(), { kind: 'execution-start', turn: 't1', command: 'c1' }), { kind: 'execution-end', turn: 't1', command: 'c1', ok: false }), { kind: 'turn-end', turn: 't1', ok: true }),
  }
  for (const [id, run] of Object.entries(traces)) it(id, () => {
    const expected = spec.traces.find(t => t.id === id)!.expect
    const actual = view(run())
    assert.equal(actual.state, expected.state, id)
    if (expected.level !== undefined) assert.equal(actual.output, expected.level, id)
    if (expected.text !== undefined) assert.equal(actual.detail, expected.text, id)
  })
  it('startup-partial retains limitations while progress completes, never invents app success', () => {
    const expected = spec.traces.find(t => t.id === 'startup-partial')!.expect
    let s = event(live(), { kind: 'startup', phase: 'announcing', partial: true })
    const states = [state(s)]
    s = event(s, { kind: 'startup', phase: 'degraded', partial: true })
    states.push(state(s))
    assert.deepEqual(states, expected.statesAfterEach)
    assert.equal(view(s).detail, expected.text)
    assert.equal(s.facts.apps, null)
  })
})
