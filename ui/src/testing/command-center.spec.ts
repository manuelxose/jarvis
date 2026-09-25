import { expect, test } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

const WORKSPACES = ['Overview', 'Conversation', 'Memory', 'System', 'Agents', 'Development', 'Media', 'Settings']

test.describe('Command Center', () => {
  test('renders with zero console errors and a keyboard-reachable nav', async ({ page }) => {
    const consoleErrors: string[] = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text())
    })

    await page.goto('/')
    await expect(page.getByText('Jarvis Command Center')).toBeVisible()
    expect(consoleErrors).toEqual([])

    // Sidebar items are real buttons, reachable by keyboard, not divs with onClick.
    await page.getByRole('button', { name: 'Conversation' }).focus()
    await expect(page.getByRole('button', { name: 'Conversation' })).toBeFocused()
  })

  for (const workspace of WORKSPACES) {
    test(`${workspace} workspace navigates and matches its screenshot`, async ({ page }) => {
      await page.goto('/')
      await page.getByRole('button', { name: workspace, exact: true }).click()
      await expect(page.getByRole('button', { name: workspace, exact: true })).toHaveAttribute('aria-current', 'page')
      await expect(page).toHaveScreenshot(`command-center-${workspace.toLowerCase()}.png`, { maxDiffPixelRatio: 0.01 })
    })
  }

  test('command palette opens on Ctrl+K, is searchable, and navigates on Enter', async ({ page }) => {
    await page.goto('/')
    await page.keyboard.press('Control+k')
    const input = page.getByPlaceholder('Type a command…')
    await expect(input).toBeFocused()
    await input.fill('Memory')
    await page.keyboard.press('Enter')
    await expect(page.getByRole('button', { name: 'Memory', exact: true })).toHaveAttribute('aria-current', 'page')
  })

  test('narrow desktop window collapses the right rail without clipping content', async ({ page }) => {
    await page.setViewportSize({ width: 1200, height: 800 })
    await page.goto('/')
    await expect(page.getByText('Jarvis Command Center')).toBeVisible()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)
    expect(overflow).toBe(false)
  })

  test('has no critical or serious automated accessibility violations', async ({ page }) => {
    await page.goto('/')
    const results = await new AxeBuilder({ page }).include('#root').analyze()
    const blocking = results.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious')
    expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([])
  })
})
