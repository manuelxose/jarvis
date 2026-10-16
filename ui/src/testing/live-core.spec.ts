/// <reference types="node" />
import { spawn, spawnSync, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { expect, test, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

const root = resolve(import.meta.dirname, '..', '..', '..')
const token = 'playwright-live-core-session'
const hasPython = spawnSync('python3', ['--version']).status === 0
let server: ChildProcess
let origin = ''
let probe = ''

test.describe.configure({ mode: 'serial' })
test.skip(!hasPython, 'python3 is not available; the real CoreSignals producer cannot be started')

test.beforeAll(async () => {
  server = spawn('python3', ['ui/src/testing/fixtures/core_signal_server.py'], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: resolve(root, 'src'), CORE_TEST_TOKEN: token },
    stdio: ['ignore', 'pipe', 'inherit'],
  })
  const lines = createInterface({ input: server.stdout! })
  const [port, statusPort] = await new Promise<[string, string]>((ready, reject) => {
    const timer = setTimeout(() => reject(new Error('core signal fixture did not start')), 10_000)
    server.once('exit', (code) => { clearTimeout(timer); reject(new Error(`core signal fixture exited (${code})`)) })
    lines.on('line', (line) => {
      const match = /^READY (\d+) (\d+)$/.exec(line)
      if (match) { clearTimeout(timer); ready([match[1]!, match[2]!]) }
    })
  })
  origin = `http://127.0.0.1:${port}`
  probe = `http://127.0.0.1:${statusPort}`
})
test.afterAll(() => { server?.kill('SIGTERM') })

async function step(route: string): Promise<void> {
  const response = await fetch(`${probe}${route}`, { method: 'POST' })
  expect((await response.json()) as { ok: boolean }).toEqual({ ok: true })
}
function errors(page: Page): string[] {
  const found: string[] = []
  page.on('pageerror', (e) => found.push(e.message))
  page.on('console', (msg) => { if (msg.type() === 'error') found.push(msg.text()) })
  return found
}
async function triggers(): Promise<number> {
  return ((await (await fetch(`${probe}/status`)).json()) as { triggers: number }).triggers
}
async function connect(page: Page) {
  await page.goto(`${origin}/#token=${token}`)
  await expect(page.getByTestId('connection-status')).toHaveText('Connected')
  const cc = core(page, 'command-center')
  await expect(cc).not.toHaveAttribute('data-state', 'DISCONNECTED')
  return cc
}
const core = (page: Page, variant: string) => page.locator(`[data-variant="${variant}"]`)

test('Command Center core follows real CoreSignals telemetry', async ({ page }) => {
  const consoleErrors = errors(page)
  await page.goto(`${origin}/#token=${token}`)
  await expect(page.getByTestId('connection-status')).toHaveText('Connected')
  const cc = core(page, 'command-center')
  await expect(cc).toBeVisible()
  await expect(cc).not.toHaveAttribute('data-state', 'DISCONNECTED')
  await expect(cc).toHaveAttribute('data-motion', 'reduced')

  await step('/turn-start')
  await step('/playback')
  await expect(cc).toHaveAttribute('data-state', 'SPEAKING', { timeout: 2000 })

  const audit = await new AxeBuilder({ page }).analyze()
  expect(audit.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')).toEqual([])

  await step('/cancel')
  await expect(cc).not.toHaveAttribute('data-state', 'SPEAKING', { timeout: 500 })
  expect(consoleErrors).toEqual([])
})

test('reload resyncs from the connect snapshot', async ({ page }) => {
  const consoleErrors = errors(page)
  await step('/turn-start')
  await step('/playback')
  await page.goto(`${origin}/#token=${token}`)
  const cc = core(page, 'command-center')
  await expect(cc).toHaveAttribute('data-state', 'SPEAKING', { timeout: 2000 })
  await step('/cancel')
  await expect(cc).not.toHaveAttribute('data-state', 'SPEAKING', { timeout: 500 })
  await step('/turn-start')
  await step('/playback')
  await page.goto(`${origin}/#token=${token}`)
  await expect(page.getByTestId('connection-status')).toHaveText('Connected')
  await expect(cc).toHaveAttribute('data-state', 'SPEAKING', { timeout: 2000 })
  await step('/cancel')
  expect(consoleErrors).toEqual([])
})

test('real startup lifecycle reaches Ready', async ({ page }) => {
  const consoleErrors = errors(page)
  const cc = await connect(page)
  await step('/activate')
  await step('/clap')
  await expect(cc).toHaveAttribute('data-state', 'STARTING', { timeout: 2000 })
  await expect(cc.getByRole('status')).toContainText('Confirming activation')
  await step('/startup/acknowledged')
  await expect(cc.getByRole('status')).toContainText('Activating')
  await step('/startup/initializing')
  await expect(cc.getByRole('status')).toContainText('Preparing services and music')
  await expect(cc.locator('[data-tick="lit"]')).toHaveCount(3)
  await step('/startup/announcing')
  await expect(cc.getByRole('status')).toContainText('Welcome')
  await expect(cc.locator('[data-tick="lit"]')).toHaveCount(4)
  await step('/apps')
  await expect(cc.getByRole('status')).toContainText('Apps: 1 ready, 1 failed')
  await step('/startup/ready')
  await expect(cc).toHaveAttribute('data-state', 'IDLE')
  await expect(cc.getByRole('status')).toContainText('Ready')
  expect(consoleErrors).toEqual([])
})

test('degraded startup shows limitations and the apps line', async ({ page }) => {
  const cc = await connect(page)
  await step('/activate')
  await step('/startup/acknowledged')
  await step('/startup/initializing')
  await step('/apps')
  await step('/startup/degraded?partial=1')
  await expect(cc.getByRole('status')).toContainText('Ready with limitations')
  await expect(cc.getByRole('status')).toContainText('Apps: 1 ready, 1 failed')
})

test('cancelled startup shows Stopping then cleanup returns to Idle', async ({ page }) => {
  const cc = await connect(page)
  await step('/activate')
  await step('/startup/acknowledged')
  await step('/startup/announcing')
  await step('/cancel-startup')
  await expect(cc).toHaveAttribute('data-state', 'STOPPING', { timeout: 2000 })
  await expect(cc.getByRole('status')).toContainText('Stopping')
  await step('/cleanup')
  await expect(cc).toHaveAttribute('data-state', 'IDLE', { timeout: 2000 })
  await expect(cc.getByRole('status')).toContainText('Idle')
})

test('reload during announcing restores the phase without replaying startup', async ({ page }) => {
  const cc = await connect(page)
  await step('/activate')
  await step('/startup/acknowledged')
  await step('/startup/announcing')
  await expect(cc.getByRole('status')).toContainText('Welcome')
  const before = await triggers()
  await page.goto(`${origin}/#token=${token}`)
  await expect(page.getByTestId('connection-status')).toHaveText('Connected')
  await expect(cc).toHaveAttribute('data-state', 'STARTING', { timeout: 2000 })
  await expect(cc.getByRole('status')).toContainText('Welcome')
  expect(await triggers()).toBe(before)
})

// Overlay live coverage: overlay.html without an Electron bridge is the fixture review harness, so
// the native overlay is covered by desktop-command-center.spec.ts and the bridge replay tests.
