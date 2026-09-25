import { cx } from '../../lib/cx'
import type { AssistantState } from '../foundations/StatusIndicator'
import styles from './VisualCorePlaceholder.module.css'

const CORE_COLOR: Partial<Record<AssistantState, string>> = {
  listening: 'var(--accent-listening)',
  speaking: 'var(--accent-speaking)',
  processing: 'var(--accent-processing)',
  executing: 'var(--accent-processing)',
  error: 'var(--state-danger)',
  offline: 'var(--state-offline)',
}

interface VisualCorePlaceholderProps {
  state: AssistantState
  size?: number
}

/**
 * Reserved slot for the future audio-reactive holographic orb (out of scope
 * this phase, see docs/design/component-inventory.md). Deliberately a flat
 * CSS blob + glow, not WebGL — swappable for a <canvas>/R3F element later
 * without a layout change, per the future-capabilities boundary.
 */
export function VisualCorePlaceholder({ state, size = 120 }: VisualCorePlaceholderProps) {
  const color = CORE_COLOR[state] ?? 'var(--text-disabled)'
  const animated = state === 'listening' || state === 'speaking' || state === 'processing'
  return (
    <div
      className={cx(styles.core, animated && styles.pulse)}
      style={{ width: size, height: size, ['--core-color' as string]: color }}
      role="img"
      aria-label={`Jarvis visual core — ${state}`}
    />
  )
}
