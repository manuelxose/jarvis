import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createDesktopTransport, sendDesktopControl } from '../src/services/desktop-transport.ts'

function fakeCore(control: (a: string) => Promise<unknown> = async () => ({ ok: true })) {
  const events = new Set<(e: any) => void>()
  const states = new Set<(s: any) => void>()
  return {
    events, states,
    core: {
      getState: async () => 'connected',
      onState: (cb: any) => { states.add(cb); return () => states.delete(cb) },
      onEvent: (cb: any) => { events.add(cb); return () => events.delete(cb) },
      control,
    } as any,
  }
}

test('subscribe filters unknown names and wrong versions, disposer unsubscribes', () => {
  const f = fakeCore()
  const t = createDesktopTransport(f.core)
  const seen: string[] = []
  const off = t.subscribe((e) => seen.push(e.name))
  for (const cb of f.events) {
    cb({ v: 1, name: 'system.metrics' })
    cb({ v: 1, name: 'not.a.real.event' })
    cb({ v: 2, name: 'system.metrics' })
    cb(null)
  }
  assert.deepEqual(seen, ['system.metrics'])
  off()
  assert.equal(f.events.size, 0)
})

test('a throwing listener does not break the subscription', () => {
  const f = fakeCore()
  createDesktopTransport(f.core).subscribe(() => { throw new Error('x') })
  for (const cb of f.events) assert.doesNotThrow(() => cb({ v: 1, name: 'system.metrics' }))
})

test('state forwarding, initial state and disposer', async () => {
  const f = fakeCore()
  const t = createDesktopTransport(f.core)
  assert.equal(await t.getState(), 'connected')
  const seen: string[] = []
  const off = t.onState((s) => seen.push(s))
  for (const cb of f.states) cb('reconnecting')
  off()
  assert.deepEqual(seen, ['reconnecting'])
  assert.equal(f.states.size, 0)
})

test('control maps ok:false, rejection and bad action to errors', async () => {
  await sendDesktopControl(fakeCore().core, 'activate')
  await assert.rejects(sendDesktopControl(fakeCore(async () => ({ ok: false, error: 'not-connected' })).core, 'sleep'), /denied \(not-connected\)/)
  await assert.rejects(sendDesktopControl(fakeCore(async () => { throw new Error('ipc') }).core, 'sleep'), /connection failed/)
  await assert.rejects(sendDesktopControl(fakeCore(async () => undefined).core, 'sleep'), /denied/)
  let calls = 0
  await assert.rejects(sendDesktopControl(fakeCore(async () => { calls++; return { ok: true } }).core, 'quit' as 'sleep'), /unavailable/)
  assert.equal(calls, 0)
})
