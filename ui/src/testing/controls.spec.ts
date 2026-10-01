/// <reference types="node" />
import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { expect, test, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

const root = resolve(import.meta.dirname, '..', '..', '..')
const token = 'playwright-controls-session'
let server: ChildProcess
let origin: string
let probe: string

test.describe.configure({ mode: 'serial' })
test.beforeAll(async () => {
  server = spawn('python3', ['ui/src/testing/fixtures/control_server.py'], {
    cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'src'), CONTROL_TEST_TOKEN: token },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  const lines = createInterface({ input: server.stdout! })
  const [port, statusPort] = await new Promise<[string, string]>((ready, reject) => {
    const timer = setTimeout(() => reject(new Error('control fixture did not start')), 10000)
    server.once('exit', (code) => { clearTimeout(timer); reject(new Error(`control fixture exited (${code})`)) })
    lines.on('line', line => {
      const match = /^READY (\d+) (\d+)$/.exec(line)
      if (match) { clearTimeout(timer); ready([match[1]!, match[2]!]) }
    })
  })
  origin = `http://127.0.0.1:${port}`
  probe = `http://127.0.0.1:${statusPort}`
})
test.afterAll(() => { server?.kill('SIGTERM') })

async function actions(): Promise<string[]> {
  const response = await fetch(`${probe}/status`)
  return ((await response.json()) as { actions: string[] }).actions
}
async function connect(page: Page) {
  await page.goto(`${origin}/#token=${token}`)
  await expect(page.getByTestId('connection-status')).toHaveText('Connected')
  expect(page.url()).not.toContain(token)
}
function errors(page: Page): string[] {
  const found: string[] = []
  page.on('pageerror', e => found.push(e.message))
  page.on('console', msg => { if (msg.type() === 'error') found.push(msg.text()) })
  return found
}

test('cancel and Escape do not dispatch; keyboard confirm dispatches once with accessible card', async ({ page }) => {
  const consoleErrors = errors(page)
  await connect(page)
  const before = (await actions()).length
  const activate = page.getByRole('button', { name: 'Activate', exact: true })
  await activate.click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('heading', { name: 'Activate Jarvis' })).toBeVisible()
  await dialog.getByRole('button', { name: 'Cancel' }).click()
  await expect(activate).toBeFocused()
  await activate.click()
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  expect(await actions()).toHaveLength(before)
  await activate.focus()
  await page.keyboard.press('Enter')
  await expect(dialog).toBeVisible()
  const audit = await new AxeBuilder({ page }).include('[role="dialog"]').analyze()
  expect(audit.violations.filter(v => v.impact === 'serious' || v.impact === 'critical')).toEqual([])
  const confirm = dialog.getByRole('button', { name: 'Confirm Activate' })
  await confirm.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('status').filter({ hasText: /Activation request accepted by daemon/ }).first()).toBeVisible()
  await expect.poll(async () => (await actions()).length).toBe(before + 1)
  expect((await actions()).at(-1)).toBe('activate')
  expect(consoleErrors).toEqual([])
})

test('confirmation card remains usable in a short app window', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 200 })
  await connect(page)
  for (const action of ['Activate', 'Sleep']) {
    await page.getByRole('button', { name: action, exact: true }).click()
    const dialog = page.getByRole('dialog')
    const bounds = await dialog.boundingBox()
    expect(bounds).not.toBeNull()
    expect(bounds!.y).toBeGreaterThanOrEqual(0)
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(200)
    await dialog.getByRole('button', { name: `Confirm ${action}` }).focus()
    await expect(dialog.getByRole('button', { name: `Confirm ${action}` })).toBeInViewport()
    await page.keyboard.press('Escape')
  }
})

test('endpoint rejects bad token, foreign origin, malformed body and never dispatches them', async ({ request }) => {
  const before = (await actions()).length
  const base = { 'Content-Type': 'application/json' }
  expect((await request.post(`${origin}/control`, { headers: { ...base, 'X-Jarvis-Token': 'bad' }, data: { action: 'sleep' } })).status()).toBe(401)
  expect((await request.post(`${origin}/control`, { headers: { ...base, 'X-Jarvis-Token': token, Origin: 'http://evil.example' }, data: { action: 'sleep' } })).status()).toBe(403)
  expect((await request.post(`${origin}/control`, { headers: { ...base, 'X-Jarvis-Token': token }, data: { action: 'restart' } })).status()).toBe(400)
  expect(await actions()).toHaveLength(before)
})

test('callback failure is announced as denial; repeated confirm does not dispatch twice', async ({ page, request }) => {
  const consoleErrors = errors(page)
  await connect(page)
  const before = (await actions()).length
  await request.post(`${probe}/fail`)
  try {
    await page.getByRole('button', { name: 'Sleep', exact: true }).click()
    const dialog = page.getByRole('dialog')
    const confirm = dialog.getByRole('button', { name: 'Confirm Sleep' })
    await confirm.dblclick()
    await expect(dialog.getByRole('status').filter({ hasText: /denied \(503\)/ })).toBeVisible()
    expect(await actions()).toHaveLength(before)
    expect(consoleErrors.filter(message => !message.includes('server responded with a status of 503 (Service Unavailable)'))).toEqual([])
  } finally {
    await request.post(`${probe}/recover`)
  }
})

test('sleep confirmation dispatches once; disconnected stream disables controls', async ({ page }) => {
  const consoleErrors = errors(page)
  await connect(page)
  const before = (await actions()).length
  await page.getByRole('button', { name: 'Sleep', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Confirm Sleep' }).click()
  await expect(page.getByRole('status').filter({ hasText: /Sleep request accepted by daemon/ }).first()).toBeVisible()
  await expect.poll(async () => (await actions()).length).toBe(before + 1)
  expect((await actions()).at(-1)).toBe('sleep')
  server.kill('SIGTERM') // a real dropped daemon stream, not a mocked browser response
  await expect(page.getByTestId('connection-status')).toContainText('Offline', { timeout: 10000 })
  await expect(page.getByRole('button', { name: 'Activate', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Sleep', exact: true })).toBeDisabled()
  expect(consoleErrors.filter(message => !message.includes('Failed to load resource: net::ERR_CONNECTION_REFUSED'))).toEqual([])
})
