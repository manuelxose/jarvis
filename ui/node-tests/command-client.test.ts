import { test } from 'node:test'
import assert from 'node:assert/strict'
import { sendControl } from '../src/services/command-client.ts'

const endpoint = { url: 'http://127.0.0.1:8080/events', token: 'test-secret' }
Object.defineProperty(globalThis, 'window', { value: { location: { origin: 'http://127.0.0.1:8080' } }, configurable: true })

test('control dispatch sends one allowlisted POST, token only in header', async () => {
  const calls: Array<[string, RequestInit]> = []
  const fetchImpl = (async (url: string, init: RequestInit) => {
    calls.push([url, init])
    return new Response('{"status":"accepted"}', { status: 202 })
  }) as typeof fetch
  await sendControl('activate', { endpoint, fetchImpl })
  assert.equal(calls.length, 1)
  assert.equal(calls[0][0], 'http://127.0.0.1:8080/control')
  assert.equal(calls[0][1].method, 'POST')
  assert.equal((calls[0][1].headers as Record<string, string>)['X-Jarvis-Token'], 'test-secret')
  assert.deepEqual(JSON.parse(calls[0][1].body as string), { action: 'activate' })
  assert.equal(calls[0][0].includes(endpoint.token), false)
})

test('bad endpoint, unknown action and no endpoint never fetch', async () => {
  let calls = 0
  const fetchImpl = (async () => { calls++; throw Error('unexpected') }) as typeof fetch
  await assert.rejects(sendControl('sleep', { endpoint: null, fetchImpl }), /unavailable/)
  await assert.rejects(sendControl('other' as 'sleep', { endpoint, fetchImpl }), /unavailable/)
  await assert.rejects(sendControl('sleep', { endpoint: { ...endpoint, url: 'http://evil.invalid/events' }, fetchImpl }), /not on this page/)
  assert.equal(calls, 0)
})

test('denial, malformed acceptance, connection loss and timeout never claim success', async () => {
  await assert.rejects(sendControl('sleep', { endpoint, fetchImpl: (async () => new Response('{"status":"denied"}', { status: 401 })) as typeof fetch }), /denied \(401\)/)
  await assert.rejects(sendControl('sleep', { endpoint, fetchImpl: (async () => new Response('oops', { status: 202 })) as typeof fetch }), /Unexpected control response/)
  await assert.rejects(sendControl('sleep', { endpoint, fetchImpl: (async () => { throw new TypeError('network') }) as typeof fetch }), /connection failed/)
  const hanging = ((_: string, init: RequestInit) => new Promise<Response>((_, reject) => init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))))) as typeof fetch
  await assert.rejects(sendControl('sleep', { endpoint, fetchImpl: hanging, timeoutMs: 5 }), /timed out/)
})

test('desktop bridge routes control through IPC and never fetches', async () => {
  const sent: string[] = []
  const win = globalThis.window as unknown as { jarvisDesktop?: unknown }
  win.jarvisDesktop = { core: { control: async (a: string) => { sent.push(a); return { ok: true } } } }
  try {
    let fetches = 0
    const fetchImpl = (async () => { fetches++; throw Error('unexpected') }) as typeof fetch
    await sendControl('activate', { fetchImpl })
    assert.deepEqual(sent, ['activate'])
    assert.equal(fetches, 0)
    win.jarvisDesktop = { core: { control: async () => ({ ok: false, error: 'not-connected' }) } }
    await assert.rejects(sendControl('sleep', { fetchImpl }), /denied \(not-connected\)/)
  } finally {
    delete win.jarvisDesktop
  }
})
