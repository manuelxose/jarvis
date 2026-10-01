/// <reference types="node" />
import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { expect, test } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

const REPO_ROOT = resolve(import.meta.dirname, '..', '..', '..')
const TOKEN = 'pw-live-t0k'

let child: ChildProcess
let origin = ''

test.describe.configure({ mode: 'serial' })

test.beforeAll(async () => {
  child = spawn(
    'python3',
    ['-m', 'jarvis.observability.event_stream', '--port', '0', '--token', TOKEN, '--demo', '--static', resolve(REPO_ROOT, 'ui', 'dist')],
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
  origin = `http://127.0.0.1:${port}`
})

test.afterAll(() => {
  child?.kill('SIGTERM')
})

test.describe('live shell', () => {
  test('connects, hides the token, shows live state and metrics', async ({ page }) => {
    await page.goto(`${origin}/#token=${TOKEN}`)
    const connection = page.getByTestId('connection-status')
    await expect(connection).toHaveText('Connected')
    expect(page.url()).not.toContain(TOKEN)
    expect(page.url()).not.toContain('token')
    await expect(page.getByTestId('live-status')).toContainText(/Assistant state: listening/)
    await expect(page.getByRole('region', { name: 'System' }).getByText('CPU')).toBeVisible()
    await expect(page.getByText('No live metrics')).toHaveCount(0)
  })

  test('first Tab reaches the skip link and Enter moves focus to main', async ({ page }) => {
    await page.goto(`${origin}/#token=${TOKEN}`)
    await expect(page.getByTestId('connection-status')).toHaveText('Connected')
    await page.keyboard.press('Tab')
    const skip = page.getByRole('link', { name: 'Skip to main content' })
    await expect(skip).toBeFocused()
    await expect(skip).toBeInViewport()
    await page.keyboard.press('Enter')
    await expect(page.locator('#main')).toBeFocused()
  })

  test('workspace navigation works by keyboard alone', async ({ page }) => {
    await page.goto(`${origin}/#token=${TOKEN}`)
    await page.getByRole('navigation', { name: 'Workspaces' }).focus()
    const system = page.getByRole('button', { name: 'System', exact: true })
    for (let i = 0; i < 8 && !(await system.evaluate((el) => el === document.activeElement)); i++) {
      await page.keyboard.press('Tab')
    }
    await expect(system).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(system).toHaveAttribute('aria-current', 'page')
  })

  test('has no serious or critical axe violations', async ({ page }) => {
    await page.goto(`${origin}/#token=${TOKEN}`)
    await expect(page.getByTestId('connection-status')).toHaveText('Connected')
    const results = await new AxeBuilder({ page }).analyze()
    const blocking = results.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious')
    expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([])
  })

  test('without a token the shell shows offline and no console errors', async ({ page }) => {
    const consoleErrors: string[] = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text())
    })
    await page.goto('/')
    await expect(page.getByTestId('connection-status')).toHaveText('Offline — no event stream')
    await expect(page.getByText('No live metrics')).toBeVisible()
    expect(consoleErrors).toEqual([])
  })
})
