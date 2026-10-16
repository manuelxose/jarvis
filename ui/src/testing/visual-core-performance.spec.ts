import { expect, test, type CDPSession, type Page, type TestInfo } from '@playwright/test'

/*
 * Fixture-only measurements in headless Chromium against the real visual-core service with
 * synthetic frames. These are NOT native Windows CPU/RAM proof. Frame intervals are compared
 * to the 16.7 ms reference and attached, never hard-gated on a shared host; the deterministic
 * invariants (commits, RAF, timers, listeners, DOM size) are asserted.
 */
const URL = '/visual-core-fixtures.html'
const VARIANTS = ['compact-overlay', 'command-center'] as const
const MOTIONS = ['full', 'disabled'] as const
const STATES = [
  { scenario: 'idle', state: 'IDLE' },
  { scenario: 'listening', state: 'LISTENING' },
  { scenario: 'speaking', state: 'SPEAKING' },
  { scenario: 'startup', state: 'STARTING' },
] as const
const WARMUP_MS = 1000
const SAMPLE_MS = 10_000
const SUSTAINED_MS = 60_000
const FRAME_REFERENCE_MS = 16.7

type Scenario = (typeof STATES)[number]['scenario']
interface Stats {
  commits: number; renderMs: number; subscribers: number; pendingTimers: number; driverTimers: number
  envelopes: number; accepted: number; rejected: number; signalReads: number; disposed: boolean | null; mounted: boolean
}
interface Ctl {
  start(s: Scenario): void; stop(): void; disposeOnly(): void; burst(n: number): number; stale(): number; repeatIdentical(n: number): number
  cancelTurn(): void; cancelStartup(): void; disconnect(): void; stats(): Stats
  cycles(n: number, checkpoints: number[]): Promise<Record<string, number>[]>
}
interface Probes {
  rafOutstanding: number; rafCallbacks: number; attrWrites: number; timers: number; listeners: number
}
declare global {
  interface Window {
    __perf: Ctl
    __probe: () => Probes
    __origRaf: typeof requestAnimationFrame
    __sample: (ms: number) => Promise<number[]>
  }
}

async function open(page: Page, variant: string, motion: string) {
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await page.addInitScript(() => {
    const raf = window.requestAnimationFrame.bind(window)
    window.__origRaf = raf
    const outstanding = new Set<number>()
    let callbacks = 0
    window.requestAnimationFrame = cb => {
      const id = raf(t => { outstanding.delete(id); callbacks++; cb(t) })
      outstanding.add(id)
      return id
    }
    const cancel = window.cancelAnimationFrame.bind(window)
    window.cancelAnimationFrame = id => { outstanding.delete(id); cancel(id) }

    let attrWrites = 0
    const setAttribute = Element.prototype.setAttribute
    Element.prototype.setAttribute = function (this: Element, ...args: [string, string]) {
      if (this instanceof SVGElement && (args[0] === 'transform' || args[0] === 'opacity')) attrWrites++
      return setAttribute.apply(this, args)
    }

    const timers = new Set<number>()
    const st = window.setTimeout.bind(window)
    const si = window.setInterval.bind(window)
    window.setTimeout = ((fn: TimerHandler, ms?: number, ...a: unknown[]) => {
      const id: number = st((...x: unknown[]) => { timers.delete(id); (fn as (...y: unknown[]) => void)(...x) }, ms, ...a)
      timers.add(id)
      return id
    }) as typeof setTimeout
    window.setInterval = ((fn: TimerHandler, ms?: number, ...a: unknown[]) => {
      const id: number = si(fn, ms, ...a)
      timers.add(id)
      return id
    }) as typeof setInterval
    const ct = window.clearTimeout.bind(window)
    const ci = window.clearInterval.bind(window)
    window.clearTimeout = id => { timers.delete(id as number); ct(id) }
    window.clearInterval = id => { timers.delete(id as number); ci(id) }

    const live: { t: EventTarget; type: string; fn: unknown }[] = []
    const add = EventTarget.prototype.addEventListener
    const remove = EventTarget.prototype.removeEventListener
    const watched = (type: string) => type === 'visibilitychange' || type === 'change'
    EventTarget.prototype.addEventListener = function (this: EventTarget, type: string, fn: never, opts?: never) {
      if (watched(type) && !live.some(l => l.t === this && l.type === type && l.fn === fn)) live.push({ t: this, type, fn })
      return add.call(this, type, fn, opts)
    }
    EventTarget.prototype.removeEventListener = function (this: EventTarget, type: string, fn: never, opts?: never) {
      const i = live.findIndex(l => l.t === this && l.type === type && l.fn === fn)
      if (i >= 0) live.splice(i, 1)
      return remove.call(this, type, fn, opts)
    }

    window.__probe = () => ({ rafOutstanding: outstanding.size, rafCallbacks: callbacks, attrWrites, timers: timers.size, listeners: live.length })
    window.__sample = ms => new Promise(resolve => {
      const out: number[] = []
      let last = 0
      const end = performance.now() + ms
      const tick = (t: number) => {
        if (last) out.push(t - last)
        last = t
        if (performance.now() < end) raf2(tick)
        else resolve(out)
      }
      const raf2 = window.__origRaf
      raf2(tick)
    })
  })
  await page.goto(`${URL}?perf=1&variant=${variant}&motion=${motion}`)
  await page.waitForFunction(() => !!window.__perf)
}

