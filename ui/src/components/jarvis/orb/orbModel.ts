import type { JarvisEvent } from '../../../protocol/events'
import type { AssistantState } from '../../foundations/StatusIndicator'

export interface OrbSignal {
  state: AssistantState
  speaking: boolean
}

export interface OrbParams {
  hue: number
  energy: number
  turbulence: number
}

export const INITIAL_SIGNAL: OrbSignal = { state: 'idle', speaking: false }

export const STATES: ReadonlySet<string> = new Set<AssistantState>([
  'idle', 'listening', 'speaking', 'processing', 'executing', 'error', 'offline', 'notification',
])

const CLEARS_SPEAKING: ReadonlySet<string> = new Set(['idle', 'offline', 'error'])

export function foldOrbSignal(prev: OrbSignal, event: JarvisEvent): OrbSignal {
  if (event.name === 'voice.state') {
    if (!STATES.has(event.state)) return prev
    const state = event.state as AssistantState
    const speaking = CLEARS_SPEAKING.has(state) ? false : prev.speaking
    return state === prev.state && speaking === prev.speaking ? prev : { state, speaking }
  }
  if (event.name === 'speech.started') return prev.speaking ? prev : { ...prev, speaking: true }
  if (event.name === 'speech.completed') return prev.speaking ? { ...prev, speaking: false } : prev
  return prev
}

// Hues (0..1) match tokens.css: listening #4fd1ff, speaking #a78bfa, processing #ffb86b, danger #ff6b81.
const HUE_LISTENING = 0.54
const HUE_SPEAKING = 0.71
const HUE_PROCESSING = 0.09
const HUE_EXECUTING = 0.12
const HUE_DANGER = 0.97

const TARGETS: Record<AssistantState, OrbParams> = {
  idle: { hue: HUE_LISTENING, energy: 0.2, turbulence: 0.12 },
  notification: { hue: HUE_LISTENING, energy: 0.4, turbulence: 0.25 },
  listening: { hue: HUE_LISTENING, energy: 0.55, turbulence: 0.3 },
  speaking: { hue: HUE_SPEAKING, energy: 1, turbulence: 0.8 },
  processing: { hue: HUE_PROCESSING, energy: 0.6, turbulence: 0.7 },
  executing: { hue: HUE_EXECUTING, energy: 0.7, turbulence: 0.85 },
  error: { hue: HUE_DANGER, energy: 0.65, turbulence: 0.5 },
  offline: { hue: 0.6, energy: 0.08, turbulence: 0.04 },
}

export function targetParams(signal: OrbSignal): OrbParams {
  const t = TARGETS[signal.speaking ? 'speaking' : signal.state] ?? TARGETS.idle
  return { ...t }
}

const clamp01 = (v: number): number => (Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 0)

export function stepParams(current: OrbParams, target: OrbParams, dtSeconds: number, rate = 6): OrbParams {
  const dt = Number.isFinite(dtSeconds) ? Math.min(0.25, Math.max(0, dtSeconds)) : 0
  const k = 1 - Math.exp(-rate * dt)
  let dh = target.hue - current.hue
  dh -= Math.round(dh)
  const hue = current.hue + dh * k
  return {
    hue: clamp01(hue - Math.floor(hue)),
    energy: clamp01(current.energy + (target.energy - current.energy) * k),
    turbulence: clamp01(current.turbulence + (target.turbulence - current.turbulence) * k),
  }
}
