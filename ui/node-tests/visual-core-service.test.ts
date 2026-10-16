import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { emptyFacts, emptyFences, type NormalizedSnapshot } from '../src/components/jarvis/visual-core/visualCoreMachine.ts'
import { createVisualCoreService, type VisualCoreScheduler } from '../src/services/visual-core-service.ts'

function harness(start = 10000) {
  const clock = { t: start }
  const timers = new Map<number, { cb: () => void; at: number }>()
  let nextId = 1
  let setCalls = 0
  const scheduler: VisualCoreScheduler = {
    set(cb, delay) { setCalls++; const id = nextId++; timers.set(id, { cb, at: clock.t + delay }); return id },
    clear(h) { timers.delete(h as number) },
  }
  const svc = createVisualCoreService({ clock: () => clock.t, scheduler })
  let seq = 0
  const snap = (overrides: Partial<NormalizedSnapshot> = {}): NormalizedSnapshot => ({
    kind: 'snapshot', session: 's1', replacesSession: null, generation: 1, watermark: seq, ageMs: 0,
    facts: emptyFacts(), fences: emptyFences(), output: null, microphone: null, candidate: null,
    successRemainingMs: 0, errorRemainingMs: 0, ...overrides,
  })
  const ev = (payload: Record<string, unknown>) => ({ session: 's1', generation: 1, seq: ++seq, ageMs: 0, ...payload })
  const advance = (ms: number) => {
    clock.t += ms
    for (const [id, t] of [...timers]) if (t.at <= clock.t) { timers.delete(id); t.cb() }
  }
  const connectLive = (overrides: Partial<NormalizedSnapshot> = {}) => {
    svc.setConnection('connected')
    seq = 0
    assert.equal(svc.ingest(snap(overrides)), true)
  }
  const speakingFacts = { ...emptyFacts(), turn: 't1', thinking: true, playback: { id: 'p1', turn: 't1', confirmed: true as const } }
  const speak = () => connectLive({ facts: speakingFacts, output: { level: 0.5, peak: 0.6, ageMs: 0 } })
  return { svc, clock, timers, setCalls: () => setCalls, snap, ev, advance, connectLive, speak, seq: () => seq }
}

describe('visual core service: event to renderer-read flow', () => {
  it('starts disconnected and static with no producer', () => {
    const h = harness()
    assert.equal(h.svc.getSnapshot().state, 'DISCONNECTED')
    assert.deepEqual(h.svc.readSignals(), { output: 0, peak: 0, microphone: 0 })
    assert.equal(h.timers.size, 0)
  })

  it('maps normalized fixtures to semantic snapshot and separate signal reads', () => {
    const h = harness()
    let calls = 0
    h.svc.subscribe(() => calls++)
    h.connectLive()
    assert.equal(h.svc.getSnapshot().state, 'IDLE')
    h.svc.ingest(h.ev({ kind: 'turn-start', turn: 't1' }))
    assert.equal(h.svc.getSnapshot().state, 'THINKING')
    h.svc.ingest(h.ev({ kind: 'playback-start', turn: 't1', playback: 'p1', confirmed: true }))
    assert.equal(h.svc.getSnapshot().state, 'THINKING', 'confirmed playback without measured output is not speaking')
    assert.equal(h.svc.getSnapshot().detail, 'Output telemetry unavailable')
    const before = calls
    h.svc.ingest(h.ev({ kind: 'output', turn: 't1', playback: 'p1', level: 0.4, peak: 0.5 }))
    assert.equal(h.svc.getSnapshot().state, 'SPEAKING')
    assert.equal(calls, before + 1)
    assert.deepEqual(h.svc.readSignals(), { output: 0.4, peak: 0.5, microphone: 0 })
    assert.ok(Object.isFrozen(h.svc.getSnapshot()))
  })

  it('generated-only speech and legacy v1 events never qualify or install', () => {
    const h = harness()
    h.connectLive()
    h.svc.ingest(h.ev({ kind: 'turn-start', turn: 't1' }))
    h.svc.ingest(h.ev({ kind: 'generated', turn: 't1', active: true }))
    assert.notEqual(h.svc.getSnapshot().state, 'SPEAKING')
    const s = h.svc.getSnapshot()
    for (const legacy of [{ type: 'speech.started' }, { type: 'voice.state', state: 'speaking' }, { type: 'speech.started', session: 's1' }, 'speaking', null, 42]) {
      assert.equal(h.svc.ingest(legacy), false)
    }
    assert.equal(h.svc.getSnapshot().state, s.state)
    assert.equal(h.svc.getSnapshot().reason, 'invalid-input')
    assert.deepEqual(h.svc.readSignals(), { output: 0, peak: 0, microphone: 0 })
  })
})