const core = (page: Page) => page.locator('[data-variant]')
const probe = (page: Page) => page.evaluate(() => window.__probe())
const stats = (page: Page) => page.evaluate(() => window.__perf.stats())

async function cdp(page: Page): Promise<CDPSession | null> {
  try { return await page.context().newCDPSession(page) } catch { return null }
}
async function metrics(session: CDPSession | null) {
  if (!session) return null
  try {
    await session.send('Performance.enable')
    const { metrics: m } = await session.send('Performance.getMetrics')
    return Object.fromEntries(m.map(x => [x.name, x.value])) as Record<string, number>
  } catch { return null }
}
async function collectGarbage(session: CDPSession | null) {
  try { await session?.send('HeapProfiler.collectGarbage') } catch { /* heap trend is reported as unmeasured when GC is unavailable */ }
}
const delta = (a: Record<string, number> | null, b: Record<string, number> | null, k: string) => (a && b && k in a && k in b ? b[k]! - a[k]! : 'unavailable')
const level = (a: Record<string, number> | null, k: string) => (a && k in a ? a[k]! : 'unavailable')
const percentile = (xs: number[], p: number) => {
  if (!xs.length) return 'unavailable'
  const s = [...xs].sort((a, b) => a - b)
  return Math.round(s[Math.min(s.length - 1, Math.ceil((p / 100) * s.length) - 1)]! * 100) / 100
}

async function attach(info: TestInfo, name: string, body: unknown) {
  await info.attach(name, { body: JSON.stringify(body, null, 2), contentType: 'application/json' })
}

test.beforeEach(({}, info) => {
  // Frame cost does not depend on viewport; one project keeps the bounded suite at minutes, not tens of minutes.
  test.skip(info.project.name !== 'desktop-1080p', 'measured once on desktop-1080p')
})

