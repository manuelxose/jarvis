import { Component, useEffect, useRef, useState, useSyncExternalStore, type ErrorInfo, type ReactNode } from 'react'
import type { VisualCoreSemantic, VisualCoreSource } from '../../../services/visual-core-service.ts'
import {
  GEOMETRY, GLYPHS, PHASE_TICK_PATHS, STATE_SPECS, VARIANT_SPECS, frameFor, resolveMotionMode,
  type Frame, type MotionMode, type Primitive, type VariantId,
} from './visualCoreGeometry.ts'
import type { StartupPhase, VisualState } from './visualCoreMachine.ts'
import styles from './VisualCore.module.css'

export interface VisualCoreProps {
  source?: VisualCoreSource
  variant: VariantId
  motion?: MotionMode
  className?: string
}

const OFFLINE: VisualCoreSemantic = {
  state: 'DISCONNECTED', label: STATE_SPECS.DISCONNECTED.label, detail: null,
  connection: 'disconnected', awaitingSnapshot: false, reason: 'disconnected', partial: false, startup: 'idle', apps: null,
}
const noopSubscribe = () => () => {}
const offlineSnapshot = () => OFFLINE
const REDUCED_QUERY = '(prefers-reduced-motion: reduce)'

function subscribeReduced(cb: () => void) {
  if (typeof matchMedia !== 'function') return () => {}
  const mq = matchMedia(REDUCED_QUERY)
  mq.addEventListener('change', cb)
  return () => mq.removeEventListener('change', cb)
}
const reducedSnapshot = () => typeof matchMedia === 'function' && matchMedia(REDUCED_QUERY).matches

const PHASE_TICKS_PATH = GLYPHS.phaseTicks[0]!.kind === 'path' ? (GLYPHS.phaseTicks[0] as { d: string }).d : ''
const LIT_TICKS: Record<StartupPhase, number> = { idle: 1, acknowledged: 2, initializing: 3, announcing: 4, ready: 5, degraded: 5, failed: 5, cancelled: 5 }

function Glyph({ items, startup }: { items: readonly Primitive[]; startup?: StartupPhase }) {
  const lit = startup ? LIT_TICKS[startup] : 0
  return (
    <>
      {items.map((p, i) => {
        if (lit && p.kind === 'path' && p.d === PHASE_TICKS_PATH) {
          return (
            <g key={i}>
              {PHASE_TICK_PATHS.map((d, n) => <path key={n} className={styles.glyph} data-tick={n < lit ? 'lit' : 'dim'} d={d} />)}
            </g>
          )
        }
        if (p.kind === 'circle') return <circle key={i} className={styles.glyphFill} cx={p.cx} cy={p.cy} r={p.r} />
        if (p.kind === 'rect') return <rect key={i} className={styles.glyph} x={p.x} y={p.y} width={p.width} height={p.height} />
        return <path key={i} className={styles.glyph} d={p.d} />
      })}
    </>
  )
}

function dashAttr(dash: readonly number[]) {
  return dash.length ? dash.join(' ') : undefined
}

function appsLine(apps: VisualCoreSemantic['apps']) {
  return apps ? `Apps: ${apps.ready} ready, ${apps.failed} failed` : 'Apps: status unavailable'
}

/** Minimum spacing between polite live-region writes; the latest text always wins when the window closes. */
export const ANNOUNCE_INTERVAL_MS = 500

function announcementText(snap: VisualCoreSemantic) {
  return [snap.label, snap.detail, snap.startup !== 'idle' ? appsLine(snap.apps) : null].filter(Boolean).join('. ')
}

