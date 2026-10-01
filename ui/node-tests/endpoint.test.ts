import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { readEndpoint } from '../src/services/endpoint.ts'

function fakeHistory() {
  const calls: unknown[][] = []
  return { calls, replaceState: (...args: unknown[]) => void calls.push(args) }
}

const base = { origin: 'http://127.0.0.1:8765', pathname: '/', search: '' }

describe('readEndpoint', () => {
  it('builds the events url from the origin and clears the token from the address bar', () => {
    const hist = fakeHistory()
    const endpoint = readEndpoint({ ...base, hash: '#token=abc%2B123' }, hist)
    assert.deepEqual(endpoint, { url: 'http://127.0.0.1:8765/events', token: 'abc+123' })
    assert.equal(hist.calls.length, 1)
    assert.deepEqual(hist.calls[0], [null, '', '/'])
    assert.ok(!String(hist.calls[0]![2]).includes('abc'))
  })

  it('keeps the path and query when clearing the hash', () => {
    const hist = fakeHistory()
    readEndpoint({ ...base, pathname: '/app', search: '?x=1', hash: '#token=t' }, hist)
    assert.deepEqual(hist.calls[0], [null, '', '/app?x=1'])
  })

  it('returns null and leaves history alone without a token', () => {
    const hist = fakeHistory()
    assert.equal(readEndpoint({ ...base, hash: '' }, hist), null)
    assert.equal(readEndpoint({ ...base, hash: '#other=1' }, hist), null)
    assert.equal(hist.calls.length, 0)
  })

  it('treats an empty token as no endpoint but still clears the hash', () => {
    const hist = fakeHistory()
    assert.equal(readEndpoint({ ...base, hash: '#token=' }, hist), null)
    assert.equal(hist.calls.length, 1)
  })
})
