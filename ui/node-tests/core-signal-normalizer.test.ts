import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { describe, it } from 'node:test'
import { createVisualCoreService, type VisualCoreScheduler } from '../src/services/visual-core-service.ts'
import { normalizeCoreEvent } from '../src/services/core-signal-normalizer.ts'

type Wire = Record<string, unknown> & { name: string; seq?: number; kind?: string }
const fixture: Wire[] = JSON.parse(readFileSync(new URL('./fixtures/core-signals.json', import.meta.url), 'utf8'))
const signal = (seq: number): Wire => fixture.find(e => e.name === 'core.signal' && e.seq === seq)!
const snapshots = fixture.filter(e => e.name === 'core.snapshot')

function harness() {
  const clock = { t: 10000 }
  const timers = new Map<number, { cb: () => void; at: number }>()
  let nextId = 1
  const scheduler: VisualCoreScheduler = {
    set(cb, delay) { const id = nextId++; timers.set(id, { cb, at: clock.t + delay }); return id },
    clear(h) { timers.delete(h as number) },
  }
  const svc = createVisualCoreService({ clock: () => clock.t, scheduler })
  const advance = (ms: number) => {
    clock.t += ms
    for (const [id, t] of [...timers]) if (t.at <= clock.t) { timers.delete(id); t.cb() }
  }
  const feed = (wire: unknown) => {
    const input = normalizeCoreEvent(wire)
    return input === null ? null : svc.ingest(input, clock.t)
  }
  const play = (from: number, to: number) => {
    for (let s = from; s <= to; s++) { advance(10); assert.equal(feed(signal(s)), true, `seq ${s} (${signal(s).kind})`) }
  }
  svc.setConnection('connected')
  assert.equal(feed(snapshots[0]), true)
  return { svc, advance, feed, play, clock }
}

