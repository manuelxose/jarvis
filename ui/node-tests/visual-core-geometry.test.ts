import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { describe, it } from 'node:test'
import {
  arcPath, DASH_PATTERNS, frameFor, GEOMETRY, GLYPHS, resolveMotionMode, STATE_SPECS, VARIANT_SPECS,
  type MotionMode, type VariantId,
} from '../src/components/jarvis/visual-core/visualCoreGeometry.ts'
import { VISUAL_STATES } from '../src/components/jarvis/visual-core/visualCoreMachine.ts'

const spec = JSON.parse(readFileSync(new URL('../../docs/design/reactive-core-spec.json', import.meta.url), 'utf8'))
const variants = Object.keys(VARIANT_SPECS) as VariantId[]
const modes: MotionMode[] = ['full', 'reduced', 'disabled']
const times = [0, 1, 333, 1200, 2399, 5000, 123456.7]
const EPS = 1e-9

describe('visual core geometry matches reactive-core-spec.json', () => {
  it('geometry constants and dash patterns', () => {
    const g = spec.identity.geometry
    assert.equal(GEOMETRY.ringRadius, g.ringRadius)
    assert.equal(GEOMETRY.strokeWidth, g.strokeWidth)
    assert.equal(GEOMETRY.maxExtent, g.maxExtent)
    assert.equal(GEOMETRY.strokeCap, g.strokeCap)
    assert.deepEqual(GEOMETRY.viewBox, g.viewBox)
    assert.deepEqual(DASH_PATTERNS, g.dashPatterns)
    assert.deepEqual(Object.keys(GLYPHS).sort(), Object.keys(g.glyphs).sort())
  })

  it('every state value', () => {
    assert.deepEqual(spec.states.map((s: { id: string }) => s.id).sort(), [...VISUAL_STATES].sort())
    for (const s of spec.states) {
      const m = STATE_SPECS[s.id as keyof typeof STATE_SPECS]
      assert.equal(m.label, s.label, s.id)
      assert.equal(m.token, s.token, s.id)
      assert.equal(m.glowToken, s.glow, s.id)
      assert.equal(m.arcSweepDeg, s.geometry.arcSweepDeg, s.id)
      assert.equal(m.dash, s.geometry.dash, s.id)
      assert.equal(m.glyph, s.geometry.glyph, s.id)
      assert.equal(m.breathPeriodMs, s.motion.breathPeriodMs, s.id)
      assert.equal(m.breathAmplitude, s.motion.breathAmplitude, s.id)
      assert.equal(m.rotationPeriodMs, s.motion.rotationPeriodMs, s.id)
      assert.equal(m.levelScaleMax, s.motion.levelScaleMax, s.id)
      assert.equal(m.transitionInMs, s.transitionInMs, s.id)
    }
  })

  it('every variant value', () => {
    for (const v of spec.variants) {
      const m = VARIANT_SPECS[v.id as VariantId]
      assert.equal(m.corePx, v.corePx, v.id)
      assert.equal(m.levelRings, v.detailBudget.levelRings, v.id)
      assert.equal(m.glow, v.detailBudget.glow, v.id)
      assert.equal(m.elements, v.detailBudget.elements, v.id)
      assert.equal(m.minStrokePx, v.legibility.minStrokePx, v.id)
    }
    assert.equal(variants.length, spec.variants.length)
  })

  it('ten states have distinct glyph+label pairs', () => {
    const pairs = new Set(VISUAL_STATES.map((s) => `${STATE_SPECS[s].glyph}|${STATE_SPECS[s].label}`))
    assert.equal(pairs.size, 10)
  })
})

