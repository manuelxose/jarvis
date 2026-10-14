import { expect, test, type Page } from '@playwright/test'
import type { DesktopSettings } from '../shell/jarvis-desktop'

// Test-only, bounded restricted bridge; never implies native HWND/DPI evidence.
interface DesktopHarness {
  requests: string[]
  listeners: Set<(mode: string) => void>
  retired: ((mode: string) => void)[]
  subscribed: number
  removed: number
  pending: ((mode: string | null) => void)[]
  emit(mode: string): void
  resolve(mode: string | null): void
  failRequest: boolean
}
interface SettingsHarness {
  value: DesktopSettings
  changed: Set<(value: DesktopSettings) => void>
  opens: Set<() => void>
  calls: boolean[]
  write: 'ok' | 'reject' | 'unchanged' | 'pending'
  resolve?: (value: DesktopSettings) => void
  readResolve?: (value: DesktopSettings) => void
}
declare global { interface Window { desktopTest: DesktopHarness; settingsTest: SettingsHarness } }

async function settingsBridge(page: Page, initial: 'ok' | 'pending' | 'reject' = 'ok') {
  await bridge(page)
  await page.addInitScript(initialRead => {
    const h: SettingsHarness = {
      value: { launchAtLogin: false, shortcuts: [
        { action: 'toggleOverlay', accelerator: 'Control+Alt+Shift+J', registered: true },
        { action: 'toggleCommandCenter', accelerator: 'Control+Alt+Shift+C', registered: false },
      ] }, changed: new Set(), opens: new Set(), calls: [], write: 'ok',
    }
    window.settingsTest = h
    window.jarvisDesktop!.settings = {
      get: () => initialRead === 'reject' ? Promise.reject(new Error('IPC closed')) : initialRead === 'pending' ? new Promise(resolve => { h.readResolve = resolve }) : Promise.resolve(h.value),
      setLaunchAtLogin: value => {
        h.calls.push(value)
        if (h.write === 'reject') return Promise.reject(new Error('IPC closed'))
        if (h.write === 'pending') return new Promise(resolve => { h.resolve = resolve })
        if (h.write === 'ok') { h.value = { ...h.value, launchAtLogin: value }; h.changed.forEach(cb => cb(h.value)) }
        return Promise.resolve(h.value)
      },
      onChanged: cb => { h.changed.add(cb); return () => { h.changed.delete(cb) } },
      onOpenRequested: cb => { h.opens.add(cb); return () => { h.opens.delete(cb) } },
    }
  }, initial)
}

async function openSettings(page: Page) {
  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  return page.getByRole('switch', { name: 'Launch Jarvis desktop at sign-in' })
}

async function bridge(page: Page, initial: 'ready' | 'pending' | 'reject' = 'ready') {
  await page.addInitScript((initialMode) => {
    const harness: DesktopHarness = {
      requests: [], listeners: new Set(), retired: [], subscribed: 0, removed: 0, pending: [], failRequest: false,
      emit(mode) { for (const listener of this.listeners) listener(mode) },
      resolve(mode) { this.pending.splice(0).forEach((resolve) => resolve(mode)) },
    }
    window.desktopTest = harness
    // Structural bridge substitution only in this browser test document.
    Object.defineProperty(window, 'jarvisDesktop', { value: {
      getMode: () => initialMode === 'reject' ? Promise.reject(new Error('IPC unavailable')) : initialMode === 'pending' ? new Promise((resolve) => harness.pending.push(resolve)) : Promise.resolve('COMMAND_CENTER_WINDOWED'),
      requestMode: (mode: string) => {
        if (harness.failRequest) throw new Error('IPC unavailable')
        if (harness.requests.length >= 100) throw new Error('test request cap')
        harness.requests.push(mode)
      },
      onModeChanged: (callback: (mode: string) => void) => {
        harness.subscribed++; harness.listeners.add(callback)
        return () => { if (harness.listeners.delete(callback)) { harness.removed++; harness.retired.push(callback) } }
      },
      setOverlayInteractive: () => { throw new Error('unexpected overlay API') },
      moveOverlayTo: () => { throw new Error('unexpected OS API') },
    } })
  }, initial)
}