for (const variant of VARIANTS) {
  for (const motion of MOTIONS) {
    for (const { scenario, state } of STATES) {
      test(`${variant} ${motion} ${scenario}: ${SAMPLE_MS / 1000}s measured sample`, async ({ page, browserName }, info) => {
        test.setTimeout(SAMPLE_MS + 60_000)
        await open(page, variant, motion)
        const session = await cdp(page)
        await page.evaluate(s => window.__perf.start(s), scenario)
        await expect(core(page)).toHaveAttribute('data-state', state)
        await expect(core(page)).toHaveAttribute('data-motion', motion)
        await page.waitForTimeout(WARMUP_MS)

        await collectGarbage(session)
        const before = await metrics(session)
        const s0 = await stats(page)
        const p0 = await probe(page)
        const t0 = Date.now()
        const intervals = await page.evaluate(ms => window.__sample(ms), SAMPLE_MS)
        const elapsedMs = Date.now() - t0
        const p1 = await probe(page)
        const s1 = await stats(page)
        await collectGarbage(session)
        const after = await metrics(session)

        const rendererRaf = p1.rafCallbacks - p0.rafCallbacks
        const svgWrites = p1.attrWrites - p0.attrWrites
        const commits = s1.commits - s0.commits
        const envelopes = s1.envelopes - s0.envelopes
        const result = {
          scope: 'fixture synthetic frames in headless Chromium; not native CPU/RAM proof',
          variant, motion, scenario, browserName, project: info.project.name, viewport: page.viewportSize(),
          warmupMs: WARMUP_MS, plannedMs: SAMPLE_MS, elapsedMs,
          frame: { samples: intervals.length, p50Ms: percentile(intervals, 50), p95Ms: percentile(intervals, 95), referenceMs: FRAME_REFERENCE_MS, overReference: intervals.filter(x => x > FRAME_REFERENCE_MS * 1.5).length },
          rendererRafCallbacks: rendererRaf, rendererFrameWrites: svgWrites,
          semanticCommits: commits, reactRenderMs: Math.round((s1.renderMs - s0.renderMs) * 100) / 100,
          envelopes, signalReads: s1.signalReads - s0.signalReads,
          cdp: {
            scriptDurationSec: delta(before, after, 'ScriptDuration'), taskDurationSec: delta(before, after, 'TaskDuration'),
            layoutCount: delta(before, after, 'LayoutCount'), recalcStyleCount: delta(before, after, 'RecalcStyleCount'),
            heapUsedBytes: { start: level(before, 'JSHeapUsedSize'), end: level(after, 'JSHeapUsedSize') },
            domNodes: { start: level(before, 'Nodes'), end: level(after, 'Nodes') },
          },
          pending: { rendererRaf: p1.rafOutstanding, timers: p1.timers, listeners: p1.listeners, serviceTimers: s1.pendingTimers, subscribers: s1.subscribers, driverTimers: s1.driverTimers },
        }
        await attach(info, `perf-${variant}-${motion}-${scenario}.json`, result)

        // Deterministic invariants.
        expect(intervals.length).toBeGreaterThan(100)
        expect(elapsedMs).toBeGreaterThanOrEqual(SAMPLE_MS - 200)
        expect(s1.subscribers).toBe(1)
        expect(s1.pendingTimers).toBeLessThanOrEqual(1)
        expect(commits, 'semantic commits must not scale with envelopes').toBeLessThanOrEqual(scenario === 'startup' ? 12 : 0)
        if (scenario === 'speaking' || scenario === 'listening') expect(envelopes, 'fixture must deliver a substantive share of the nominal 300 (30 Hz x 10 s); timers drift under load').toBeGreaterThan(100)
        if (motion === 'disabled') {
          expect(rendererRaf).toBe(0)
          expect(svgWrites).toBe(0)
          expect(p1.rafOutstanding).toBe(0)
        } else {
          expect(rendererRaf, 'full mode must actually animate').toBeGreaterThan(100)
          // Ambient breath is paced at 15 fps; unpaced rAF would be ~60/s here and 200+/s in the GPU-less shell.
          if (scenario === 'idle') expect(rendererRaf, 'idle must not run at display rate').toBeLessThan(200)
          expect(svgWrites).toBeGreaterThan(100)
          expect(p1.rafOutstanding).toBeLessThanOrEqual(1) // between paced frames the pending handle is the timer
        }
        await expect(core(page)).toHaveAttribute('data-state', scenario === 'startup' ? 'STARTING' : state)
        if (scenario === 'startup') {
          await expect(core(page)).toHaveAttribute('data-startup-phase', 'announcing')
          await expect(core(page)).toContainText('Apps: 2 ready, 1 failed')
        }
      })
    }
  }
}

