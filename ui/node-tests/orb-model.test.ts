import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  foldOrbSignal,
  INITIAL_SIGNAL,
  STATES,
  stepParams,
  targetParams,
  type OrbParams,
  type OrbSignal,
} from '../src/components/jarvis/orb/orbModel.ts'
import type { JarvisEvent } from '../src/protocol/events.ts'

const env = { ts: 0, seq: 0 } as never
const voice = (state: string): JarvisEvent => ({ ...env, name: 'voice.state', state, reason: '' }) as JarvisEvent
const started = { ...env, name: 'speech.started', trace_id: 't', source: 'live' } as JarvisEvent
const completed = { ...env, name: 'speech.completed', trace_id: 't', cancelled: false } as JarvisEvent
const other = { ...env, name: 'voice.loading', reason: 'x' } as JarvisEvent

const inRange = (p: OrbParams) => {
  for (const v of [p.hue, p.energy, p.turbulence]) assert.ok(Number.isFinite(v) && v >= 0 && v <= 1, `out of range: ${v}`)
}

describe('foldOrbSignal', () => {
  it('sets state from a known voice.state', () => {
    assert.deepEqual(foldOrbSignal(INITIAL_SIGNAL, voice('listening')), { state: 'listening', speaking: false })
  })

  it('sets and clears speaking from speech events', () => {
    const on = foldOrbSignal(INITIAL_SIGNAL, started)
    assert.equal(on.speaking, true)
    assert.equal(foldOrbSignal(on, completed).speaking, false)
  })

  it('clears speaking on idle, offline and error but not on other states', () => {
    const speaking: OrbSignal = { state: 'speaking', speaking: true }
    for (const s of ['idle', 'offline', 'error']) assert.equal(foldOrbSignal(speaking, voice(s)).speaking, false, s)
    assert.equal(foldOrbSignal(speaking, voice('processing')).speaking, true)
  })

  it('ignores unknown states, keeping the reference', () => {
    const prev: OrbSignal = { state: 'listening', speaking: false }
    assert.equal(foldOrbSignal(prev, voice('warp-speed')), prev)
    assert.equal(foldOrbSignal(prev, voice('')), prev)
  })

  it('ignores unrelated events, keeping the reference', () => {
    const prev: OrbSignal = { state: 'listening', speaking: true }
    assert.equal(foldOrbSignal(prev, other), prev)
  })

  it('keeps the reference for no-op transitions, including speech.completed without a start', () => {
    assert.equal(foldOrbSignal(INITIAL_SIGNAL, completed), INITIAL_SIGNAL)
    assert.equal(foldOrbSignal(INITIAL_SIGNAL, voice('idle')), INITIAL_SIGNAL)
    const on = foldOrbSignal(INITIAL_SIGNAL, started)
    assert.equal(foldOrbSignal(on, started), on)
  })
})

describe('targetParams', () => {
  it('maps every known state to params within 0..1', () => {
    for (const state of STATES) {
      inRange(targetParams({ state: state as OrbSignal['state'], speaking: false }))
      inRange(targetParams({ state: state as OrbSignal['state'], speaking: true }))
    }
  })

  it('gives speaking the most energy and turbulence, more than idle', () => {
    const speaking = targetParams({ state: 'listening', speaking: true })
    const idle = targetParams(INITIAL_SIGNAL)
    assert.ok(speaking.energy > idle.energy)
    assert.ok(speaking.turbulence > idle.turbulence)
    for (const state of STATES) {
      const p = targetParams({ state: state as OrbSignal['state'], speaking: false })
      assert.ok(p.energy <= speaking.energy, state)
    }
  })

  it('gives offline lower energy than idle and distinct hues for listening, speaking and error', () => {
    assert.ok(targetParams({ state: 'offline', speaking: false }).energy < targetParams(INITIAL_SIGNAL).energy)
    const hues = (['listening', 'speaking', 'error'] as const).map((state) => targetParams({ state, speaking: false }).hue)
    assert.equal(new Set(hues).size, 3)
  })

  it('falls back to idle params for an unexpected state at runtime', () => {
    assert.deepEqual(targetParams({ state: 'bogus' as never, speaking: false }), targetParams(INITIAL_SIGNAL))
  })
})

describe('stepParams', () => {
  const from: OrbParams = { hue: 0.5, energy: 0, turbulence: 0 }

  it('converges to the target within about 2s of 60Hz steps', () => {
    const target = targetParams({ state: 'speaking', speaking: true })
    let p = from
    for (let i = 0; i < 120; i++) p = stepParams(p, target, 1 / 60)
    assert.ok(Math.abs(p.energy - target.energy) < 0.01)
    assert.ok(Math.abs(p.turbulence - target.turbulence) < 0.01)
    assert.ok(Math.abs(p.hue - target.hue) < 0.01)
  })

  it('returns current values when dt is 0', () => {
    assert.deepEqual(stepParams(from, { hue: 0.9, energy: 1, turbulence: 1 }, 0), from)
  })

  it('clamps a huge dt to 0.25s', () => {
    const target: OrbParams = { hue: 0.5, energy: 1, turbulence: 1 }
    assert.deepEqual(stepParams(from, target, 1000), stepParams(from, target, 0.25))
    assert.ok(stepParams(from, target, 1000).energy < 1)
  })

  it('treats NaN and negative dt as no movement and keeps outputs clamped', () => {
    const target: OrbParams = { hue: 0.9, energy: 1, turbulence: 1 }
    assert.deepEqual(stepParams(from, target, Number.NaN), from)
    assert.deepEqual(stepParams(from, target, -5), from)
    inRange(stepParams(from, { hue: 2, energy: 9, turbulence: -3 }, 0.25))
    inRange(stepParams({ hue: Number.NaN, energy: Number.NaN, turbulence: 2 }, target, 0.1))
  })

  it('wraps hue over the short path from 0.95 to 0.05', () => {
    const p = stepParams({ hue: 0.95, energy: 0, turbulence: 0 }, { hue: 0.05, energy: 0, turbulence: 0 }, 0.1)
    assert.ok(p.hue > 0.95 || p.hue < 0.05, `hue ${p.hue} took the long way`)
    inRange(p)
    let q: OrbParams = { hue: 0.95, energy: 0, turbulence: 0 }
    for (let i = 0; i < 120; i++) q = stepParams(q, { hue: 0.05, energy: 0, turbulence: 0 }, 1 / 60)
    assert.ok(Math.abs(q.hue - 0.05) < 0.01)
  })
})
