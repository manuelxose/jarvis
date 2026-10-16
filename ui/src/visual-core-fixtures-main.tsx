import { Profiler, StrictMode, useEffect, useMemo, useState } from 'react'
import { flushSync } from 'react-dom'
import { createRoot } from 'react-dom/client'
import './index.css'
import { VisualCore } from './components/jarvis/visual-core/VisualCore.tsx'
import { VARIANT_SPECS, type VariantId } from './components/jarvis/visual-core/visualCoreGeometry.ts'
import { emptyFacts, emptyFences, VISUAL_STATES, type Facts, type VisualState } from './components/jarvis/visual-core/visualCoreMachine.ts'
import { fakeSource, startupSource, STARTUP_SCENARIOS, type StartupScenario } from './components/jarvis/visual-core/fixtureSource.ts'
import { createVisualCoreService, type VisualCoreSemantic, type VisualCoreService, type VisualCoreSource } from './services/visual-core-service.ts'
import { STATE_SPECS } from './components/jarvis/visual-core/visualCoreGeometry.ts'
import type { MotionMode } from './components/jarvis/visual-core/visualCoreGeometry.ts'

const VARIANTS = Object.keys(VARIANT_SPECS) as VariantId[]
const MOTIONS: MotionMode[] = ['full', 'reduced', 'disabled']

function Cell({ state, variant, motion, noSource, scenario }: { state: VisualState; variant: VariantId; motion: MotionMode; noSource?: boolean; scenario?: StartupScenario }) {
  const source = useMemo(() => (noSource ? undefined : scenario ? startupSource(scenario) : fakeSource(state)), [state, noSource, scenario])
  return (
    <div data-cell="" data-cell-state={state} data-motion={scenario === 'startup-fast' ? 'fast' : undefined} style={{ padding: 12 }}>
      <VisualCore source={source} variant={variant} motion={motion} />
    </div>
  )
}

function Row({ name, variant, motion }: { name: string; variant: VariantId; motion: MotionMode }) {
  return (
    <section data-row={name} style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-start', gap: 8, padding: 8 }}>
      {VISUAL_STATES.map(state => (
        <Cell key={state} state={state} variant={variant} motion={motion} />
      ))}
    </section>
  )
}

interface A11yControls {
  set(patch: Partial<VisualCoreSemantic> & { state?: VisualState }): void
  replaceSource(): void
  setMotion(m: MotionMode): void
  setMounted(on: boolean): void
  subscribers(): number
}

/** Fixture-only mutable source and controls for the accessibility spec; never imported by production entrypoints. */
function A11yFixture({ variant, motion: initial }: { variant: VariantId; motion: MotionMode }) {
  const [motion, setMotion] = useState(initial)
  const [mounted, setMounted] = useState(true)
  const [source, setSource] = useState<VisualCoreSource>()
  const ctl = useMemo(() => {
    let snap: VisualCoreSemantic = { ...fakeSource('IDLE').getSnapshot() }
    const listeners = new Set<() => void>()
    const make = (): VisualCoreSource => ({
      getSnapshot: () => snap,
      subscribe: l => { listeners.add(l); return () => { listeners.delete(l) } },
      readSignals: () => ({ output: 0.5, peak: 0.5, microphone: 0 }),
    })
    return {
      make,
      set(patch: Partial<VisualCoreSemantic>) {
        const state = patch.state ?? snap.state
        snap = { ...snap, label: STATE_SPECS[state].label, ...patch, connection: state === 'DISCONNECTED' ? 'disconnected' : 'connected' }
        listeners.forEach(l => l())
      },
      count: () => listeners.size,
    }
  }, [])
  useEffect(() => {
    setSource(ctl.make())
    const w = window as unknown as { __vc: A11yControls }
    w.__vc = {
      set: ctl.set,
      replaceSource: () => setSource(ctl.make()),
      setMotion,
      setMounted,
      subscribers: ctl.count,
    }
  }, [ctl])
  return (
    <main data-fixture="single" style={{ padding: 16, width: 'fit-content' }}>
      <h1 style={{ fontSize: 14 }}>Visual core fixture</h1>
      {mounted ? <VisualCore source={source} variant={variant} motion={motion} /> : <p data-unmounted="">unmounted</p>}
    </main>
  )
}