test.describe('lifecycle and negative paths', () => {
  for (const variant of VARIANTS) {
    test(`${variant}: 30 Hz envelopes plus a 10x burst do not fan out into semantic renders or timers`, async ({ page }) => {
      await open(page, variant, 'full')
      await page.evaluate(() => window.__perf.start('speaking'))
      await expect(core(page)).toHaveAttribute('data-state', 'SPEAKING')
      await page.waitForTimeout(500)
      const s0 = await stats(page)
      const p0 = await probe(page)
      const accepted = await page.evaluate(() => window.__perf.burst(300))
      const identical = await page.evaluate(() => window.__perf.repeatIdentical(300))
      await page.waitForTimeout(500)
      const s1 = await stats(page)
      const p1 = await probe(page)
      expect(accepted).toBe(300)
      expect(identical).toBe(300)
      expect(s1.commits - s0.commits).toBe(0)
      expect(s1.pendingTimers).toBeLessThanOrEqual(1)
      expect(p1.timers - p0.timers).toBeLessThanOrEqual(1)
      expect(p1.listeners).toBe(p0.listeners)
      expect(p1.rafOutstanding).toBeLessThanOrEqual(1) // between paced frames the pending handle is the timer
    })

    test(`${variant}: stale and out-of-order frames during speech are rejected and invisible`, async ({ page }) => {
      await open(page, variant, 'disabled')
      await page.evaluate(() => window.__perf.start('speaking'))
      await expect(core(page)).toHaveAttribute('data-state', 'SPEAKING')
      const s0 = await stats(page)
      const accepted = await page.evaluate(() => window.__perf.stale())
      const s1 = await stats(page)
      expect(accepted).toBe(0)
      expect(s1.rejected).toBeGreaterThan(s0.rejected)
      await expect(core(page)).toHaveAttribute('data-state', 'SPEAKING')
      expect(s1.commits - s0.commits).toBeLessThanOrEqual(1)
    })

    for (const [name, act, expected] of [
      ['cancel during speech', 'cancelTurn', 'IDLE'],
      ['disconnect during speech', 'disconnect', 'DISCONNECTED'],
    ] as const) {
      test(`${variant}: ${name} leaves textual state, then disposal is safe`, async ({ page }) => {
        const errors: string[] = []
        page.on('pageerror', e => errors.push(e.message))
        await open(page, variant, 'full')
        await page.evaluate(() => window.__perf.start('speaking'))
        await expect(core(page)).toHaveAttribute('data-state', 'SPEAKING')
        await page.evaluate(a => window.__perf[a](), act)
        await expect(core(page)).toHaveAttribute('data-state', expected)
        // The polite live region coalesces announcements for 500 ms: capture the text only once it has settled,
        // otherwise a slow host flushes it between the two reads below.
        let text = await core(page).innerText()
        await expect(async () => {
          await page.waitForTimeout(700) // longer than the 500 ms coalescing window
          const next = await core(page).innerText()
          const stable = next === text
          text = next
          expect(stable).toBe(true)
        }).toPass({ timeout: 10_000 })
        await page.evaluate(() => window.__perf.disposeOnly())
        await page.waitForTimeout(300)
        expect(await core(page).innerText()).toBe(text)
        const s = await stats(page)
        expect(s.disposed).toBe(true)
        expect(s.pendingTimers).toBe(0)
        expect(s.driverTimers).toBe(0)
        expect(errors).toEqual([])
      })
    }

    test(`${variant}: cancelled and disconnected startup keep text, no pending timers`, async ({ page }) => {
      await open(page, variant, 'full')
      await page.evaluate(() => window.__perf.start('startup'))
      await expect(core(page)).toHaveAttribute('data-state', 'STARTING')
      await page.evaluate(() => window.__perf.cancelStartup())
      await expect(core(page)).toHaveAttribute('data-state', 'STOPPING')
      await page.evaluate(() => window.__perf.disconnect())
      await expect(core(page)).toHaveAttribute('data-state', 'DISCONNECTED')
      await expect(core(page)).toContainText('Offline')
      await page.evaluate(() => window.__perf.stop())
      await page.waitForTimeout(700)
      const s = await stats(page)
      const p = await probe(page)
      expect(s.mounted).toBe(false)
      expect(s.pendingTimers).toBe(0)
      expect(s.subscribers).toBe(0)
      expect(p.rafOutstanding).toBe(0)
    })

    for (const motion of MOTIONS) test(`${variant} ${motion}: unmount leaves no DOM writes, RAF, timers or listeners`, async ({ page }) => {
      {
        await open(page, variant, motion)
        const base = await probe(page)
        await page.evaluate(() => window.__perf.start('speaking'))
        await expect(core(page)).toHaveAttribute('data-state', 'SPEAKING')
        await page.waitForTimeout(700)
        await page.evaluate(() => {
          window.__perf.stop()
          const w = window as unknown as { __mut: number }
          w.__mut = 0
          new MutationObserver(m => { w.__mut += m.length }).observe(document.body, { childList: true, characterData: true, attributes: true, subtree: true })
        })
        const p0 = await probe(page)
        await page.waitForTimeout(800)
        const p1 = await probe(page)
        const s = await stats(page)
        expect(p1.attrWrites - p0.attrWrites, 'no SVG writes after unmount').toBe(0)
        expect(await page.evaluate(() => (window as unknown as { __mut: number }).__mut), 'no DOM mutations after unmount').toBe(0)
        expect(p1.rafOutstanding).toBe(0)
        expect(p1.rafCallbacks).toBe(p0.rafCallbacks)
        expect(p1.listeners).toBe(base.listeners)
        expect(p1.timers).toBeLessThanOrEqual(base.timers)
        expect(s.subscribers).toBe(0)
        expect(s.pendingTimers).toBe(0)
        expect(s.driverTimers).toBe(0)
      }
    })
  }
})

