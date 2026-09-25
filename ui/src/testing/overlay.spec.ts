import { expect, test } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

const MODES = ['compact-idle', 'listening', 'speaking', 'processing', 'expanded', 'notification', 'error', 'disconnected']

test.describe('Desktop Overlay', () => {
  test('renders with zero console errors', async ({ page }) => {
    const consoleErrors: string[] = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text())
    })
    await page.goto('/overlay.html')
    await expect(page.getByRole('button', { name: 'listening', exact: true })).toBeVisible()
    expect(consoleErrors).toEqual([])
  })

  for (const mode of MODES) {
    test(`${mode} state matches its screenshot`, async ({ page }) => {
      await page.goto('/overlay.html')
      await page.getByRole('button', { name: mode, exact: true }).click()
      await expect(page).toHaveScreenshot(`overlay-${mode}.png`, { maxDiffPixelRatio: 0.01 })
    })
  }

  test('state controls are keyboard-reachable', async ({ page }) => {
    await page.goto('/overlay.html')
    await page.getByRole('button', { name: 'compact-idle', exact: true }).focus()
    await expect(page.getByRole('button', { name: 'compact-idle', exact: true })).toBeFocused()
  })

  test('has no critical or serious automated accessibility violations', async ({ page }) => {
    await page.goto('/overlay.html')
    const results = await new AxeBuilder({ page }).analyze()
    const blocking = results.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious')
    expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([])
  })
})