type PerfScenario = 'idle' | 'listening' | 'speaking' | 'startup'
interface PerfStats {
  commits: number; renderMs: number; subscribers: number; pendingTimers: number; driverTimers: number
  envelopes: number; accepted: number; rejected: number; signalReads: number; disposed: boolean | null; mounted: boolean
}
interface PerfControls {
  start(scenario: PerfScenario): void
  /** Unmount, then dispose the service. */
  stop(): void
  /** Dispose the service while the renderer stays mounted. */
  disposeOnly(): void
  burst(n: number): number
  stale(): number
  repeatIdentical(n: number): number
  cancelTurn(): void
  cancelStartup(): void
  disconnect(): void
  stats(): PerfStats
  cycles(n: number, checkpoints: number[]): Promise<Record<string, number>[]>
}

const HEARTBEAT_MS = 500
const ENVELOPE_MS = 33

/** Fixture-only dynamic source driven by the real service with synthetic, fixture-local frames; never imported by production entrypoints. */
function PerfFixture({ variant, motion }: { variant: VariantId; motion: MotionMode }) {
  const [session, setSession] = useState<{ source: VisualCoreSource } | null>(null)
  const ctl = useMemo(() => {
    let svc: VisualCoreService | null = null
    let seq = 0
    let envelopes = 0
    let accepted = 0
    let signalReads = 0
    let subscribers = 0
    let commits = 0
    let renderMs = 0
    const drivers = new Set<ReturnType<typeof setInterval> | ReturnType<typeof setTimeout>>()
    const every = (fn: () => void, ms: number) => { const h = setInterval(fn, ms); drivers.add(h) }
    const after = (fn: () => void, ms: number) => { const h = setTimeout(() => { drivers.delete(h); fn() }, ms); drivers.add(h) }
    const clearDrivers = () => { for (const h of drivers) { clearInterval(h as ReturnType<typeof setInterval>); clearTimeout(h as ReturnType<typeof setTimeout>) } drivers.clear() }
    const ev = (payload: Record<string, unknown>) => ({ session: 'perf', generation: 1, seq: ++seq, ageMs: 0, ...payload })
    const send = (input: unknown) => { envelopes++; const ok = svc?.ingest(input) ?? false; if (ok) accepted++; return ok }
    const snap = (facts: Facts, extra: Record<string, unknown> = {}) => ({
      kind: 'snapshot', session: 'perf', replacesSession: null, generation: 1, watermark: seq === 0 && envelopes === 0 ? 0 : ++seq, ageMs: 0,
      facts, fences: emptyFences(), output: null, microphone: null, candidate: null, successRemainingMs: 0, errorRemainingMs: 0, ...extra,
    })
    const level = (i: number) => Math.round((0.5 + 0.4 * Math.sin(i / 5)) * 1000) / 1000
    const speakingFacts: Facts = { ...emptyFacts(), turn: 't1', thinking: true, playback: { id: 'p1', turn: 't1', confirmed: true } }
    const apps = (...s: ('pending' | 'ready' | 'failed')[]) => s.map((status, i) => ({ id: `app${i}`, status }))
    const speak = () => send(ev({ kind: 'output', turn: 't1', playback: 'p1', level: level(envelopes), peak: 0.9 }))
    const heartbeat = () => send(ev({ kind: 'heartbeat' }))

    const start = (scenario: PerfScenario) => {
      const created = createVisualCoreService()
      svc = created
      seq = 0; envelopes = 0; accepted = 0; signalReads = 0; commits = 0; renderMs = 0
      const source: VisualCoreSource = {
        getSnapshot: created.getSnapshot,
        subscribe: l => { subscribers++; const off = created.subscribe(l); return () => { subscribers--; off() } },
        readSignals: now => { signalReads++; return created.readSignals(now) },
      }
      created.setConnection('connected')
      if (scenario === 'idle') { send(snap(emptyFacts())); every(heartbeat, HEARTBEAT_MS) }
      if (scenario === 'listening') {
        send(snap({ ...emptyFacts(), listening: true }))
        every(() => send(ev({ kind: 'microphone', level: level(envelopes), peak: 0.8 })), ENVELOPE_MS)
      }
      if (scenario === 'speaking') {
        send(snap(speakingFacts, { output: { level: 0.5, peak: 0.6, ageMs: 0 } }))
        every(speak, ENVELOPE_MS)
      }
      if (scenario === 'startup') {
        const step = (phase: Facts['startup'], a: ReturnType<typeof apps>) => send(snap({ ...emptyFacts(), startup: phase, apps: a }))
        step('acknowledged', apps('pending', 'pending', 'pending'))
        every(heartbeat, HEARTBEAT_MS)
        after(() => step('initializing', apps('pending', 'pending', 'pending')), 1500)
        after(() => step('initializing', apps('ready', 'pending', 'pending')), 3000)
        after(() => step('initializing', apps('ready', 'ready', 'pending')), 4500)
        after(() => step('initializing', apps('ready', 'ready', 'failed')), 6000)
        after(() => step('announcing', apps('ready', 'ready', 'failed')), 7500)
      }
      flushSync(() => setSession({ source }))
    }
    const burstOutput = (n: number, fixedLevel?: number) => {
      let ok = 0
      for (let i = 0; i < n; i++) if (send(ev({ kind: 'output', turn: 't1', playback: 'p1', level: fixedLevel ?? level(i), peak: 0.9 }))) ok++
      return ok
    }
    const domNodes = () => document.getElementsByTagName('*').length

    const controls: PerfControls = {
      start,
      stop() {
        clearDrivers()
        flushSync(() => setSession(null))
        svc?.dispose()
      },
      disposeOnly() { clearDrivers(); svc?.dispose() },
      burst: n => burstOutput(n),
      repeatIdentical: n => burstOutput(n, 0.5),
      stale() {
        const before = accepted
        send({ session: 'old', generation: 9, seq: 99, ageMs: 0, kind: 'output', turn: 't1', playback: 'p1', level: 1, peak: 1 })
        send({ session: 'perf', generation: 0, seq: seq + 1, ageMs: 0, kind: 'output', turn: 't1', playback: 'p1', level: 1, peak: 1 })
        send({ session: 'perf', generation: 1, seq: 1, ageMs: 0, kind: 'output', turn: 't1', playback: 'p1', level: 1, peak: 1 })
        return accepted - before
      },
      cancelTurn() { send(ev({ kind: 'cancel-turn', turn: 't1' })) },
      cancelStartup() { send(ev({ kind: 'startup', phase: 'cancelled', partial: false })) },
      disconnect() { svc?.setConnection('disconnected') },
      stats: () => ({
        commits, renderMs, subscribers, pendingTimers: svc?.getDiagnostics().pendingTimers ?? 0, driverTimers: drivers.size,
        envelopes, accepted, rejected: svc?.getDiagnostics().rejected ?? 0, signalReads, disposed: svc?.getDiagnostics().disposed ?? null,
        mounted: document.querySelector('[data-variant]') !== null,
      }),
      async cycles(n, checkpoints) {
        const frame = () => new Promise<void>(r => requestAnimationFrame(() => r()))
        const out: Record<string, number>[] = []
        for (let i = 1; i <= n; i++) {
          start('speaking')
          burstOutput(30)
          await frame()
          const k = i % 3
          if (k === 0) controls.cancelTurn()
          if (k === 1) controls.disconnect()
          if (k === 2) { controls.cancelStartup(); controls.stale() }
          await frame()
          controls.stop()
          if (checkpoints.includes(i)) out.push({ cycle: i, domNodes: domNodes(), subscribers, driverTimers: drivers.size, pendingTimers: svc?.getDiagnostics().pendingTimers ?? 0 })
        }
        return out
      },
    }
    return { controls, onRender: (_id: string, _phase: string, actual: number) => { commits++; renderMs += actual } }
  }, [])
  useEffect(() => {
    (window as unknown as { __perf: PerfControls }).__perf = ctl.controls
  }, [ctl])
  return (
    <main data-fixture="perf" style={{ padding: 16, width: 'fit-content' }}>
      <h1 style={{ fontSize: 14 }}>Visual core fixture</h1>
      {session ? (
        <Profiler id="visual-core" onRender={ctl.onRender}>
          <VisualCore source={session.source} variant={variant} motion={motion} />
        </Profiler>
      ) : <p data-unmounted="">unmounted</p>}
    </main>
  )
}

