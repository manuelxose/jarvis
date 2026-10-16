/** Pure, renderer-neutral geometry and motion model for the 2D visual core.
 * Derived from docs/design/reactive-core-spec.json; t (ms) and level are inputs, no clock inside.
 */
import type { VisualState } from './visualCoreMachine.ts'

export type MotionMode = 'full' | 'reduced' | 'disabled'
export type VariantId = 'compact-overlay' | 'expanded-overlay' | 'command-center' | 'startup'
export type DashId = 'solid' | 'dashed' | 'dotted' | 'segmented'
export type GlyphId = 'dot' | 'tick' | 'dots' | 'bars' | 'square' | 'check' | 'error' | 'slash' | 'phaseTicks' | 'stopBars'
export type Primitive =
  | { readonly kind: 'circle'; readonly cx: number; readonly cy: number; readonly r: number; readonly fill: boolean }
  | { readonly kind: 'rect'; readonly x: number; readonly y: number; readonly width: number; readonly height: number }
  | { readonly kind: 'path'; readonly d: string }

export const GEOMETRY = { viewBox: [-1, -1, 2, 2], ringRadius: 0.62, levelRingStep: 0.08, strokeWidth: 0.035, strokeCap: 'round', maxExtent: 0.9 } as const
export const DASH_PATTERNS: Readonly<Record<DashId, readonly number[]>> = {
  solid: [], dashed: [0.14, 0.10], dotted: [0.01, 0.11], segmented: [0.38, 0.12],
}

export const PHASE_TICK_PATHS: readonly string[] = [0, 72, 144, 216, 288].map((deg) => {
  const rad = (deg * Math.PI) / 180
  const [s, c] = [Math.sin(rad), -Math.cos(rad)]
  const p = (r: number) => `${(s * r).toFixed(4)} ${(c * r).toFixed(4)}`
  return `M ${p(0.74)} L ${p(0.82)}`
})
const phaseTickPath = PHASE_TICK_PATHS.join(' ')

export const GLYPHS: Readonly<Record<GlyphId, readonly Primitive[]>> = {
  dot: [{ kind: 'circle', cx: 0, cy: 0, r: 0.12, fill: true }],
  tick: [{ kind: 'path', d: 'M 0 -0.48 L 0 -0.72' }],
  dots: [-0.18, 0, 0.18].map((cx) => ({ kind: 'circle', cx, cy: 0, r: 0.05, fill: true }) as const),
  bars: [{ kind: 'path', d: 'M -0.18 -0.14 V 0.14 M 0 -0.24 V 0.24 M 0.18 -0.14 V 0.14' }],
  square: [{ kind: 'rect', x: -0.18, y: -0.18, width: 0.36, height: 0.36 }],
  check: [{ kind: 'path', d: 'M -0.24 0 L -0.06 0.18 L 0.28 -0.20' }],
  error: [{ kind: 'path', d: 'M 0 -0.28 L 0.28 0.22 L -0.28 0.22 Z M 0 -0.12 V 0.06 M 0 0.15 L 0 0.16' }],
  slash: [{ kind: 'path', d: 'M -0.24 0.24 L 0.24 -0.24' }],
  phaseTicks: [{ kind: 'path', d: phaseTickPath }, { kind: 'circle', cx: 0, cy: 0, r: 0.12, fill: true }],
  stopBars: [{ kind: 'path', d: 'M -0.10 -0.20 V 0.20 M 0.10 -0.20 V 0.20' }],
}
/** Outermost drawn radius contributed by a glyph (phase ticks reach 0.82). */
const GLYPH_OUTER: Partial<Record<GlyphId, number>> = { phaseTicks: 0.82 }

export interface StateSpec {
  readonly token: string
  readonly glowToken: string | null
  readonly arcSweepDeg: number
  readonly dash: DashId
  readonly glyph: GlyphId
  readonly breathPeriodMs: number
  readonly breathAmplitude: number
  readonly rotationPeriodMs: number
  readonly levelScaleMax: number
  readonly transitionInMs: number
  readonly label: string
}
const S = (
  label: string, token: string, glowToken: string | null, arcSweepDeg: number, dash: DashId, glyph: GlyphId,
  breathPeriodMs: number, breathAmplitude: number, rotationPeriodMs: number, levelScaleMax: number, transitionInMs: number,
): StateSpec => ({ label, token, glowToken, arcSweepDeg, dash, glyph, breathPeriodMs, breathAmplitude, rotationPeriodMs, levelScaleMax, transitionInMs })
export const STATE_SPECS: Readonly<Record<VisualState, StateSpec>> = {
  IDLE: S('Idle', '--accent-listening', '--glow-core', 360, 'solid', 'dot', 4000, 0.03, 0, 0, 320),
  LISTENING: S('Listening', '--accent-listening', '--glow-core', 270, 'solid', 'tick', 2400, 0.04, 0, 0, 200),
  THINKING: S('Thinking', '--accent-processing', '--glow-core', 360, 'dashed', 'dots', 0, 0, 2400, 0, 200),
  SPEAKING: S('Speaking', '--accent-speaking', '--glow-core-inset', 360, 'solid', 'bars', 0, 0, 0, 0.12, 120),
  EXECUTING: S('Executing', '--accent-processing', '--glow-core', 300, 'segmented', 'square', 0, 0, 1600, 0, 120),
  SUCCESS: S('Done', '--state-success', null, 360, 'solid', 'check', 0, 0, 0, 0, 120),
  ERROR: S('Error', '--state-danger', null, 300, 'solid', 'error', 0, 0, 0, 0, 0),
  DISCONNECTED: S('Offline', '--state-offline', null, 360, 'dotted', 'slash', 0, 0, 0, 0, 0),
  STARTING: S('Starting', '--accent-listening', '--glow-core', 360, 'solid', 'phaseTicks', 0, 0, 0, 0.12, 320),
  STOPPING: S('Stopping', '--text-secondary', null, 360, 'solid', 'stopBars', 0, 0, 0, 0, 0),
}

