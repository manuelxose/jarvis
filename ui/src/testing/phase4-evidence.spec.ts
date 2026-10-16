import { expect, test } from '@playwright/test'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'

// Fixture-route screenshots only: no backend, no production events or credentials.
const URL = '/visual-core-fixtures.html'
const OUT = resolve(import.meta.dirname, '../../../docs/engineering/phase-4-evidence')
const PRESENTATIONS: Record<string, string> = { overlay: 'compact-overlay', 'command-center': 'command-center' }
const STATES: Record<string, string> = {
  IDLE: 'Idle', LISTENING: 'Listening', THINKING: 'Thinking', SPEAKING: 'Speaking', EXECUTING: 'Executing',
  SUCCESS: 'Done', ERROR: 'Error', DISCONNECTED: 'Offline', STARTING: 'Starting', STOPPING: 'Stopping',
}
const STARTUP = ['startup-full', 'startup-reduced', 'startup-interrupted']

type Entry = { file: string; kind: string; presentation: string; state: string; motion: string; source: 'fixture'; assertion: string }
const entries: Entry[] = []

test.describe.configure({ mode: 'serial' })
test.beforeEach(({}, info) => test.skip(info.project.name !== 'desktop-1080p', 'evidence captured once on desktop-1080p'))

async function shot(page: import('@playwright/test').Page, e: Omit<Entry, 'source'>) {
  const path = resolve(OUT, e.file)
  mkdirSync(dirname(path), { recursive: true })
  await page.locator('[data-cell]').screenshot({ path, animations: 'disabled' })
  entries.push({ ...e, source: 'fixture' })
}

for (const [pname, variant] of Object.entries(PRESENTATIONS)) {
  for (const [state, label] of Object.entries(STATES)) {
    test(`evidence ${pname} ${state}`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion: 'no-preference' })
      await page.goto(`${URL}?state=${state.toLowerCase()}&variant=${variant}&motion=disabled`)
      const core = page.locator('[data-variant]')
      await expect(core).toHaveAttribute('data-state', state)
      await expect(core.locator('[role="status"]')).toContainText(label)
      await shot(page, { file: `${pname}-${state.toLowerCase()}.png`, kind: 'state', presentation: pname, state, motion: 'disabled', assertion: `data-state=${state}; status text contains "${label}"` })
    })
  }
  test(`evidence ${pname} reduced motion`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto(`${URL}?state=speaking&variant=${variant}&motion=full`)
    const core = page.locator('[data-variant]')
    await expect(core).toHaveAttribute('data-motion', 'reduced')
    await expect(core).toHaveAttribute('data-state', 'SPEAKING')
    await shot(page, { file: `${pname}-reduced-motion-speaking.png`, kind: 'reduced-motion', presentation: pname, state: 'SPEAKING', motion: 'reduced', assertion: 'data-motion=reduced under OS prefers-reduced-motion; state text retained' })
  })
}

for (const scenario of STARTUP) {
  test(`evidence ${scenario}`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await page.goto(`${URL}?scenario=${scenario}&variant=startup`)
    const core = page.locator('[data-variant]')
    const cancelled = scenario === 'startup-interrupted'
    await expect(core).toHaveAttribute('data-state', cancelled ? 'STOPPING' : 'STARTING')
    await expect(core).toHaveAttribute('data-startup-phase', cancelled ? 'cancelled' : 'announcing')
    if (!cancelled) await expect(core.locator('[role="status"]')).toContainText('Apps: 2 ready, 0 failed')
    await shot(page, { file: `${scenario}.png`, kind: 'startup', presentation: 'startup', state: cancelled ? 'STOPPING' : 'STARTING', motion: scenario === 'startup-reduced' ? 'reduced' : 'full', assertion: `data-startup-phase=${cancelled ? 'cancelled' : 'announcing'}` })
  })
}

test('rapid state transitions settle on the last state without a stuck label', async ({ page }) => {
  await page.goto(`${URL}?a11y=1&variant=command-center&motion=disabled`)
  await page.waitForFunction(() => !!(window as unknown as { __vc?: unknown }).__vc)
  await page.evaluate(() => {
    const vc = (window as unknown as { __vc: { set(p: Record<string, unknown>): void } }).__vc
    for (const s of ['LISTENING', 'THINKING', 'SPEAKING', 'EXECUTING', 'ERROR', 'DISCONNECTED', 'IDLE', 'LISTENING']) vc.set({ state: s })
  })
  const core = page.locator('[data-variant]')
  await expect(core).toHaveAttribute('data-state', 'LISTENING')
  await expect(core.locator('[role="status"]')).toContainText('Listening', { timeout: 2000 })
})

const MANIFEST = 'native-hardware.json'

// Native status is defined only by the tracked manifest; fail closed if it is missing or malformed.
function nativeSummary() {
  const manifest = JSON.parse(readFileSync(resolve(OUT, MANIFEST), 'utf8')) as {
    aggregate_status?: unknown
    artifacts?: { id: string; path: string }[]
    rows?: { id?: unknown; status?: unknown; evidence_scope?: unknown; artifacts?: unknown }[]
  }
  const paths = new Map((manifest.artifacts ?? []).map((a) => [a.id, a.path]))
  expect(typeof manifest.aggregate_status, `${MANIFEST} aggregate_status`).toBe('string')
  expect(Array.isArray(manifest.rows) && manifest.rows.length > 0, `${MANIFEST} rows`).toBe(true)
  const rows = manifest.rows!.map((r) => {
    expect(typeof r.id, `${MANIFEST} row id`).toBe('string')
    expect(typeof r.status, `${MANIFEST} row ${String(r.id)} status`).toBe('string')
    expect(typeof r.evidence_scope, `${MANIFEST} row ${String(r.id)} evidence_scope`).toBe('string')
    expect(Array.isArray(r.artifacts), `${MANIFEST} row ${String(r.id)} artifacts`).toBe(true)
    const artifacts = (r.artifacts as string[]).map((aid) => {
      expect(paths.has(aid), `${MANIFEST} row ${String(r.id)} references unknown artifact ${aid}`).toBe(true)
      return paths.get(aid)!
    })
    return { id: r.id, status: r.status, evidence_scope: r.evidence_scope, artifacts }
  })
  return {
    manifest: MANIFEST,
    aggregate_status: manifest.aggregate_status,
    note: 'Fixture entries below are not native evidence; native status is defined only by the manifest.',
    rows,
  }
}

test('write evidence index', async () => {
  expect(entries.length).toBe(2 * (Object.keys(STATES).length + 1) + STARTUP.length)
  const native_hardware = nativeSummary()
  const index = {
    generated: 'phase4-evidence.spec.ts',
    source: 'fixture routes (/visual-core-fixtures.html); not native Windows, no backend',
    entries: entries.sort((a, b) => a.file.localeCompare(b.file)),
    native_hardware,
  }
  writeFileSync(resolve(OUT, 'index.json'), JSON.stringify(index, null, 2) + '\n')

  const onDisk = JSON.parse(readFileSync(resolve(OUT, 'index.json'), 'utf8'))
  expect(onDisk.native_hardware, 'index.json native_hardware retained').toEqual(nativeSummary())
  expect(onDisk.entries.length).toBe(entries.length)
})
