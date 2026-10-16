import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { describe, it } from 'node:test'
import { createVisualCoreFeed, toMachineConnection } from '../src/services/visual-core-feed.ts'
import { createVisualCoreService, type VisualCoreScheduler } from '../src/services/visual-core-service.ts'

const fixture = JSON.parse(readFileSync(new URL('./fixtures/core-signals.json', import.meta.url), 'utf8')) as Record<string, unknown>[]

function make() {
  const clock = { t: 10000 }
  const timers = new Map<number, () => void>()
  let id = 1
  const scheduler: VisualCoreScheduler = {
    set(cb) { const h = id++; timers.set(h, cb); return h },
    clear(h) { timers.delete(h as number) },
  }
  const svc = createVisualCoreService({ clock: () => clock.t, scheduler })
  return { svc, feed: createVisualCoreFeed(svc), clock }
}
const signal = (seq: number, extra: Record<string, unknown>) => ({ v: 1, name: 'core.signal', session: 'fixture-session', generation: 1, seq, age_ms: 0, ...extra })

describe('visual core feed', () => {
  it('maps transport statuses to machine connections', () => {
    const table: Record<string, string> = {
      connected: 'connected', connecting: 'connecting', reconnecting: 'connecting', disconnected: 'disconnected',
      'auth-failure': 'auth-error', 'incompatible-version': 'incompatible', bogus: 'disconnected', '': 'disconnected',
    }
    for (const [from, to] of Object.entries(table)) assert.equal(toMachineConnection(from), to)
  })

  it('late-mount replay reaches the same state as continuous ingestion', async () => {
    const a = make()
    a.feed.setStatus('connected')
    for (const e of fixture) a.feed.accept(e)
    const b = make()
    b.feed.setStatus('connected')
    await b.feed.resync(Promise.resolve(fixture))
    assert.deepEqual(b.svc.getSnapshot(), a.svc.getSnapshot())
    assert.equal(b.svc.getDiagnostics().rejected, a.svc.getDiagnostics().rejected)
  })

  it('live duplicates of replayed seq are not rejected nor force awaiting-snapshot', async () => {
    const { svc, feed } = make()
    feed.setStatus('connected')
    let release!: (v: unknown[]) => void
    const p = feed.resync(new Promise<unknown[]>(r => { release = r }))
    for (const e of fixture.slice(10, 16)) feed.accept(e)
    release(fixture.slice(0, 12))
    await p
    feed.accept(fixture[11])
    feed.accept(fixture[12])
    assert.equal(svc.getDiagnostics().rejected, 0)
    assert.equal(svc.getSnapshot().awaitingSnapshot, false)
  })

  it('getReplay rejection waits for a live snapshot without throwing', async () => {
    const { svc, feed } = make()
    feed.setStatus('connected')
    await feed.resync(Promise.reject(new Error('ipc')))
    assert.equal(svc.getSnapshot().awaitingSnapshot, true)
    feed.accept(fixture[0])
    assert.equal(svc.getSnapshot().awaitingSnapshot, false)
  })

  it('legacy events never yield SPEAKING; cancel-turn removes it', () => {
    const { svc, feed } = make()
    feed.setStatus('connected')
    feed.accept(fixture[0])
    feed.accept({ name: 'speech.started' })
    feed.accept({ name: 'voice.state', state: 'speaking' })
    assert.notEqual(svc.getSnapshot().state, 'SPEAKING')
    assert.equal(svc.getDiagnostics().rejected, 0)
    feed.accept(signal(1, { kind: 'turn-start', turn: 't1' }))
    feed.accept(signal(2, { kind: 'playback-start', turn: 't1', playback: 'p1', confirmed: true }))
    feed.accept(signal(3, { kind: 'output', turn: 't1', playback: 'p1', level: 0.5, peak: 0.6 }))
    assert.equal(svc.getSnapshot().state, 'SPEAKING')
    feed.accept(signal(4, { kind: 'cancel-turn', turn: 't1' }))
    assert.notEqual(svc.getSnapshot().state, 'SPEAKING')
  })

  it('malformed events are ignored', () => {
    const { svc, feed } = make()
    feed.setStatus('connected')
    for (const bad of [null, 7, 'x', [], { name: 'core.signal' }, signal(1, { kind: 'nope' })]) feed.accept(bad)
    assert.equal(svc.getDiagnostics().rejected, 0)
  })

  it('setStatus(disconnected) yields DISCONNECTED and resets dedupe', () => {
    const { svc, feed } = make()
    feed.setStatus('connected')
    for (const e of fixture) feed.accept(e)
    feed.setStatus('disconnected')
    assert.equal(svc.getSnapshot().state, 'DISCONNECTED')
    feed.setStatus('connected')
    feed.accept({ ...fixture[0], session: 'next-session', replaces_session: 'fixture-session', generation: 2 })
    assert.equal(svc.getSnapshot().awaitingSnapshot, false)
  })

  it('dispose is idempotent, drops timers and makes accept a no-op', async () => {
    const { svc, feed } = make()
    feed.setStatus('connected')
    feed.accept(fixture[0])
    feed.dispose()
    feed.dispose()
    const before = svc.getSnapshot()
    feed.accept(fixture[1])
    feed.setStatus('disconnected')
    await feed.resync(fixture)
    assert.equal(svc.getSnapshot(), before)
    assert.equal(svc.getDiagnostics().pendingTimers, 0)
    assert.equal(svc.getDiagnostics().disposed, true)
  })
})
