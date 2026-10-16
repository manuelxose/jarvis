import type { VisualCoreSource } from '../../../services/visual-core-service.ts'
import { STATE_SPECS } from './visualCoreGeometry.ts'
import type { VisualState } from './visualCoreMachine.ts'

/** Fixture-only static source for the browser review harness and visual specs; never use when a bridge or endpoint exists. */
export function fakeSource(state: VisualState): VisualCoreSource {
  const snapshot = {
    state,
    label: STATE_SPECS[state].label,
    detail: null,
    connection: state === 'DISCONNECTED' ? ('disconnected' as const) : ('connected' as const),
    awaitingSnapshot: false,
    reason: null,
    partial: false,
    startup: 'idle' as const,
    apps: null,
  }
  const output = state === 'SPEAKING' || state === 'STARTING' ? 0.5 : 0
  const signals = { output, peak: output, microphone: 0 }
  return { getSnapshot: () => snapshot, subscribe: () => () => {}, readSignals: () => signals }
}

export const STARTUP_SCENARIOS = ['startup-full', 'startup-reduced', 'startup-fast', 'startup-muted', 'startup-interrupted'] as const
export type StartupScenario = (typeof STARTUP_SCENARIOS)[number]

/** Fixture-only startup sources for the review harness; never used in production paths. */
export function startupSource(scenario: StartupScenario): VisualCoreSource {
  const interrupted = scenario === 'startup-interrupted'
  const state: VisualState = interrupted ? 'STOPPING' : 'STARTING'
  const snapshot = {
    state,
    label: STATE_SPECS[state].label,
    detail: interrupted ? null : 'Welcome',
    connection: 'connected' as const,
    awaitingSnapshot: false,
    reason: null,
    partial: false,
    startup: interrupted ? ('cancelled' as const) : ('announcing' as const),
    apps: interrupted ? null : { ready: 2, failed: 0, pending: 0, total: 2 },
  }
  const output = scenario === 'startup-full' || scenario === 'startup-reduced' || scenario === 'startup-fast' ? 0.5 : 0
  const signals = { output, peak: output, microphone: 0 }
  return { getSnapshot: () => snapshot, subscribe: () => () => {}, readSignals: () => signals }
}