describe('connection, snapshot and timeout', () => {
  it('reports every connection state as disconnected with matching connection', () => {
    const h = harness()
    for (const status of ['connecting', 'disconnected', 'auth-error', 'incompatible'] as const) {
      h.svc.setConnection(status)
      assert.equal(h.svc.getSnapshot().state, 'DISCONNECTED')
      assert.equal(h.svc.getSnapshot().connection, status)
    }
    assert.equal(h.svc.setConnection('bogus' as never), false)
  })

  it('connected without snapshot awaits, then exposes State unavailable at 2000ms', () => {
    const h = harness()
    h.svc.setConnection('connected')
    assert.equal(h.svc.getSnapshot().state, 'DISCONNECTED')
    assert.equal(h.svc.getSnapshot().detail, 'Awaiting authoritative state')
    assert.equal(h.svc.ingest({ session: 's1', generation: 1, seq: 1, ageMs: 0, kind: 'turn-start', turn: 't1' }), false)
    assert.equal(h.timers.size, 1)
    let calls = 0
    h.svc.subscribe(() => calls++)
    h.advance(1999)
    assert.equal(h.svc.getSnapshot().detail, 'Awaiting authoritative state')
    h.advance(1)
    assert.equal(h.svc.getSnapshot().detail, 'State unavailable')
    assert.equal(h.svc.getSnapshot().reason, 'snapshot-timeout')
    assert.equal(calls, 1)
    assert.equal(h.timers.size, 0)
  })

  it('snapshot after reconnect restores and a stale-amplitude snapshot stays static', () => {
    const h = harness()
    h.speak()
    assert.equal(h.svc.getSnapshot().state, 'SPEAKING')
    h.svc.setConnection('disconnected')
    assert.equal(h.svc.getSnapshot().state, 'DISCONNECTED')
    h.advance(50)
    h.svc.setConnection('connected')
    // Snapshot output measured 100ms before capture, captured 200ms ago: 300ms old.
    assert.equal(h.svc.ingest(h.snap({ facts: { ...emptyFacts(), turn: 't1', thinking: true, playback: { id: 'p2', turn: 't1', confirmed: true } }, output: { level: 0.9, peak: 0.9, ageMs: 100 }, ageMs: 200, watermark: 5 })), true)
    assert.equal(h.svc.getSnapshot().state, 'THINKING')
    assert.deepEqual(h.svc.readSignals(), { output: 0, peak: 0, microphone: 0 })
    assert.equal(h.svc.ingest(h.snap({ watermark: 1 })), false, 'snapshot rollback rejected')
  })

  it('delayed prior-session traffic is rejected', () => {
    const h = harness()
    h.speak()
    const s = h.svc.getSnapshot()
    assert.equal(h.svc.ingest({ session: 's0', generation: 9, seq: 99, ageMs: 0, kind: 'output', turn: 't1', playback: 'p1', level: 1, peak: 1 }), false)
    assert.equal(h.svc.getSnapshot().state, s.state)
    assert.equal(h.svc.getSnapshot().reason, 'stale-generation/session')
    assert.equal(h.svc.readSignals().output, 0.5)
  })
})

