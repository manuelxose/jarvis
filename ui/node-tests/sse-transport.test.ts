import assert from 'node:assert/strict'
import { spawn, type ChildProcess } from 'node:child_process'
import { after, before, describe, it } from 'node:test'
import { createInterface } from 'node:readline'
import { resolve } from 'node:path'

import { JARVIS_EVENT_NAMES } from '../src/protocol/events.ts'
import type { JarvisEvent } from '../src/protocol/events.ts'
import { createSseTransport } from '../src/services/sse-transport.ts'

const REPO_ROOT = resolve(import.meta.dirname, '..', '..')
const TOKEN = 't0k'

let child: ChildProcess
let url = ''

function until(predicate: () => boolean, ms = 5000): Promise<void> {
  return new Promise((resolveWait, reject) => {
    const started = Date.now()
    const timer = setInterval(() => {
      if (predicate()) {
        clearInterval(timer)
        resolveWait()
      } else if (Date.now() - started > ms) {
        clearInterval(timer)
        reject(new Error('timed out waiting for condition'))
      }
    }, 20)
  })
}

before(async () => {
  child = spawn(
    process.env.JARVIS_PYTHON ?? 'python3',
    ['-m', 'jarvis.observability.event_stream', '--port', '0', '--token', TOKEN, '--demo'],
    { cwd: REPO_ROOT, env: { ...process.env, PYTHONPATH: resolve(REPO_ROOT, 'src') }, stdio: ['pipe', 'pipe', 'inherit'] },
  )
  const lines = createInterface({ input: child.stdout! })
  const port = await new Promise<string>((resolveReady, reject) => {
    const timer = setTimeout(() => reject(new Error('event_stream never printed READY')), 10_000)
    child.once('exit', (code) => reject(new Error(`event_stream exited early (${code})`)))
    lines.on('line', (line) => {
      const match = /^READY (\d+)$/.exec(line)
      if (match) {
        clearTimeout(timer)
        resolveReady(match[1]!)
      }
    })
  })
  url = `http://127.0.0.1:${port}/events`
})

after(() => {
  child?.kill('SIGTERM')
})

describe('createSseTransport', () => {
  it('connects and delivers real hub events, then stops after unsubscribe', async () => {
    const transport = createSseTransport(url, TOKEN)
    try {
      const events: JarvisEvent[] = []
      const unsubscribe = transport.subscribe((event) => events.push(event))
      await until(() => events.length >= 3)
      assert.equal(transport.connected, true)
      for (const event of events) {
        assert.equal(event.v, 1)
        assert.ok((JARVIS_EVENT_NAMES as readonly string[]).includes(event.name), event.name)
      }
      unsubscribe()
      const seen = events.length
      await new Promise((r) => setTimeout(r, 600))
      assert.equal(events.length, seen)
    } finally {
      transport.close()
    }
    assert.equal(transport.connected, false)
  })

  it('never delivers or connects with a wrong token', async () => {
    const transport = createSseTransport(url, 'wrong')
    try {
      const events: JarvisEvent[] = []
      transport.subscribe((event) => events.push(event))
      await new Promise((r) => setTimeout(r, 800))
      assert.equal(events.length, 0)
      assert.equal(transport.connected, false)
    } finally {
      transport.close()
    }
  })

  it('ignores unknown names, bad versions and non-JSON frames, and isolates throwing listeners', () => {
    type Fake = { onmessage: (m: { data: string }) => void; onopen: () => void; onerror: () => void; close(): void }
    const instances: Fake[] = []
    class FakeEventSource {
      onmessage: Fake['onmessage'] = () => {}
      onopen: Fake['onopen'] = () => {}
      onerror: Fake['onerror'] = () => {}
      constructor() {
        instances.push(this)
      }
      close() {}
    }
    const transport = createSseTransport('http://x/events', 't', FakeEventSource as unknown as typeof EventSource)
    const fake = instances[0]!
    const got: JarvisEvent[] = []
    transport.subscribe(() => {
      throw new Error('boom')
    })
    transport.subscribe((event) => got.push(event))

    assert.doesNotThrow(() => {
      fake.onmessage({ data: '{"v":1,"name":"bogus"}' })
      fake.onmessage({ data: 'not json' })
      fake.onmessage({ data: 'null' })
      fake.onmessage({ data: '{"v":2,"name":"voice.state"}' })
    })
    assert.equal(got.length, 0)

    fake.onmessage({ data: '{"v":1,"name":"voice.state","state":"idle","reason":"t","ts":1,"mono_ms":1}' })
    assert.equal(got.length, 1)
    fake.onopen()
    assert.equal(transport.connected, true)
    fake.onerror()
    assert.equal(transport.connected, false)
  })
})