describe('core signal normalizer + fixture replay', () => {
  it('drives IDLE -> LISTENING -> SPEAKING and expires stale output', () => {
    const h = harness()
    assert.equal(h.svc.getSnapshot().state, 'IDLE')
    h.play(1, 2)
    assert.equal(h.svc.getSnapshot().state, 'LISTENING')
    assert.ok(h.svc.readSignals().microphone > 0)
    h.play(3, 6)
    assert.equal(h.svc.getSnapshot().state, 'SPEAKING')
    assert.ok(h.svc.readSignals().output > 0)
    h.play(7, 8)
    h.play(9, 9)
    assert.notEqual(h.svc.getSnapshot().state, 'SPEAKING', 'playback ended')
    assert.equal(h.svc.readSignals().output, 0)
    // fresh output then silence: stale after 250 ms
    const h2 = harness()
    h2.play(1, 6)
    assert.equal(h2.svc.getSnapshot().state, 'SPEAKING')
    h2.advance(300)
    assert.notEqual(h2.svc.getSnapshot().state, 'SPEAKING')
    assert.equal(h2.svc.readSignals().output, 0)
  })

  it('holds SUCCESS after turn-end ok', () => {
    const h = harness()
    h.play(1, 10)
    assert.equal(h.svc.getSnapshot().state, 'SUCCESS')
    h.advance(1000)
    assert.equal(h.svc.getSnapshot().state, 'IDLE')
  })

  it('cancel stops speaking immediately and rejects the late output', () => {
    const h = harness()
    h.play(1, 13)
    assert.equal(h.svc.getSnapshot().state, 'SPEAKING')
    h.play(15, 15)
    assert.notEqual(h.svc.getSnapshot().state, 'SPEAKING')
    assert.equal(h.svc.readSignals().output, 0)
    const rejected = h.svc.getDiagnostics().rejected
    const late = { ...signal(13), seq: 16 }
    h.advance(10)
    assert.equal(h.feed(late), false)
    assert.equal(h.svc.getDiagnostics().rejected, rejected + 1)
    assert.notEqual(h.svc.getSnapshot().state, 'SPEAKING')
    assert.equal(h.svc.readSignals().output, 0)
  })

  it('replays the whole fixture including implicit welcome turn and final snapshot', () => {
    const h = harness()
    h.play(1, 20)
    assert.equal(h.feed(snapshots[1]), true)
    assert.equal(h.svc.getDiagnostics().rejected, 0)
  })

  it('rejects duplicate and out-of-order seq', () => {
    const h = harness()
    h.play(1, 2)
    assert.equal(h.feed(signal(2)), false)
    assert.equal(h.feed(signal(1)), false)
    assert.equal(h.svc.getDiagnostics().rejected, 2)
  })

  it('enters awaitingSnapshot on a dropped seq and recovers on a snapshot', () => {
    const h = harness()
    h.play(1, 2)
    h.advance(10)
    assert.equal(h.feed(signal(4)), false)
    assert.equal(h.svc.getSnapshot().awaitingSnapshot, true)
    assert.equal(h.feed(snapshots[1]), true)
    assert.equal(h.svc.getSnapshot().awaitingSnapshot, false)
  })

  it('maps legacy and unknown events to null', () => {
    for (const e of [{ name: 'speech.started' }, { name: 'voice.state', state: 'speaking' }, { name: 'core.signal', session: 's', generation: 1, seq: 1, age_ms: 0, kind: 'made-up' }, null, 'x', 7, []]) {
      assert.equal(normalizeCoreEvent(e), null)
    }
  })

  it('does not report SPEAKING when disconnected', () => {
    const h = harness()
    h.play(1, 6)
    assert.equal(h.svc.getSnapshot().state, 'SPEAKING')
    h.svc.setConnection('disconnected')
    assert.equal(h.svc.getSnapshot().state, 'DISCONNECTED')
  })

  it('malformed wire yields null', () => {
    const base = signal(6)
    const { session: _s, ...noSession } = base
    for (const bad of [noSession, { ...base, seq: '6' }, { ...base, level: NaN }, { ...base, playback: 3 }, { ...base, age_ms: Infinity }, { ...signal(5), confirmed: false },
      { ...signal(9), outcome: 'weird' }, { ...signal(10), ok: 'yes' }]) {
      assert.equal(normalizeCoreEvent(bad), null)
    }
    const snap = snapshots[0] as Wire & { facts: Record<string, unknown>; fences: Record<string, unknown> }
    for (const bad of [{ ...snap, facts: null }, { ...snap, fences: { ...snap.fences, playbacks: [1] } }, { ...snap, facts: { ...snap.facts, startup: 'nope' } }, { ...snap, watermark: 'x' }, { ...snap, output: { level: NaN, peak: 0, age_ms: 0 } }]) {
      assert.equal(normalizeCoreEvent(bad), null)
    }
  })
})

describe('startup wire kinds', () => {
  const env = { name: 'core.signal', session: 's1', generation: 1, seq: 1, age_ms: 0 }
  it('maps candidate, startup and stop', () => {
    assert.deepEqual(normalizeCoreEvent({ ...env, kind: 'candidate', candidate: 'c1', active: true, remaining_ms: 800 }),
      { session: 's1', generation: 1, seq: 1, ageMs: 0, kind: 'candidate', candidate: 'c1', active: true, remainingMs: 800 })
    assert.deepEqual(normalizeCoreEvent({ ...env, kind: 'startup', phase: 'degraded', partial: true }),
      { session: 's1', generation: 1, seq: 1, ageMs: 0, kind: 'startup', phase: 'degraded', partial: true })
    assert.deepEqual(normalizeCoreEvent({ ...env, kind: 'stop' }), { session: 's1', generation: 1, seq: 1, ageMs: 0, kind: 'stop' })
  })
  it('rejects malformed startup payloads', () => {
    const bad = [
      { ...env, kind: 'startup', phase: 'warming', partial: false }, { ...env, kind: 'startup', phase: 'ready', partial: 'no' },
      { ...env, kind: 'startup', phase: 'ready' },
      { ...env, kind: 'candidate', candidate: 'c1', active: true }, { ...env, kind: 'candidate', candidate: 'c1', active: true, remaining_ms: -1 },
      { ...env, kind: 'candidate', candidate: 'c1', active: true, remaining_ms: NaN }, { ...env, kind: 'candidate', candidate: '', active: true, remaining_ms: 1 },
      { ...env, kind: 'candidate', candidate: 'c1', active: 1, remaining_ms: 1 },
    ]
    for (const b of bad) assert.equal(normalizeCoreEvent(b), null)
  })
})