describe('expiry and freshness', () => {
  it('output expires strictly after 250ms through one scheduled semantic notification', () => {
    const h = harness()
    h.speak()
    assert.equal(h.timers.size, 1)
    let calls = 0
    h.svc.subscribe(() => calls++)
    h.advance(250)
    assert.equal(h.svc.getSnapshot().state, 'SPEAKING')
    assert.equal(h.svc.readSignals().output, 0.5)
    h.advance(1)
    assert.equal(h.svc.getSnapshot().state, 'THINKING')
    assert.equal(calls, 1)
    assert.equal(h.svc.readSignals().output, 0)
  })

  it('invalid and nonfinite samples never refresh freshness', () => {
    const h = harness()
    h.speak()
    h.advance(200)
    for (const level of [NaN, Infinity, -Infinity, '0.5', null]) {
      assert.equal(h.svc.ingest(h.ev({ kind: 'output', turn: 't1', playback: 'p1', level, peak: 0.5 })), false)
    }
    assert.equal(h.svc.ingest(h.ev({ kind: 'output', turn: 't1', playback: 'p1', level: 0.5, peak: 0.5, ageMs: -1 })), false)
    h.advance(51)
    assert.equal(h.svc.getSnapshot().state, 'THINKING')
    assert.equal(h.svc.readSignals().output, 0)
  })

  it('a delayed amplitude cannot become fresh on receipt', () => {
    const h = harness()
    h.speak()
    h.advance(10)
    assert.equal(h.svc.ingest(h.ev({ kind: 'output', turn: 't1', playback: 'p1', level: 0.7, peak: 0.7, ageMs: 300 })), false)
    assert.equal(h.svc.getSnapshot().state, 'SPEAKING', 'older measurement is rejected and does not overwrite the newer one')
    assert.equal(h.svc.readSignals().output, 0.5)
  })

  it('success hold expires on schedule', () => {
    const h = harness()
    h.connectLive({ facts: { ...emptyFacts(), turn: 't1', thinking: true } })
    h.svc.ingest(h.ev({ kind: 'turn-end', turn: 't1', ok: true }))
    assert.equal(h.svc.getSnapshot().state, 'SUCCESS')
    h.advance(899)
    assert.equal(h.svc.getSnapshot().state, 'SUCCESS')
    h.advance(1)
    assert.equal(h.svc.getSnapshot().state, 'IDLE')
  })
})

describe('synchronous clearing and fences', () => {
  it('cancel and disconnect zero signals before any listener observes the change', () => {
    for (const act of [
      (h: ReturnType<typeof harness>) => h.svc.ingest(h.ev({ kind: 'cancel-turn', turn: 't1' })),
      (h: ReturnType<typeof harness>) => h.svc.ingest(h.ev({ kind: 'stop' })),
      (h: ReturnType<typeof harness>) => h.svc.setConnection('disconnected'),
    ]) {
      const h = harness()
      h.speak()
      const seen: number[] = []
      h.svc.subscribe(() => seen.push(h.svc.readSignals().output))
      act(h)
      assert.deepEqual(seen, [0])
      assert.notEqual(h.svc.getSnapshot().state, 'SPEAKING')
    }
  })

  it('duplicate terminal delivery and late old-generation frames are inert', () => {
    const h = harness()
    h.speak()
    h.svc.ingest(h.ev({ kind: 'playback-end', turn: 't1', playback: 'p1', outcome: 'ended' }))
    const s = h.svc.getSnapshot()
    let calls = 0
    h.svc.subscribe(() => calls++)
    assert.equal(h.svc.ingest(h.ev({ kind: 'playback-end', turn: 't1', playback: 'p1', outcome: 'ended' })), false)
    assert.equal(h.svc.ingest(h.ev({ kind: 'output', turn: 't1', playback: 'p1', level: 1, peak: 1 })), false)
    assert.equal(h.svc.ingest({ session: 's1', generation: 0, seq: h.seq() + 1, ageMs: 0, kind: 'output', turn: 't1', playback: 'p1', level: 1, peak: 1 }), false)
    assert.equal(h.svc.getSnapshot().state, s.state)
    assert.ok(calls <= 3, 'notifications bounded by distinct rejection reasons, not frame count')
    assert.equal(h.svc.readSignals().output, 0)
    assert.ok(h.svc.getDiagnostics().rejected >= 3)
  })
})

describe('boundedness under high-rate samples', () => {
  it('100000 same-semantic samples keep snapshot identity, notify nobody and hold one timer', () => {
    const h = harness()
    h.speak()
    const s = h.svc.getSnapshot()
    let calls = 0
    h.svc.subscribe(() => calls++)
    const setsBefore = h.setCalls()
    for (let i = 0; i < 100000; i++) {
      h.clock.t += 0.001
      assert.equal(h.svc.ingest(h.ev({ kind: 'output', turn: 't1', playback: 'p1', level: (i % 100) / 100, peak: 1 })), true)
    }
    assert.equal(h.svc.getSnapshot(), s)
    assert.equal(calls, 0)
    assert.equal(h.timers.size, 1)
    assert.equal(h.svc.getDiagnostics().pendingTimers, 1)
    assert.ok(h.setCalls() - setsBefore <= 1, 'no timer per sample')
    assert.equal(h.svc.readSignals().output, 0.99)
  })
})

