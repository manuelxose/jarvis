/// <reference types="node" />
import { spawn, type ChildProcess } from 'node:child_process'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { expect, test, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

const REPO_ROOT = resolve(import.meta.dirname, '..', '..', '..')
const TOKEN = 'pw-orb-t0k'

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

const WEBGL_ORB = 'canvas[data-renderer="webgl2"]'

function collectConsoleErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text())
  })
  page.on('pageerror', (error) => errors.push(error.message))
  return errors
}

async function connect(page: Page) {
  await page.goto(`${origin}/#token=${TOKEN}`)
  await expect(page.getByTestId('connection-status')).toHaveText('Connected')
}

test.describe('jarvis orb (live)', () => {
  test('renders non-blank WebGL2 pixels with an accessible label', async ({ page }) => {
    const errors = collectConsoleErrors(page)
    // The orb does not set preserveDrawingBuffer, so the buffer is cleared after compositing and
    // reduced motion draws only once. The test forces it on so the drawn pixels can be read back.
    await page.addInitScript(() => {
      const original = HTMLCanvasElement.prototype.getContext
      HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, attrs?: unknown) {
        if (type === 'webgl2') attrs = { ...(attrs as object), preserveDrawingBuffer: true }
        return (original as (...args: unknown[]) => unknown).call(this, type, attrs)
      } as typeof HTMLCanvasElement.prototype.getContext
    })
    await connect(page)
    const orb = page.locator(WEBGL_ORB).first()
    await expect(orb).toBeVisible()
    await expect(orb).toHaveAttribute('aria-label', /^Jarvis visual core/)

    await expect
      .poll(
        () =>
          orb.evaluate((canvas: HTMLCanvasElement) => {
            const copy = document.createElement('canvas')
            copy.width = canvas.width
            copy.height = canvas.height
            const ctx = copy.getContext('2d')!
            ctx.drawImage(canvas, 0, 0)
            const data = ctx.getImageData(0, 0, copy.width, copy.height).data
            let opaque = 0
            for (let i = 3; i < data.length; i += 4) if (data[i]! > 0) opaque++
            return opaque
          }),
        { timeout: 5_000 },
      )
      .toBeGreaterThan(0)
    expect(errors).toEqual([])
  })

  test('data-orb-state follows the live event stream', async ({ page }) => {
    await connect(page)
    const orb = page.locator(WEBGL_ORB).first()
    await expect
      .poll(async () => ['listening', 'speaking'].includes((await orb.getAttribute('data-orb-state')) ?? ''), { timeout: 10_000 })
      .toBe(true)
    await expect(page.getByTestId('live-status')).toContainText(/Assistant state: \w+/)
  })

  test('falls back to the CSS core when WebGL2 is unavailable, without console errors', async ({ page }) => {
    const errors = collectConsoleErrors(page)
    await page.addInitScript(() => {
      const original = HTMLCanvasElement.prototype.getContext
      HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, ...rest: unknown[]) {
        if (type === 'webgl2') return null
        return (original as (...args: unknown[]) => unknown).call(this, type, ...rest)
      } as typeof HTMLCanvasElement.prototype.getContext
    })
    await connect(page)
    const fallback = page.locator('[data-renderer="fallback"]').first()
    await expect(fallback).toBeVisible()
    await expect(fallback.getByRole('img', { name: /^Jarvis visual core/ })).toBeVisible()
    await expect(page.locator(WEBGL_ORB)).toHaveCount(0)
    expect(errors).toEqual([])
  })

  test('without a token the orb still renders and there are no console errors', async ({ page }) => {
    const errors = collectConsoleErrors(page)
    await page.goto(origin)
    await expect(page.getByTestId('connection-status')).toHaveText('Offline — no event stream')
    await expect(page.locator('[data-renderer]').first()).toBeVisible()
    await expect(page.locator('[data-renderer]').first()).toHaveAttribute('data-orb-state', /.+/)
    expect(errors).toEqual([])
  })

  test('has no serious or critical axe violations while the orb renders', async ({ page }) => {
    await connect(page)
    await expect(page.locator(WEBGL_ORB).first()).toBeVisible()
    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()
    const blocking = results.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious')
    expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([])
  })

  test('reduced motion schedules no continuous animation loop', async ({ page }) => {
    await page.addInitScript(() => {
      const w = window as unknown as { __raf: number }
      w.__raf = 0
      const original = window.requestAnimationFrame.bind(window)
      window.requestAnimationFrame = (cb) => {
        w.__raf++
        return original(cb)
      }
    })
    await connect(page)
    await page.waitForTimeout(1_000)
    const count = await page.evaluate(() => (window as unknown as { __raf: number }).__raf)
    expect(count).toBeLessThan(5)
  })

  test.describe('with motion allowed', () => {
    test.use({ reducedMotion: 'no-preference' })

    test('runs a continuous animation loop', async ({ page }) => {
      await page.addInitScript(() => {
        const w = window as unknown as { __raf: number }
        w.__raf = 0
        const original = window.requestAnimationFrame.bind(window)
        window.requestAnimationFrame = (cb) => {
          w.__raf++
          return original(cb)
        }
      })
      await connect(page)
      await page.waitForTimeout(1_000)
      const count = await page.evaluate(() => (window as unknown as { __raf: number }).__raf)
      expect(count).toBeGreaterThan(10)
    })
  })
})
