import { expect, test } from '@playwright/test'
import type { DesktopMode } from '../shell/jarvis-desktop'

declare global {
  interface Window {
    overlayTest: { interactions: boolean[]; requests: DesktopMode[]; notify(mode: DesktopMode): void; listeners(): number; releaseMode(): void }
  }
}

// Test-only bridge. This suite proves DOM/bridge contracts, NOT Windows input receipt or drag.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    let mode: DesktopMode = 'OVERLAY_COMPACT'
    const listeners = new Set<(value: DesktopMode) => void>()
    const interactions: boolean[] = [], requests: DesktopMode[] = []
    const notify = (value: DesktopMode) => { mode = value; listeners.forEach(fn => fn(value)) }
    let releaseMode = () => {}
    window.overlayTest = { interactions, requests, notify, listeners: () => listeners.size, releaseMode: () => releaseMode() }
    window.jarvisDesktop = {
      getMode: () => {
        if (location.search.includes('reject-mode')) return Promise.reject(new Error('test bridge unavailable'))
        if (location.search.includes('late-mode')) return new Promise(resolve => { releaseMode = () => resolve('OVERLAY_COMPACT') })
        return Promise.resolve(mode)
      },
      requestMode(value) { requests.push(value); notify(value) },
      onModeChanged(fn) { listeners.add(fn); return () => { listeners.delete(fn) } },
      setOverlayInteractive(value) { interactions.push(value) }, moveOverlayTo() {},
    }
  })
})

test('native bridge stub synchronizes content and controls, transparent canvas and drag policy', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.setViewportSize({ width: 420, height: 160 })
  await page.goto('/overlay.html')
  await expect(page.getByRole('button', { name: 'Expand', exact: true })).toBeVisible()
  await expect(page.getByRole('group', { name: 'Simulate overlay state' })).toBeHidden()
  expect(await page.evaluate(() => ['html', 'body', '#root', '[data-overlay-root]'].map(s => getComputedStyle(document.querySelector(s)!).backgroundColor))).toEqual(Array(4).fill('rgba(0, 0, 0, 0)'))
  expect(await page.locator('[data-overlay-drag]').evaluate(el => getComputedStyle(el).getPropertyValue('-webkit-app-region'))).toBe('drag')
  expect(await page.getByRole('button', { name: 'Expand', exact: true }).evaluate(el => getComputedStyle(el).getPropertyValue('-webkit-app-region'))).toBe('no-drag')
  await page.mouse.move(2, 2)
  expect(await page.evaluate(() => window.overlayTest.interactions.at(-1))).toBe(false)
  await page.locator('[data-overlay-drag]').hover()
  await expect.poll(() => page.evaluate(() => window.overlayTest.interactions.at(-1))).toBe(true)
  expect(await page.locator('[data-overlay-interactive]').evaluate(el => el.getBoundingClientRect().bottom <= innerHeight)).toBe(true)
  await page.getByRole('button', { name: 'Expand', exact: true }).click()
  await page.setViewportSize({ width: 420, height: 520 })
  await expect(page.getByRole('button', { name: 'Collapse' })).toBeVisible()
  expect(await page.locator('[data-overlay-interactive]').evaluate(el => el.getBoundingClientRect().bottom <= innerHeight)).toBe(true)
  await expect(page.locator('[data-overlay-interactive] [tabindex="0"]')).toBeVisible()
  await page.getByRole('button', { name: 'Collapse' }).click()
  await expect(page.getByRole('button', { name: 'Expand', exact: true })).toBeVisible()
  // A main-process notification, not optimistic local state, drives the card.
  await page.evaluate(() => window.overlayTest.notify('OVERLAY_EXPANDED'))
  await expect(page.getByRole('button', { name: 'Collapse' })).toBeVisible()
  await page.getByRole('button', { name: 'Open center' }).click()
  expect(await page.evaluate(() => window.overlayTest.requests)).toEqual(['OVERLAY_EXPANDED', 'OVERLAY_COMPACT', 'COMMAND_CENTER_WINDOWED'])
  expect(await page.evaluate(() => window.overlayTest.listeners())).toBe(2)
  expect(errors).toEqual([])
})

test('late getMode response cannot overwrite a newer native notification', async ({ page }) => {
  await page.goto('/overlay.html?late-mode')
  await expect(page.getByRole('button', { name: 'Expand', exact: true })).toBeVisible()
  await page.evaluate(() => window.overlayTest.notify('OVERLAY_EXPANDED'))
  await expect(page.getByRole('button', { name: 'Collapse' })).toBeVisible()
  await page.evaluate(() => window.overlayTest.releaseMode())
  await expect(page.getByRole('button', { name: 'Collapse' })).toBeVisible()
})

test('rejected bridge read defaults to compact pass-through', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/overlay.html?reject-mode')
  await expect(page.getByRole('button', { name: 'Expand', exact: true })).toBeVisible()
  expect(await page.evaluate(() => window.overlayTest.interactions.at(-1))).toBe(false)
  expect(errors).toEqual([])
})

test('bridge stub leaves margin and lifecycle events pass-through without duplicate movement IPC', async ({ page }) => {
  await page.goto('/overlay.html')
  const card = page.locator('[data-overlay-interactive]')
  await card.hover()
  await expect.poll(() => page.evaluate(() => window.overlayTest.interactions.at(-1))).toBe(true)
  const count = await page.evaluate(() => window.overlayTest.interactions.length)
  await card.hover()
  expect(await page.evaluate(() => window.overlayTest.interactions.length)).toBe(count)
  await page.evaluate(() => window.dispatchEvent(new Event('blur')))
  expect(await page.evaluate(() => window.overlayTest.interactions.at(-1))).toBe(false)
  await page.mouse.move(2, 2)
  await card.hover()
  await page.evaluate(() => document.dispatchEvent(new Event('mouseleave')))
  expect(await page.evaluate(() => window.overlayTest.interactions.at(-1))).toBe(false)
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')))
  expect(await page.evaluate(() => window.overlayTest.interactions.at(-1))).toBe(false)
})