function Fixtures() {
  const q = new URLSearchParams(location.search)
  const state = q.get('state')?.toUpperCase() as VisualState | undefined
  const variant = q.get('variant') as VariantId | null
  const motion = q.get('motion') as MotionMode | null
  const noSource = q.has('nosource')
  const scenarioParam = q.get('scenario') as StartupScenario | null
  const scenario = scenarioParam && STARTUP_SCENARIOS.includes(scenarioParam) ? scenarioParam : undefined

  if (q.has('perf')) {
    return <PerfFixture variant={variant && VARIANTS.includes(variant) ? variant : 'command-center'} motion={motion && MOTIONS.includes(motion) ? motion : 'full'} />
  }

  if (q.has('a11y')) {
    return <A11yFixture variant={variant && VARIANTS.includes(variant) ? variant : 'command-center'} motion={motion && MOTIONS.includes(motion) ? motion : 'full'} />
  }

  if (scenario) {
    const v = variant && VARIANTS.includes(variant) ? variant : 'startup'
    const m: MotionMode = scenario === 'startup-reduced' ? 'reduced' : 'full'
    return (
      <main data-fixture="single" data-scenario={scenario} style={{ padding: 16, width: 'fit-content' }}>
        <h1 style={{ fontSize: 14 }}>Visual core fixture</h1>
        <Cell state={scenario === 'startup-interrupted' ? 'STOPPING' : 'STARTING'} variant={v} motion={m} scenario={scenario} />
      </main>
    )
  }

  if (state || variant || motion || noSource) {
    const s = state && VISUAL_STATES.includes(state) ? state : 'IDLE'
    const v = variant && VARIANTS.includes(variant) ? variant : 'command-center'
    const m = motion && MOTIONS.includes(motion) ? motion : 'full'
    return (
      <main data-fixture="single" style={{ padding: 16, width: 'fit-content' }}>
        <h1 style={{ fontSize: 14 }}>Visual core fixture</h1>
        <Cell state={s} variant={v} motion={m} noSource={noSource} />
      </main>
    )
  }

  return (
    <main data-fixture="grid" style={{ width: 1880, padding: 16 }}>
      <h1 style={{ fontSize: 14 }}>Visual core fixtures</h1>
      <div data-grid="main">
        {VARIANTS.map(v => (
          <Row key={v} name={v} variant={v} motion="full" />
        ))}
      </div>
      <div data-grid="motion">
        <Row name="reduced" variant="expanded-overlay" motion="reduced" />
        <Row name="disabled" variant="expanded-overlay" motion="disabled" />
      </div>
    </main>
  )
}

const rootElement = document.getElementById('root')
if (!rootElement) throw new Error('root element not found')
createRoot(rootElement).render(
  <StrictMode>
    <Fixtures />
  </StrictMode>,
)