for (const variant of VARIANTS) {
  test(`${variant}: 100 repeated lifecycle cycles keep DOM size, timers and listeners stable`, async ({ page }, info) => {
    test.skip(info.project.name !== 'desktop-1080p', 'measured once on desktop-1080p')
    test.setTimeout(180_000)
    await open(page, variant, 'full')
    const session = await cdp(page)
    await page.evaluate(() => window.__perf.cycles(3, []))
    await page.waitForTimeout(700)
    const base = await probe(page)
    await collectGarbage(session)
    const heap0 = await metrics(session)
    const t0 = Date.now()
    const checkpoints = await page.evaluate(() => window.__perf.cycles(100, [10, 50, 100]))
    const elapsedMs = Date.now() - t0
    await page.waitForTimeout(700)
    const end = await probe(page)
    await collectGarbage(session)
    const heap1 = await metrics(session)
    const s = await stats(page)
    await attach(info, `cycles-${variant}.json`, {
      scope: 'fixture lifecycle cycles; cancel/disconnect/stale alternate per cycle',
      variant, cycles: 100, elapsedMs, checkpoints, baseline: base, end,
      heapUsedBytes: { start: level(heap0, 'JSHeapUsedSize'), end: level(heap1, 'JSHeapUsedSize') },
      domNodesCdp: { start: level(heap0, 'Nodes'), end: level(heap1, 'Nodes') },
      note: 'Heap is reported as a trend only; leak assertions use deterministic counters.',
    })
    expect(checkpoints.map(c => c.cycle)).toEqual([10, 50, 100])
    const dom = checkpoints.map(c => c.domNodes)
    expect(dom[2]).toBe(dom[0])
    expect(dom[1]).toBe(dom[0])
    for (const c of checkpoints) { expect(c.subscribers).toBe(0); expect(c.driverTimers).toBe(0); expect(c.pendingTimers).toBe(0) }
    expect(end.rafOutstanding).toBe(0)
    expect(end.listeners).toBe(base.listeners)
    expect(end.timers).toBeLessThanOrEqual(base.timers)
    expect(s.mounted).toBe(false)
  })
}