describe('subscription and disposal', () => {
  it('unsubscribe, self-removal and listener exceptions do not block others', () => {
    const h = harness()
    const order: string[] = []
    const off = h.svc.subscribe(() => order.push('a'))
    h.svc.subscribe(() => { throw new Error('boom') })
    const offSelf = h.svc.subscribe(() => { order.push('self'); offSelf() })
    h.svc.subscribe(() => order.push('z'))
    h.svc.setConnection('connecting')
    h.svc.setConnection('auth-error')
    assert.deepEqual(order, ['a', 'self', 'z', 'a', 'z'])
    off(); off()
    h.svc.setConnection('incompatible')
    assert.deepEqual(order, ['a', 'self', 'z', 'a', 'z', 'z'])
    assert.equal(h.svc.getDiagnostics().listenerErrors, 3)
  })

  it('dispose is idempotent, clears the timer and rejects later traffic', () => {
    const h = harness()
    h.speak()
    let calls = 0
    h.svc.subscribe(() => calls++)
    assert.equal(h.timers.size, 1)
    h.svc.dispose(); h.svc.dispose()
    assert.equal(h.timers.size, 0)
    assert.equal(h.svc.getDiagnostics().disposed, true)
    assert.equal(h.svc.ingest(h.ev({ kind: 'output', turn: 't1', playback: 'p1', level: 1, peak: 1 })), false)
    assert.equal(h.svc.setConnection('disconnected'), false)
    h.svc.tick()
    h.advance(10000)
    assert.equal(calls, 0)
    assert.equal(h.timers.size, 0)
    assert.deepEqual(h.svc.readSignals(), { output: 0, peak: 0, microphone: 0 })
    h.svc.subscribe(() => calls++)()
  })
})