async function palette(page: Page, label: string) {
  await page.keyboard.press('Control+k')
  const input = page.getByPlaceholder('Type a command…')
  await expect(input).toBeFocused()
  await input.fill(label)
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog')).toHaveCount(0)
}

test('tray Settings navigation exposes truthful login preference and read-only shortcut status', async ({ page }) => {
  await settingsBridge(page)
  await page.goto('/')
  await page.evaluate(() => window.settingsTest.opens.forEach(cb => cb()))
  await expect(page.getByRole('button', { name: 'Settings', exact: true })).toHaveAttribute('aria-current', 'page')
  const toggle = page.getByRole('switch', { name: 'Launch Jarvis desktop at sign-in' })
  await expect(toggle).toBeEnabled()
  await expect(toggle).toHaveAttribute('aria-checked', 'false')
  await expect(page.getByText('Toggle overlay:').locator('..')).toContainText('Registered')
  await expect(page.getByText('Toggle Command Center:').locator('..')).toContainText('Unavailable')
  await expect(page.getByText(/Shortcuts are read-only here/)).toBeVisible()
  await toggle.focus(); await page.keyboard.press('Space')
  await expect(toggle).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByText('Desktop sign-in preference saved.')).toBeVisible()
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-checked', 'false')
  expect(await page.evaluate(() => window.settingsTest.calls)).toEqual([true, false])
  await page.evaluate(() => { const h = window.settingsTest; h.value = { ...h.value, launchAtLogin: true }; h.changed.forEach(cb => cb(h.value)) })
  await expect(toggle).toHaveAttribute('aria-checked', 'true')
  await page.getByRole('button', { name: 'Overview', exact: true }).click()
  expect(await page.evaluate(() => ({ changed: window.settingsTest.changed.size, opens: window.settingsTest.opens.size }))).toEqual({ changed: 0, opens: 1 })
})

test('desktop settings write rejection, unchanged OS state and timeout never imply success', async ({ page }) => {
  await settingsBridge(page)
  await page.goto('/')
  const toggle = await openSettings(page)
  await expect(toggle).toBeEnabled()
  await page.evaluate(() => { window.settingsTest.write = 'unchanged' })
  await toggle.click()
  await expect(page.getByText('Sign-in preference unchanged. Check desktop bootstrap logs.')).toBeVisible()
  await page.evaluate(() => { window.settingsTest.write = 'reject' })
  await toggle.click()
  await expect(page.getByText('Desktop sign-in preference unavailable. Try again.')).toBeVisible()
  await page.evaluate(() => { window.settingsTest.write = 'pending' })
  await toggle.click()
  await expect(toggle).toBeDisabled()
  await expect(toggle).toHaveAttribute('aria-checked', 'false')
  await expect(page.getByText('Desktop sign-in preference unavailable. Try again.')).toBeVisible()
  await expect(toggle).toBeEnabled()
  expect(await page.evaluate(() => window.settingsTest.calls)).toEqual([true, true, true])
  await page.evaluate(() => { const h = window.settingsTest; h.resolve?.({ ...h.value, launchAtLogin: true }) })
  await expect(toggle).toHaveAttribute('aria-checked', 'false')
})

for (const initial of ['pending', 'reject'] as const) {
  test(`desktop settings ${initial} initial read remains disabled and recovers on host notification`, async ({ page }) => {
    await settingsBridge(page, initial)
    await page.goto('/')
    const toggle = await openSettings(page)
    await expect(toggle).toBeDisabled()
    await expect(page.getByText('Desktop settings unavailable. Try reopening Settings.')).toBeVisible()
    await page.evaluate(() => {
      const h = window.settingsTest
      h.value = { ...h.value, launchAtLogin: true }
      h.changed.forEach(cb => cb(h.value))
      h.readResolve?.({ ...h.value, launchAtLogin: false })
    })
    await expect(toggle).toBeEnabled()
    await expect(toggle).toHaveAttribute('aria-checked', 'true')
  })
}