export interface VariantSpec {
  readonly corePx: number
  readonly levelRings: number
  readonly glow: boolean
  readonly elements: number
  readonly minStrokePx: number
}
export const VARIANT_SPECS: Readonly<Record<VariantId, VariantSpec>> = {
  'compact-overlay': { corePx: 40, levelRings: 1, glow: false, elements: 6, minStrokePx: 2 },
  'expanded-overlay': { corePx: 96, levelRings: 2, glow: true, elements: 12, minStrokePx: 2 },
  'command-center': { corePx: 240, levelRings: 3, glow: true, elements: 24, minStrokePx: 2 },
  startup: { corePx: 320, levelRings: 3, glow: true, elements: 24, minStrokePx: 2 },
}

/** Precedence: user disabled > OS prefers-reduced-motion > user reduced > user full. */
export function resolveMotionMode({ setting, osReduced }: { setting: MotionMode; osReduced: boolean }): MotionMode {
  if (setting === 'disabled') return 'disabled'
  if (osReduced || setting === 'reduced') return 'reduced'
  return 'full'
}

const round = (n: number) => Math.round(n * 1e6) / 1e6 + 0

/** Arc from the top (-90 deg) clockwise; 360 is two half arcs because SVG cannot draw a full-circle arc. */
export function arcPath(sweepDeg: number, r: number): string {
  if (!Number.isFinite(sweepDeg) || !Number.isFinite(r) || sweepDeg <= 0 || r <= 0) return ''
  if (sweepDeg >= 360) return `M 0 ${round(-r)} A ${r} ${r} 0 1 1 0 ${round(r)} A ${r} ${r} 0 1 1 0 ${round(-r)}`
  const end = ((-90 + sweepDeg) * Math.PI) / 180
  return `M 0 ${round(-r)} A ${r} ${r} 0 ${sweepDeg > 180 ? 1 : 0} 1 ${round(r * Math.cos(end))} ${round(r * Math.sin(end))}`
}

export interface Frame {
  readonly rotationDeg: number
  /** Group scale (breath). */
  readonly scale: number
  /** Per-ring multiplier applied on top of scale; length 1 unless live level rings are drawn. */
  readonly ringScales: readonly number[]
  readonly glow: string | null
  readonly strokeWidth: number
  readonly dash: readonly number[]
  readonly glyph: readonly Primitive[]
  readonly arcPath: string
  readonly transitionMs: number
}

export function frameFor({ state, variant, mode, t, level }: {
  state: VisualState; variant: VariantId; mode: MotionMode; t: number; level?: number
}): Frame {
  const s = STATE_SPECS[state]
  const v = VARIANT_SPECS[variant]
  const full = mode === 'full'
  const time = Number.isFinite(t) && t > 0 ? t : 0
  const strokeWidth = Math.max(GEOMETRY.strokeWidth, (v.minStrokePx * 2) / v.corePx)

  const breath = full && s.breathPeriodMs > 0
    ? 1 + (s.breathAmplitude * (1 - Math.cos((2 * Math.PI * time) / s.breathPeriodMs))) / 2
    : 1
  const rotationDeg = full && s.rotationPeriodMs > 0 ? ((360 * time) / s.rotationPeriodMs) % 360 : 0
  const y = typeof level === 'number' && Number.isFinite(level) ? Math.min(1, Math.max(0, level)) : 0
  const live = full && s.levelScaleMax > 0 && y > 0
  const rings = live ? v.levelRings : 1
  const outer = Math.max(GEOMETRY.ringRadius + GEOMETRY.levelRingStep * (rings - 1), GLYPH_OUTER[s.glyph] ?? 0)

  const maxTotal = GEOMETRY.maxExtent / (outer + strokeWidth / 2)
  const scale = Math.min(breath, maxTotal)
  const levelFactor = Math.min(live ? 1 + s.levelScaleMax * y : 1, maxTotal / scale)

  return {
    rotationDeg,
    scale,
    ringScales: Array.from({ length: rings }, () => levelFactor),
    glow: full && v.glow ? s.glowToken : null,
    strokeWidth,
    dash: DASH_PATTERNS[s.dash],
    glyph: GLYPHS[s.glyph],
    arcPath: arcPath(s.arcSweepDeg, GEOMETRY.ringRadius),
    transitionMs: full ? s.transitionInMs : 0,
  }
}