describe('frameFor', () => {
  it('reduced/disabled frames are static at every t and ignore level', () => {
    for (const mode of ['reduced', 'disabled'] as const) {
      for (const state of VISUAL_STATES) for (const variant of variants) {
        const base = frameFor({ state, variant, mode, t: 0, level: 0 })
        assert.equal(base.transitionMs, 0)
        assert.equal(base.rotationDeg, 0)
        assert.equal(base.scale, 1)
        assert.deepEqual(base.ringScales, [1])
        assert.equal(base.glow, null)
        for (const t of times) assert.deepEqual(frameFor({ state, variant, mode, t, level: 1 }), base)
      }
    }
  })

  it('full mode breath and clockwise rotation follow the formula', () => {
    const idle = STATE_SPECS.IDLE
    assert.equal(frameFor({ state: 'IDLE', variant: 'command-center', mode: 'full', t: 0 }).scale, 1)
    const peak = frameFor({ state: 'IDLE', variant: 'command-center', mode: 'full', t: idle.breathPeriodMs / 2 })
    assert.ok(Math.abs(peak.scale - (1 + idle.breathAmplitude)) < EPS)
    const half = frameFor({ state: 'THINKING', variant: 'command-center', mode: 'full', t: 600 })
    assert.ok(Math.abs(half.rotationDeg - 90) < EPS)
    assert.equal(half.scale, 1)
  })

  it('rotation is never negative, including negative or non-finite t', () => {
    for (const t of [-5000, -1, Number.NaN, Number.NEGATIVE_INFINITY, ...times]) {
      for (const state of VISUAL_STATES) {
        const f = frameFor({ state, variant: 'startup', mode: 'full', t })
        assert.ok(f.rotationDeg >= 0 && f.rotationDeg < 360, `${state} t=${t}`)
        assert.ok(Number.isFinite(f.scale))
      }
    }
  })

  it('non-finite or absent level yields no level scale', () => {
    for (const level of [undefined, Number.NaN, Infinity, -Infinity, 0, -0.5]) {
      const f = frameFor({ state: 'SPEAKING', variant: 'command-center', mode: 'full', t: 0, level })
      assert.deepEqual(f.ringScales, [1], String(level))
    }
  })

  it('level rings scale 1+levelScaleMax*y and are limited by the variant', () => {
    const f = frameFor({ state: 'SPEAKING', variant: 'command-center', mode: 'full', t: 0, level: 0.5 })
    assert.equal(f.ringScales.length, 3)
    assert.ok(Math.abs(f.ringScales[0] - 1.06) < EPS)
    assert.equal(frameFor({ state: 'SPEAKING', variant: 'expanded-overlay', mode: 'full', t: 0, level: 0.5 }).ringScales.length, 2)
    assert.equal(frameFor({ state: 'SPEAKING', variant: 'compact-overlay', mode: 'full', t: 0, level: 0.5 }).ringScales.length, 1)
    assert.deepEqual(frameFor({ state: 'THINKING', variant: 'startup', mode: 'full', t: 0, level: 1 }).ringScales, [1])
  })

  it('level above 1 clamps to 1', () => {
    const a = frameFor({ state: 'SPEAKING', variant: 'expanded-overlay', mode: 'full', t: 0, level: 2 })
    const b = frameFor({ state: 'SPEAKING', variant: 'expanded-overlay', mode: 'full', t: 0, level: 1 })
    assert.deepEqual(a, b)
  })

  it('extent including stroke never exceeds maxExtent at peak', () => {
    for (const state of VISUAL_STATES) for (const variant of variants) {
      const stateSpec = STATE_SPECS[state]
      for (const t of [0, stateSpec.breathPeriodMs / 2 || 1]) {
        const f = frameFor({ state, variant, mode: 'full', t, level: 1 })
        const ringOuter = GEOMETRY.ringRadius + GEOMETRY.levelRingStep * (f.ringScales.length - 1)
        const outer = Math.max(ringOuter, stateSpec.glyph === 'phaseTicks' ? 0.82 : 0)
        const extent = f.scale * Math.max(...f.ringScales) * (outer + f.strokeWidth / 2)
        assert.ok(extent <= GEOMETRY.maxExtent + EPS, `${state}/${variant} extent ${extent}`)
      }
    }
  })

  it('stroke is at least minStrokePx at the variant core size', () => {
    for (const variant of variants) {
      const { corePx, minStrokePx } = VARIANT_SPECS[variant]
      const f = frameFor({ state: 'IDLE', variant, mode: 'full', t: 0 })
      assert.ok((f.strokeWidth * corePx) / 2 >= minStrokePx - EPS, variant)
      assert.ok(f.strokeWidth >= GEOMETRY.strokeWidth)
    }
  })

  it('glow only when the variant allows and mode is full', () => {
    assert.equal(frameFor({ state: 'IDLE', variant: 'compact-overlay', mode: 'full', t: 0 }).glow, null)
    assert.equal(frameFor({ state: 'IDLE', variant: 'command-center', mode: 'full', t: 0 }).glow, '--glow-core')
    assert.equal(frameFor({ state: 'SPEAKING', variant: 'command-center', mode: 'full', t: 0 }).glow, '--glow-core-inset')
    assert.equal(frameFor({ state: 'ERROR', variant: 'command-center', mode: 'full', t: 0 }).glow, null)
  })

  it('transition durations: full uses the state value; every duration is allowed', () => {
    for (const state of VISUAL_STATES) {
      const ms = frameFor({ state, variant: 'startup', mode: 'full', t: 0 }).transitionMs
      assert.equal(ms, STATE_SPECS[state].transitionInMs)
      assert.ok(spec.identity.transition.allowedMs.includes(ms))
    }
    for (const state of spec.identity.transition.safetyStatesSnap) {
      assert.equal(frameFor({ state, variant: 'startup', mode: 'full', t: 0 }).transitionMs, 0)
    }
  })
})

describe('resolveMotionMode', () => {
  it('precedence matrix: disabled > OS reduce > reduced > full', () => {
    const expected: Record<string, MotionMode> = {
      'disabled,false': 'disabled', 'disabled,true': 'disabled',
      'reduced,false': 'reduced', 'reduced,true': 'reduced',
      'full,false': 'full', 'full,true': 'reduced',
    }
    for (const setting of modes) for (const osReduced of [false, true]) {
      assert.equal(resolveMotionMode({ setting, osReduced }), expected[`${setting},${osReduced}`])
    }
  })
})

describe('arcPath', () => {
  it('starts at top and sweeps clockwise', () => {
    assert.equal(arcPath(90, 0.5), 'M 0 -0.5 A 0.5 0.5 0 0 1 0.5 0')
    assert.equal(arcPath(270, 0.5), 'M 0 -0.5 A 0.5 0.5 0 1 1 -0.5 0')
    assert.match(arcPath(360, 0.62), /^M 0 -0\.62 A 0\.62 0\.62 0 1 1 0 0\.62 A 0\.62 0\.62 0 1 1 0 -0\.62$/)
  })
  it('rejects degenerate input', () => {
    for (const [s, r] of [[0, 1], [-10, 1], [Number.NaN, 1], [90, 0], [90, Infinity]]) assert.equal(arcPath(s, r), '')
    assert.equal(arcPath(720, 0.5), arcPath(360, 0.5))
  })
})
