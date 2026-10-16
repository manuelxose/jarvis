import { expect, test, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

const URL = '/visual-core-fixtures.html'
const VARIANTS = ['compact-overlay', 'command-center']
const STATES: Record<string, string> = {
  IDLE: 'Idle', LISTENING: 'Listening', THINKING: 'Thinking', SPEAKING: 'Speaking', EXECUTING: 'Executing',
  SUCCESS: 'Done', ERROR: 'Error', DISCONNECTED: 'Offline', STARTING: 'Starting', STOPPING: 'Stopping',
}

type Ctl = {
  set(p: Record<string, unknown>): void; replaceSource(): void; setMotion(m: string): void; setMounted(o: boolean): void; subscribers(): number
}
declare global { interface Window { __vc: Ctl; __raf: number; __writes: number } }

async function open(page: Page, variant: string, motion: string, reduced: 'reduce' | 'no-preference' = 'no-preference') {
  await page.emulateMedia({ reducedMotion: reduced })
  await page.addInitScript(() => {
    window.__raf = 0
    const raf = window.requestAnimationFrame.bind(window)
    window.requestAnimationFrame = cb => raf(t => { window.__raf++; cb(t) })
  })
  await page.goto(`${URL}?a11y=1&variant=${variant}&motion=${motion}`)
  await page.waitForFunction(() => !!window.__vc)
  await expect(page.locator('[data-variant]')).toHaveAttribute('data-renderer', 'svg')
}

const status = (page: Page) => page.locator('[role="status"]')
const watchWrites = (page: Page) => page.evaluate(() => {
  window.__writes = 0
  new MutationObserver(m => { window.__writes += m.length }).observe(document.querySelector('[data-announcement]')!, { childList: true, characterData: true, subtree: true })
})

for (const variant of VARIANTS) {
  test.describe(variant, () => {
    test('all ten states expose text label and a glyph, not color only', async ({ page }) => {
      await open(page, variant, 'disabled')
      for (const [state, label] of Object.entries(STATES)) {
        await page.evaluate(s => window.__vc.set({ state: s }), state)
        const core = page.locator('[data-variant]')
        await expect(core).toHaveAttribute('data-state', state)
        await expect(core.locator('[class*="label"]').first()).toContainText(label)
        expect(await core.locator('svg path, svg circle, svg rect').count()).toBeGreaterThan(1)
        await expect(status(page)).toHaveCount(1)
        await expect(status(page)).toHaveText(new RegExp(label), { timeout: 2000 })
      }
    })

    for (const mode of ['disabled', 'reduced']) {
      test(`${mode} motion: no RAF, no CSS motion, axe clean`, async ({ page }) => {
        await open(page, variant, mode)
        await page.evaluate(() => window.__vc.set({ state: 'SPEAKING' }))
        await page.waitForTimeout(300)
        expect(await page.evaluate(() => window.__raf)).toBe(0)
        const anims = await page.evaluate(() => document.getAnimations().length)
        expect(anims).toBe(0)
        expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
      })
    }

    test('full motion does animate (renderer is not inert)', async ({ page }) => {
      await open(page, variant, 'full')
      await expect.poll(() => page.evaluate(() => window.__raf)).toBeGreaterThan(3)
    })

    test('OS reduced-motion toggles at runtime and stops RAF', async ({ page }) => {
      await open(page, variant, 'full')
      const core = page.locator('[data-variant]')
      await expect(core).toHaveAttribute('data-motion', 'full')
      await page.evaluate(() => window.__vc.set({ state: 'SPEAKING' }))
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await expect(core).toHaveAttribute('data-motion', 'reduced')
      await expect(core).toHaveAttribute('data-state', 'SPEAKING')
      await page.waitForTimeout(100)
      const before = await page.evaluate(() => window.__raf)
      await page.waitForTimeout(300)
      expect(await page.evaluate(() => window.__raf)).toBe(before)
      await page.emulateMedia({ reducedMotion: 'no-preference' })
      await expect(core).toHaveAttribute('data-motion', 'full')
      await expect.poll(() => page.evaluate(() => window.__raf)).toBeGreaterThan(before)
      await page.evaluate(() => window.__vc.setMotion('disabled'))
      await expect(core).toHaveAttribute('data-motion', 'disabled')
    })

    test('announcements are coalesced, keep the latest terminal outcome, and ignore repeats', async ({ page }) => {
      await open(page, variant, 'disabled')
      await watchWrites(page)
      await page.evaluate(() => {
        const phases = ['acknowledged', 'initializing', 'announcing', 'ready']
        window.__vc.set({ state: 'STARTING', startup: 'acknowledged', apps: null })
        for (const p of phases) window.__vc.set({ state: 'STARTING', startup: p, apps: { ready: 1, failed: 0, pending: 1, total: 2 } })
        window.__vc.set({ state: 'STARTING', startup: 'failed', detail: 'Startup failed', apps: { ready: 1, failed: 1, pending: 0, total: 2 } })
      })
      await expect(status(page)).toContainText('Startup failed', { timeout: 2000 })
      await expect(status(page)).toContainText('Apps: 1 ready, 1 failed')
      expect(await page.evaluate(() => window.__writes)).toBeLessThanOrEqual(3)
      // Repeated identical state does not write again.
      await page.waitForTimeout(700)
      const settled = await page.evaluate(() => window.__writes)
      await page.evaluate(() => window.__vc.set({ state: 'STARTING', startup: 'failed', detail: 'Startup failed', apps: { ready: 1, failed: 1, pending: 0, total: 2 } }))
      await page.waitForTimeout(700)
      expect(await page.evaluate(() => window.__writes)).toBe(settled)
    })

    test('visible text is immediate while announcement lags; terminal ERROR arrives during debounce', async ({ page }) => {
      await open(page, variant, 'disabled')
      await page.evaluate(() => window.__vc.set({ state: 'LISTENING' }))
      await page.evaluate(() => window.__vc.set({ state: 'ERROR', detail: 'Provider unreachable' }))
      const core = page.locator('[data-variant]')
      await expect(core).toHaveAttribute('data-state', 'ERROR')
      await expect(core.locator('[class*="label"]').first()).toContainText('Provider unreachable')
      await expect(status(page)).toContainText('Provider unreachable', { timeout: 2000 })
      await expect(status(page)).not.toContainText('Listening')
    })

    test('announcement writes do not scale with signal reads or envelopes', async ({ page }) => {
      await open(page, variant, 'full')
      await page.evaluate(() => window.__vc.set({ state: 'SPEAKING' }))
      await page.waitForTimeout(700)
      await watchWrites(page)
      await page.waitForTimeout(800)
      expect(await page.evaluate(() => window.__writes)).toBe(0)
    })

    test('source replacement drops pending announcements; unmount cleans up', async ({ page }) => {
      await open(page, variant, 'disabled')
      await page.evaluate(() => window.__vc.set({ state: 'LISTENING' }))
      await page.evaluate(() => window.__vc.set({ state: 'THINKING' }))
      await page.evaluate(() => window.__vc.replaceSource())
      await expect(status(page)).toContainText('Thinking')
      await page.evaluate(() => { window.__vc.set({ state: 'EXECUTING' }) })
      await page.evaluate(() => window.__vc.setMounted(false))
      await expect(page.locator('[data-unmounted]')).toBeVisible()
      await expect(status(page)).toHaveCount(0)
      expect(await page.evaluate(() => window.__vc.subscribers())).toBe(0)
      await page.waitForTimeout(700)
      await expect(status(page)).toHaveCount(0)
    })
  })
}

test('missing source and disconnected state remain textual', async ({ page }) => {
  await page.goto(`${URL}?nosource=1&variant=command-center`)
  await expect(status(page)).toHaveText(/Offline/)
  await expect(page.locator('[data-variant]')).toContainText('Offline')
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([])
})