test(`sustained ${SUSTAINED_MS / 1000}s telemetry run with remount and dispose`, async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop-1080p', 'measured once on desktop-1080p')
  test.setTimeout(SUSTAINED_MS + 120_000)
  await open(page, 'command-center', 'full')
  const session = await cdp(page)
  await page.evaluate(() => window.__perf.start('speaking'))
  await expect(core(page)).toHaveAttribute('data-state', 'SPEAKING')
  await page.waitForTimeout(WARMUP_MS)
  await collectGarbage(session)
  const m0 = await metrics(session)
  const s0 = await stats(page)
  const p0 = await probe(page)
  const slices: Record<string, unknown>[] = []
  const intervals: number[] = []
  const t0 = Date.now()
  const SLICE_MS = 10_000
  for (let i = 0; i < SUSTAINED_MS / SLICE_MS; i++) {
    if (i === 3) {
      // Remount mid-run: old service disposed, new one started; counters reset in the fixture.
      await page.evaluate(() => { window.__perf.stop(); window.__perf.start('speaking') })
      await expect(core(page)).toHaveAttribute('data-state', 'SPEAKING')
      await page.waitForTimeout(WARMUP_MS)
    }
    const before = await stats(page)
    const part = await page.evaluate(ms => window.__sample(ms), SLICE_MS)
    intervals.push(...part)
    const after = await stats(page)
    await collectGarbage(session)
    const m = await metrics(session)
    slices.push({
      slice: i, samples: part.length, p50Ms: percentile(part, 50), p95Ms: percentile(part, 95),
      commits: after.commits - before.commits, envelopes: after.envelopes - before.envelopes,
      heapUsedBytes: level(m, 'JSHeapUsedSize'), nodes: level(m, 'Nodes'),
    })
  }
  const elapsedMs = Date.now() - t0
  const p1 = await probe(page)
  const m1 = await metrics(session)
  await page.evaluate(() => window.__perf.stop())
  await page.waitForTimeout(700)
  const pEnd = await probe(page)
  const sEnd = await stats(page)
  await attach(info, 'sustained-command-center.json', {
    scope: 'fixture synthetic 30 Hz telemetry in headless Chromium; not native CPU/RAM proof',
    plannedMs: SUSTAINED_MS, elapsedMs, remountAtSlice: 3, warmupMs: WARMUP_MS,
    frame: { samples: intervals.length, p50Ms: percentile(intervals, 50), p95Ms: percentile(intervals, 95), referenceMs: FRAME_REFERENCE_MS },
    slices,
    cdp: {
      scriptDurationSec: delta(m0, m1, 'ScriptDuration'), taskDurationSec: delta(m0, m1, 'TaskDuration'),
      heapUsedBytes: { start: level(m0, 'JSHeapUsedSize'), end: level(m1, 'JSHeapUsedSize') },
    },
    baselineProbe: p0, endProbe: p1, afterDisposeProbe: pEnd, afterDisposeStats: sEnd, startStatsEnvelopes: s0.envelopes,
  })
  expect(elapsedMs).toBeGreaterThanOrEqual(SUSTAINED_MS - 500)
  expect(slices.every(x => x.commits === 0 || x.slice === 3)).toBe(true)
  expect(p1.timers - p0.timers).toBeLessThanOrEqual(1)
  expect(p1.listeners).toBe(p0.listeners)
  expect(p1.rafOutstanding).toBeLessThanOrEqual(1) // between paced frames the pending handle is the timer
  expect(pEnd.rafOutstanding).toBe(0)
  expect(sEnd.subscribers).toBe(0)
  expect(sEnd.pendingTimers).toBe(0)
  expect(sEnd.driverTimers).toBe(0)
})