function useAnnouncement(text: string, source: VisualCoreSource | undefined) {
  const [announced, setAnnounced] = useState(text)
  const latest = useRef(text)
  const lastWrite = useRef(0)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  latest.current = text
  useEffect(() => {
    const wait = lastWrite.current + ANNOUNCE_INTERVAL_MS - performance.now()
    if (wait <= 0) {
      lastWrite.current = performance.now()
      setAnnounced(text)
    } else if (!timer.current) {
      timer.current = setTimeout(() => {
        timer.current = null
        lastWrite.current = performance.now()
        setAnnounced(latest.current)
      }, wait)
    }
  }, [text])
  useEffect(() => {
    setAnnounced(latest.current)
    return () => {
      if (timer.current) clearTimeout(timer.current)
      timer.current = null
      lastWrite.current = 0
    }
  }, [source])
  return announced
}

function Label({ snap, announced }: { snap: VisualCoreSemantic; announced: string }) {
  return (
    <div className={styles.label}>
      {snap.label}
      {snap.detail ? <span className={styles.detail}>{snap.detail}</span> : null}
      {snap.startup !== 'idle' ? <span className={styles.detail}>{appsLine(snap.apps)}</span> : null}
      <span className={styles.srOnly} role="status" aria-live="polite" aria-atomic="true" data-announcement="">{announced}</span>
    </div>
  )
}

// Minimum ms between drawn frames. Live audio level needs display rate, rotation looks smooth at 30 fps and a
// slow few-percent breath at 15. Without GPU vsync (the shell disables acceleration) rAF itself fires 200+ times a
// second, so frames are paced with a timer and rAF is only used to draw one.
const LEVEL_FRAME_MS = 12
const ROTATION_FRAME_MS = 33
const BREATH_FRAME_MS = 66

interface CoreSvgProps { startup: StartupPhase; state: VisualState; variant: VariantId; mode: MotionMode; source?: VisualCoreSource }

function CoreSvg({ startup, state, variant, mode, source }: CoreSvgProps) {
  const spec = STATE_SPECS[state]
  const v = VARIANT_SPECS[variant]
  const arcGroup = useRef<SVGGElement>(null)
  const glyphGroup = useRef<SVGGElement>(null)
  const ringRefs = useRef<(SVGCircleElement | null)[]>([])

  const base: Frame = frameFor({ state, variant, mode, t: 0, level: 0 })
  const extraRings = spec.levelScaleMax > 0 ? Math.max(0, v.levelRings - 1) : 0

  useEffect(() => {
    // Nothing to animate (SUCCESS, ERROR, ...): no loop, the t=0 frame is the final one.
    const animated = spec.breathPeriodMs > 0 || spec.rotationPeriodMs > 0 || spec.levelScaleMax > 0
    if (mode !== 'full' || !source || !animated) return
    const minFrameMs = spec.levelScaleMax > 0 ? LEVEL_FRAME_MS : spec.rotationPeriodMs > 0 ? ROTATION_FRAME_MS : BREATH_FRAME_MS
    let raf = 0
    let timer = 0
    const apply = (f: Frame) => {
      const k = f.scale * (f.ringScales[0] ?? 1)
      arcGroup.current?.setAttribute('transform', `rotate(${f.rotationDeg}) scale(${k})`)
      glyphGroup.current?.setAttribute('transform', `scale(${f.scale})`)
      ringRefs.current.forEach((el, i) => {
        if (!el) return
        const live = f.ringScales.length > i + 1
        el.setAttribute('opacity', live ? String(0.35 / (i + 1)) : '0')
        el.setAttribute('transform', `scale(${f.scale * (f.ringScales[i + 1] ?? 1)})`)
      })
    }
    const loop = (now: number) => {
      raf = 0
      const sig = source.readSignals(now)
      apply(frameFor({ state, variant, mode, t: now, level: sig.output }))
      timer = window.setTimeout(() => { timer = 0; raf = requestAnimationFrame(loop) }, Math.max(0, minFrameMs - (performance.now() - now)))
    }
    const start = () => { if (!raf && !timer && document.visibilityState === 'visible') raf = requestAnimationFrame(loop) }
    const stop = () => { if (raf) cancelAnimationFrame(raf); if (timer) clearTimeout(timer); raf = timer = 0 }
    const onVisibility = () => (document.visibilityState === 'visible' ? start() : stop())
    document.addEventListener('visibilitychange', onVisibility)
    start()
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      stop()
      apply(frameFor({ state, variant, mode: 'reduced', t: 0, level: 0 }))
    }
  }, [mode, source, spec, state, variant])

  const glowToken = base.glow
  return (
    <div
      className={styles.stage}
      style={{
        width: v.corePx,
        height: v.corePx,
        boxShadow: glowToken ? `var(${glowToken})` : undefined,
        transition: base.transitionMs ? `box-shadow ${base.transitionMs}ms ease` : undefined,
      }}
    >
      <svg className={styles.svg} viewBox="-1 -1 2 2" aria-hidden="true" focusable="false">
        {Array.from({ length: extraRings }, (_, i) => (
          <circle
            key={i}
            ref={el => { ringRefs.current[i] = el }}
            r={GEOMETRY.ringRadius + GEOMETRY.levelRingStep * (i + 1)}
            fill="none"
            stroke={`var(${spec.token})`}
            strokeWidth={base.strokeWidth}
            opacity={0}
          />
        ))}
        <g ref={arcGroup}>
          <path
            d={base.arcPath}
            fill="none"
            stroke={`var(${spec.token})`}
            strokeWidth={base.strokeWidth}
            strokeLinecap={GEOMETRY.strokeCap}
            strokeDasharray={dashAttr(base.dash)}
          />
        </g>
        <g ref={glyphGroup} strokeWidth={base.strokeWidth}>
          <Glyph items={base.glyph} startup={startup} />
        </g>
      </svg>
    </div>
  )
}

class CoreBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('VisualCore renderer failed; using static fallback', error, info.componentStack)
  }
  render() { return this.state.failed ? this.props.fallback : this.props.children }
}

function StaticCore({ state, variant, startup }: { state: VisualState; variant: VariantId; startup: StartupPhase }) {
  const f = frameFor({ state, variant, mode: 'disabled', t: 0, level: 0 })
  const px = VARIANT_SPECS[variant].corePx
  return (
    <div className={styles.stage} style={{ width: px, height: px }}>
      <svg className={styles.svg} viewBox="-1 -1 2 2" aria-hidden="true" focusable="false">
        <path d={f.arcPath} fill="none" stroke={`var(${STATE_SPECS[state].token})`} strokeWidth={f.strokeWidth} strokeLinecap="round" strokeDasharray={dashAttr(f.dash)} />
        <g strokeWidth={f.strokeWidth}><Glyph items={f.glyph} startup={startup} /></g>
      </svg>
    </div>
  )
}

export function VisualCore({ source, variant, motion = 'full', className }: VisualCoreProps) {
  const snap = useSyncExternalStore(source ? source.subscribe : noopSubscribe, source ? source.getSnapshot : offlineSnapshot)
  const osReduced = useSyncExternalStore(subscribeReduced, reducedSnapshot, () => false)
  const mode = source ? resolveMotionMode({ setting: motion, osReduced }) : 'disabled'
  const announced = useAnnouncement(announcementText(snap), source)
  const classes = [styles.root, variant === 'compact-overlay' ? styles.compact : '', className ?? ''].filter(Boolean).join(' ')

  const staticView = (
    <div className={classes} data-state={snap.state} data-startup-phase={snap.startup} data-variant={variant} data-motion="disabled" data-renderer="static">
      <StaticCore state={snap.state} variant={variant} startup={snap.startup} />
      <Label snap={snap} announced={announced} />
    </div>
  )
  if (!source) return staticView

  return (
    <CoreBoundary
      key={`${snap.state}:${variant}`}
      fallback={staticView}
    >
      <div className={classes} data-state={snap.state} data-startup-phase={snap.startup} data-variant={variant} data-motion={mode} data-renderer="svg">
        <CoreSvg startup={snap.startup} state={snap.state} variant={variant} mode={mode} source={source} />
        <Label snap={snap} announced={announced} />
      </div>
    </CoreBoundary>
  )
}

export default VisualCore
