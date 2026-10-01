/// <reference types="node" />
import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { expect, test } from '@playwright/test'

import { JARVIS_EVENT_NAMES } from '../protocol/events'

const REPO_ROOT = resolve(import.meta.dirname, '..', '..', '..')
const TOKEN = 'pw-t0k'

let child: ChildProcess
let port = ''

test.describe.configure({ mode: 'serial' })

test.beforeAll(async () => {
  child = spawn('python3', ['-m', 'jarvis.observability.event_stream', '--port', '0', '--token', TOKEN, '--demo'], {
    cwd: REPO_ROOT,
    env: { ...process.env, PYTHONPATH: resolve(REPO_ROOT, 'src') },
    stdio: ['pipe', 'pipe', 'inherit'],
  })
  const lines = createInterface({ input: child.stdout! })
  port = await new Promise<string>((resolveReady, reject) => {
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
})

test.afterAll(() => {
  child?.kill('SIGTERM')
})

test('streams real events cross-origin to the page', async ({ page }) => {
  await page.goto('/')
  const names = await page.evaluate(
    ({ url }) =>
      new Promise<string[]>((resolveNames, reject) => {
        const source = new EventSource(url)
        const seen: string[] = []
        source.onmessage = (message) => {
          seen.push(JSON.parse(message.data).name)
          if (seen.length === 3) {
            source.close()
            resolveNames(seen)
          }
        }
        source.onerror = () => {
          source.close()
          reject(new Error('EventSource error'))
        }
      }),
    { url: `http://127.0.0.1:${port}/events?token=${TOKEN}` },
  )
  expect(names).toHaveLength(3)
  for (const name of names) expect(JARVIS_EVENT_NAMES as readonly string[]).toContain(name)
})

test('a bad token errors and delivers nothing', async ({ page }) => {
  await page.goto('/')
  const result = await page.evaluate(
    ({ url }) =>
      new Promise<{ errored: boolean; messages: number }>((resolveResult) => {
        const source = new EventSource(url)
        let errored = false
        let messages = 0
        source.onerror = () => {
          errored = true
        }
        source.onmessage = () => {
          messages += 1
        }
        setTimeout(() => {
          source.close()
          resolveResult({ errored, messages })
        }, 1500)
      }),
    { url: `http://127.0.0.1:${port}/events?token=nope` },
  )
  expect(result.errored).toBe(true)
  expect(result.messages).toBe(0)
})