describe('visual core service: cinematic startup semantics', () => {
  const startup = (h: ReturnType<typeof harness>, phase: string, partial = false) => h.svc.ingest(h.ev({ kind: 'startup', phase, partial }))
  const sem = (h: ReturnType<typeof harness>) => h.svc.getSnapshot()

  it('full path: candidate to announcing with confirmed output to ready', () => {
    const h = harness()
    h.connectLive()
    h.svc.ingest(h.ev({ kind: 'candidate', candidate: 'c1', active: true, remainingMs: 1000 }))
    assert.equal(sem(h).state, 'STARTING'); assert.equal(sem(h).detail, 'Confirming activation')
    startup(h, 'acknowledged'); assert.equal(sem(h).detail, 'Activating')
    startup(h, 'initializing'); assert.equal(sem(h).detail, 'Preparing services and music')
    startup(h, 'announcing'); assert.equal(sem(h).detail, 'Welcome')
    h.svc.ingest(h.ev({ kind: 'turn-start', turn: 'w1' }))
    h.svc.ingest(h.ev({ kind: 'playback-start', turn: 'w1', playback: 'wp', confirmed: true }))
    h.svc.ingest(h.ev({ kind: 'output', turn: 'w1', playback: 'wp', level: 0.5, peak: 0.6 }))
    assert.equal(sem(h).state, 'STARTING')
    assert.equal(h.svc.readSignals().output, 0.5)
    h.svc.ingest(h.ev({ kind: 'playback-end', turn: 'w1', playback: 'wp', outcome: 'ended' }))
    h.svc.ingest(h.ev({ kind: 'turn-end', turn: 'w1', ok: true }))
    startup(h, 'ready')
    h.advance(1000)
    assert.equal(sem(h).state, 'IDLE'); assert.equal(sem(h).label, 'Ready'); assert.equal(sem(h).startup, 'ready')
  })

  it('muted: announcing without playback stays STARTING with zero output', () => {
    const h = harness()
    h.connectLive()
    startup(h, 'announcing')
    assert.equal(sem(h).state, 'STARTING')
    assert.deepEqual(h.svc.readSignals(), { output: 0, peak: 0, microphone: 0 })
  })

  it('interrupted: cancelled startup stops, rejects late output, then cleanup returns to IDLE', () => {
    const h = harness()
    h.connectLive()
    startup(h, 'announcing')
    h.svc.ingest(h.ev({ kind: 'turn-start', turn: 'w1' }))
    h.svc.ingest(h.ev({ kind: 'playback-start', turn: 'w1', playback: 'wp', confirmed: true }))
    startup(h, 'cancelled')
    assert.equal(sem(h).state, 'STOPPING')
    assert.equal(h.svc.ingest(h.ev({ kind: 'output', turn: 'w1', playback: 'wp', level: 0.9, peak: 0.9 })), false)
    assert.equal(h.svc.readSignals().output, 0)
    h.svc.ingest(h.snap({ generation: 1, watermark: h.seq() + 1, facts: { ...emptyFacts(), startup: 'cancelled' }, fences: { ...emptyFences(), stopped: true, cleanedUp: true } }))
    assert.equal(sem(h).state, 'IDLE')
  })

  it('candidate-expired: inactive candidate never reaches STARTING', () => {
    const h = harness()
    h.connectLive()
    h.svc.ingest(h.ev({ kind: 'candidate', candidate: 'c1', active: true, remainingMs: 500 }))
    h.svc.ingest(h.ev({ kind: 'candidate', candidate: 'c1', active: false, remainingMs: 0 }))
    assert.equal(sem(h).state, 'IDLE')
  })

  it('failed: persistent ERROR with level 0', () => {
    const h = harness()
    h.connectLive()
    startup(h, 'initializing')
    startup(h, 'failed')
    assert.equal(sem(h).state, 'ERROR')
    assert.equal(h.svc.readSignals().output, 0)
  })

  it('partial: degraded is Ready with limitations and survives a later turn generation', () => {
    const h = harness()
    h.connectLive()
    startup(h, 'degraded', true)
    assert.equal(sem(h).state, 'IDLE'); assert.equal(sem(h).detail, 'Ready with limitations')
    h.svc.ingest({ session: 's1', generation: 2, seq: h.seq() + 1, ageMs: 0, kind: 'turn-start', turn: 't9' })
    assert.equal(sem(h).state, 'THINKING')
    assert.equal(sem(h).startup, 'degraded'); assert.equal(sem(h).partial, true)
  })

  it('new activation startup in a new generation resets carried facts', () => {
    const h = harness()
    h.connectLive()
    startup(h, 'ready')
    h.svc.ingest({ session: 's1', generation: 2, seq: h.seq() + 1, ageMs: 0, kind: 'startup', phase: 'acknowledged', partial: false })
    assert.equal(sem(h).startup, 'acknowledged'); assert.equal(sem(h).partial, false)
  })

  it('apps summary is null when unknown and counted from snapshot', () => {
    const h = harness()
    h.connectLive()
    assert.equal(sem(h).apps, null)
    h.svc.ingest(h.snap({ watermark: h.seq() + 1, facts: { ...emptyFacts(), startup: 'degraded', partial: true, apps: [{ id: 'a', status: 'ready' }, { id: 'b', status: 'failed' }, { id: 'c', status: 'pending' }] } }))
    assert.deepEqual(sem(h).apps, { ready: 1, failed: 1, pending: 1, total: 3 })
  })

  it('reconnect-no-replay: fresh service shows announcing from a snapshot and drops older seq', () => {
    const h = harness()
    h.svc.setConnection('connected')
    assert.equal(h.svc.ingest(h.snap({ watermark: 7, facts: { ...emptyFacts(), startup: 'announcing' } })), true)
    assert.equal(sem(h).state, 'STARTING'); assert.equal(sem(h).startup, 'announcing')
    assert.equal(h.svc.ingest({ session: 's1', generation: 1, seq: 7, ageMs: 0, kind: 'startup', phase: 'initializing', partial: false }), false)
    assert.equal(sem(h).startup, 'announcing')
  })
})

