import { expect, test } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

const VARIANTS = ['compact-overlay', 'expanded-overlay', 'command-center', 'startup']
const STATES = ['IDLE', 'LISTENING', 'THINKING', 'SPEAKING', 'EXECUTING', 'SUCCESS', 'ERROR', 'DISCONNECTED', 'STARTING', 'STOPPING']
const URL = '/visual-core-fixtures.html'
const STARTUP_SCENARIOS = ['startup-full', 'startup-reduced', 'startup-fast', 'startup-muted', 'startup-interrupted']

test.describe('Visual core fixtures', () => {
  test('renders 40 cells with distinct states and visible labels per variant', async ({ page }) => {
    await page.goto(URL)
    const grid = page.locator('[data-grid="main"]')
    await expect(grid.locator('[data-cell]')).toHaveCount(40)
    for (const variant of VARIANTS) {
      const row = grid.locator(`[data-row="${variant}"]`)
      const cores = row.locator('[data-variant]')
      await expect(cores).toHaveCount(10)
      const states = await cores.evaluateAll(els => els.map(e => e.getAttribute('data-state')))
      expect([...states].sort()).toEqual([...STATES].sort())
      for (const el of await row.locator('[role="status"]').all()) {
        await expect(el).toBeVisible()
        expect((await el.innerText()).trim().length).toBeGreaterThan(0)
      }
    }
  })

  test('grid and each variant row match screenshots', async ({ page }) => {
    await page.goto(URL)
    await expect(page.locator('[data-grid="main"]')).toHaveScreenshot('grid.png', { maxDiffPixelRatio: 0.01 })
    for (const variant of VARIANTS) {
      await expect(page.locator(`[data-row="${variant}"]`)).toHaveScreenshot(`row-${variant}.png`, { maxDiffPixelRatio: 0.01 })
    }
  })

  test('has no axe violations', async ({ page }) => {
    await page.goto(URL)
    await expect(page.locator('[data-cell]').first()).toBeVisible()
    const results = await new AxeBuilder({ page }).analyze()
    expect(results.violations).toEqual([])
  })

  test('uses no canvas and requests no WebGL context', async ({ page }) => {
    await page.addInitScript(() => {
      const w = window as unknown as { __ctx: string[] }
      w.__ctx = []
      const orig = HTMLCanvasElement.prototype.getContext
      HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, ...rest: unknown[]) {
        w.__ctx.push(type)
        return (orig as (...a: unknown[]) => unknown).call(this, type, ...rest) as never
      } as typeof orig
    })
    await page.goto(URL)
    await expect(page.locator('[data-cell]').first()).toBeVisible()
    await expect(page.locator('canvas')).toHaveCount(0)
    expect(await page.evaluate(() => (window as unknown as { __ctx: string[] }).__ctx)).toEqual([])
  })

  test('motion attributes follow setting and OS preference', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await page.goto(`${URL}?state=thinking&variant=command-center&motion=full`)
    await expect(page.locator('[data-variant]')).toHaveAttribute('data-motion', 'full')
    await page.goto(`${URL}?state=thinking&variant=command-center&motion=disabled`)
    await expect(page.locator('[data-variant]')).toHaveAttribute('data-motion', 'disabled')
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto(`${URL}?state=thinking&variant=command-center&motion=full`)
    await expect(page.locator('[data-variant]')).toHaveAttribute('data-motion', 'reduced')
  })

  test('cell without a source falls back to static DISCONNECTED', async ({ page }) => {
    await page.goto(`${URL}?nosource=1&variant=compact-overlay`)
    const core = page.locator('[data-variant]')
    await expect(core).toHaveAttribute('data-renderer', 'static')
    await expect(core).toHaveAttribute('data-state', 'DISCONNECTED')
    await expect(core.locator('[role="status"]')).toBeVisible()
  })

  for (const scenario of STARTUP_SCENARIOS) {
    for (const variant of ['startup', 'compact-overlay']) {
      test(`${scenario} in ${variant} matches screenshot and has no axe violations`, async ({ page }) => {
        await page.goto(`${URL}?scenario=${scenario}&variant=${variant}`)
        const core = page.locator('[data-variant]')
        const interrupted = scenario === 'startup-interrupted'
        await expect(core).toHaveAttribute('data-state', interrupted ? 'STOPPING' : 'STARTING')
        await expect(core).toHaveAttribute('data-startup-phase', interrupted ? 'cancelled' : 'announcing')
        if (!interrupted) {
          await expect(core.locator('[data-tick="lit"]')).toHaveCount(4)
          await expect(core.locator('[role="status"]')).toContainText('Apps: 2 ready, 0 failed')
        }
        if (scenario === 'startup-reduced') await expect(core).toHaveAttribute('data-motion', 'reduced')
        await expect(page.locator('[data-cell]')).toHaveScreenshot(`${scenario}-${variant}.png`, { maxDiffPixelRatio: 0.01 })
        expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
      })
    }
  }
})