test('browser Settings has no desktop section', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Settings', exact: true }).click()
  await expect(page.getByRole('switch')).toHaveCount(0)
  await expect(page.getByText('Appearance', { exact: true })).toBeVisible()
})

const workspaces = ['Overview', 'Conversation', 'Memory', 'System', 'Agents', 'Development', 'Media', 'Settings']

test.beforeEach(async ({ page }) => {
  // No production connection is simulated. Capture actual JS failures separately
  // from expected offline network/transport diagnostics.
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
  Object.assign(page, { desktopErrors: errors })
})
test.afterEach(async ({ page }) => {
  expect((page as Page & { desktopErrors: string[] }).desktopErrors).toEqual([])
})

test('visible controls and keyboard palette request only confirmed native modes', async ({ page }) => {
  await bridge(page)
  await page.goto('/')
  const enter = page.getByRole('button', { name: 'Enter fullscreen', exact: true })
  await enter.focus()
  await page.keyboard.press('Enter')
  await expect(enter).toBeVisible() // no optimistic fullscreen
  await expect(page.getByText('Desktop mode requested; waiting for native confirmation.')).toBeVisible()
  await page.evaluate(() => window.desktopTest.emit('COMMAND_CENTER_FULLSCREEN'))
  const exit = page.getByRole('button', { name: 'Exit fullscreen', exact: true })
  await expect(exit).toBeVisible()
  await palette(page, 'Exit fullscreen')
  await expect(exit).toBeVisible()
  await page.evaluate(() => window.desktopTest.emit('COMMAND_CENTER_WINDOWED')) // native Escape/exit notification
  await expect(enter).toBeVisible()
  await palette(page, 'Enter fullscreen')
  await page.getByRole('button', { name: 'Return to compact overlay', exact: true }).click()
  await palette(page, 'Return to compact overlay')
  expect(await page.evaluate(() => window.desktopTest.requests)).toEqual([
    'COMMAND_CENTER_FULLSCREEN', 'COMMAND_CENTER_WINDOWED', 'COMMAND_CENTER_FULLSCREEN', 'OVERLAY_COMPACT', 'OVERLAY_COMPACT',
  ])
  await expect(page.getByRole('button', { name: 'Activate', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Sleep', exact: true })).toBeDisabled()
})

test('stale initial reads and removed listeners cannot override notifications; repeated requests stay bounded', async ({ page }) => {
  await bridge(page, 'pending')
  await page.goto('/')
  await page.evaluate(() => window.desktopTest.emit('COMMAND_CENTER_FULLSCREEN'))
  await page.evaluate(() => {
    window.desktopTest.resolve('COMMAND_CENTER_WINDOWED')
    window.desktopTest.retired.forEach((callback) => callback('COMMAND_CENTER_WINDOWED'))
    window.desktopTest.emit('INVALID_MODE')
  })
  const exit = page.getByRole('button', { name: 'Exit fullscreen', exact: true })
  await expect(exit).toBeVisible()
  for (let i = 0; i < 10; i++) await exit.click()
  await expect(exit).toBeVisible()
  expect(await page.evaluate(() => ({ listeners: window.desktopTest.listeners.size, removed: window.desktopTest.removed, subscribed: window.desktopTest.subscribed, requests: window.desktopTest.requests }))).toEqual({
    listeners: 1, removed: 1, subscribed: 2, requests: Array(10).fill('COMMAND_CENTER_WINDOWED'),
  })
})

test('unmount removes mode and settings-open subscriptions', async ({ page }) => {
  await settingsBridge(page)
  await page.goto('/')
  await page.evaluate(async () => {
    const reactUrl = '/node_modules/.vite/deps/react.js'
    const domUrl = '/node_modules/.vite/deps/react-dom_client.js'
    const appUrl = '/src/app/App.tsx'
    const { default: { createElement } } = await import(/* @vite-ignore */ reactUrl)
    const { default: { createRoot } } = await import(/* @vite-ignore */ domUrl)
    const { App } = await import(/* @vite-ignore */ appUrl)
    const container = document.createElement('div')
    document.body.append(container)
    const root = createRoot(container)
    root.render(createElement(App))
    await new Promise((resolve) => setTimeout(resolve, 100))
    if (window.desktopTest.listeners.size !== 2 || window.settingsTest.opens.size !== 2) throw new Error('extra mount not subscribed')
    root.unmount()
    container.remove()
    window.desktopTest.resolve('COMMAND_CENTER_FULLSCREEN')
  })
  expect(await page.evaluate(() => window.desktopTest.listeners.size)).toBe(1)
  expect(await page.evaluate(() => window.settingsTest.opens.size)).toBe(1)
})

test('rejected and thrown bridge operations degrade without fake success', async ({ page }) => {
  await bridge(page, 'reject')
  await page.goto('/')
  await expect(page.getByText('Desktop mode unavailable. Browser review remains usable.')).toBeVisible()
  await page.evaluate(() => { window.desktopTest.failRequest = true })
  await page.getByRole('button', { name: 'Enter fullscreen', exact: true }).click()
  await expect(page.getByText('Desktop mode request failed. Try again.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Exit fullscreen' })).toHaveCount(0)
})

test('initial timeout ignores late results but permits later native notification', async ({ page }) => {
  await bridge(page, 'pending')
  await page.goto('/')
  await expect(page.getByText('Desktop mode unavailable. Browser review remains usable.')).toBeVisible()
  await page.evaluate(() => window.desktopTest.resolve('COMMAND_CENTER_FULLSCREEN'))
  await expect(page.getByRole('button', { name: 'Enter fullscreen', exact: true })).toBeVisible()
  await page.evaluate(() => window.desktopTest.emit('COMMAND_CENTER_FULLSCREEN'))
  await expect(page.getByRole('button', { name: 'Exit fullscreen', exact: true })).toBeVisible()
})

test('browser review keeps navigation and truthfully degrades desktop commands', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Return to compact overlay', exact: true })).toHaveCount(0)
  await palette(page, 'Enter fullscreen')
  await expect(page.getByText('Desktop controls are unavailable in browser review.')).toBeVisible()
  await palette(page, 'Return to compact overlay')
  await palette(page, 'Go to Memory')
  await expect(page.getByRole('button', { name: 'Memory', exact: true })).toHaveAttribute('aria-current', 'page')
})

for (const minimum of [false, true]) {
  test(`all workspaces retain scroll access without document overflow (${minimum ? '960x600 narrow boundary' : 'project CSS viewport'})`, async ({ page }) => {
    if (minimum) await page.setViewportSize({ width: 960, height: 600 })
    await settingsBridge(page)
    await page.goto('/')
    for (const name of workspaces) {
      await page.getByRole('button', { name, exact: true }).click()
      await expect(page.getByRole('button', { name, exact: true })).toHaveAttribute('aria-current', 'page')
      await expect(page.getByRole('main')).toBeVisible()
      await expect(page.getByRole('button', { name: 'Return to compact overlay', exact: true })).toBeInViewport()
      await expect(page.getByRole('button', { name: 'Enter fullscreen', exact: true })).toBeInViewport()
      expect(await page.evaluate(() => {
        const main = document.querySelector('main')!
        const nav = document.querySelector('nav')!
        return {
          documentOverflow: document.documentElement.scrollWidth > innerWidth || document.documentElement.scrollHeight > innerHeight,
          mainOverflow: main.scrollWidth > main.clientWidth,
          mainBottom: main.getBoundingClientRect().bottom <= innerHeight + 1,
          scroll: getComputedStyle(main).overflowY,
          navScroll: getComputedStyle(nav).overflowY,
        }
      }), name).toEqual({ documentOverflow: false, mainOverflow: false, mainBottom: true, scroll: 'auto', navScroll: 'auto' })
      await page.getByRole('main').evaluate((element) => { element.scrollTop = element.scrollHeight })
      await expect(page.getByRole('main')).toBeVisible()
    }
  })
}