describe('lifecycle accounting with an injected scheduler', () => {

  it('100 create/drive/dispose cycles leave no timer, listener or late callback', () => {
    for (let i = 0; i < 100; i++) {
      const h = harness()
      let notified = 0
      const off = h.svc.subscribe(() => notified++)
      h.speak()
      h.svc.ingest(h.ev({ kind: 'output', turn: 't1', playback: 'p1', level: 0.7, peak: 0.8 }))
      if (i % 3 === 0) h.svc.ingest(h.ev({ kind: 'cancel-turn', turn: 't1' }))
      if (i % 3 === 1) h.svc.setConnection('disconnected')
      if (i % 3 === 2) h.svc.ingest(h.ev({ kind: 'startup', phase: 'cancelled', partial: false }))
      assert.ok(h.timers.size <= 1, 'at most one deadline is ever pending')
      h.svc.dispose()
      const seen = notified
      assert.equal(h.timers.size, 0)
      assert.equal(h.svc.getDiagnostics().pendingTimers, 0)
      h.advance(120000)
      assert.equal(notified, seen, 'no notification after disposal')
      off()
    }
  })

  it('30 Hz envelopes plus a 10x burst never add timers or notifications', () => {
    const h = harness()
    h.speak()
    let calls = 0
    h.svc.subscribe(() => calls++)
    const sets = h.setCalls()
    for (let i = 0; i < 90; i++) { h.advance(33); h.svc.ingest(h.ev({ kind: 'output', turn: 't1', playback: 'p1', level: (i % 10) / 10, peak: 1 })) }
    for (let i = 0; i < 300; i++) h.svc.ingest(h.ev({ kind: 'output', turn: 't1', playback: 'p1', level: 0.5, peak: 1 }))
    assert.equal(calls, 0)
    assert.ok(h.timers.size <= 1)
    assert.ok(h.setCalls() - sets <= 91, 'deadline moves with freshness at most once per envelope, never accumulating')
  })

  it('stale, out-of-order and duplicate frames during speech do not notify or add timers', () => {
    const h = harness()
    h.speak()
    let calls = 0
    h.svc.subscribe(() => calls++)
    const timers = h.timers.size
    const good = h.ev({ kind: 'output', turn: 't1', playback: 'p1', level: 0.6, peak: 0.7 })
    assert.equal(h.svc.ingest(good), true)
    assert.equal(h.svc.ingest(good), false, 'repeated identical envelope is a stale sequence')
    assert.equal(h.svc.ingest({ ...good, seq: good.seq - 1 }), false)
    assert.equal(h.svc.ingest({ ...good, seq: good.seq + 5 }), false, 'a sequence gap is rejected and forces reconciliation')
    assert.equal(h.svc.getSnapshot().awaitingSnapshot, true)
    assert.equal(h.svc.getSnapshot().reason, 'sequence-gap')
    assert.equal(h.svc.ingest({ ...good, session: 'old', generation: 9, seq: 99 }), false)
    assert.ok(h.timers.size <= timers + 1)
    assert.ok(calls <= 4, `notifications bounded by distinct rejection reasons, got ${calls}`)
  })

  it('cancel or disconnect during startup replaces pending deadlines and disposal clears them', () => {
    const h = harness()
    h.connectLive()
    h.svc.ingest(h.ev({ kind: 'candidate', candidate: 'c1', active: true, remainingMs: 1500 }))
    h.svc.ingest(h.ev({ kind: 'startup', phase: 'announcing', partial: false }))
    assert.ok(h.timers.size <= 1)
    h.svc.ingest(h.ev({ kind: 'startup', phase: 'cancelled', partial: false }))
    h.svc.setConnection('disconnected')
    assert.equal(h.svc.getSnapshot().state, 'DISCONNECTED')
    assert.ok(h.timers.size <= 1)
    h.svc.dispose()
    assert.equal(h.timers.size, 0)
    assert.equal(h.svc.getSnapshot().state, 'DISCONNECTED', 'last textual state survives disposal')
  })

  it('disposing from inside a listener schedules nothing and later callbacks are inert', () => {
    const h = harness()
    h.speak()
    h.svc.subscribe(() => h.svc.dispose())
    h.svc.ingest(h.ev({ kind: 'cancel-turn', turn: 't1' }))
    assert.equal(h.svc.getDiagnostics().disposed, true)
    assert.equal(h.timers.size, 0)
    h.advance(100000)
    assert.equal(h.svc.ingest(h.ev({ kind: 'output', turn: 't1', playback: 'p1', level: 1, peak: 1 })), false)
  })
})
