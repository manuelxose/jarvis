import { cx } from '../../lib/cx'
import styles from './StatusIndicator.module.css'

export type AssistantState =
  | 'idle'
  | 'listening'
  | 'speaking'
  | 'processing'
  | 'executing'
  | 'error'
  | 'offline'
  | 'notification'

const COLOR_VAR: Record<AssistantState, string> = {
  idle: 'var(--text-secondary)',
  listening: 'var(--accent-listening)',
  speaking: 'var(--accent-speaking)',
  processing: 'var(--accent-processing)',
  executing: 'var(--accent-processing)',
  error: 'var(--state-danger)',
  offline: 'var(--text-secondary)',
  notification: 'var(--accent-listening)',
}

const LABEL: Record<AssistantState, string> = {
  idle: 'Idle',
  listening: 'Listening',
  speaking: 'Speaking',
  processing: 'Processing',
  executing: 'Executing',
  error: 'Error',
  offline: 'Offline',
  notification: 'Notification',
}

interface StatusIndicatorProps {
  state: AssistantState
  /** Override the default label (e.g. append detail). Shape+label are always both present — never color alone. */
  label?: string
  className?: string
}

/** Communicates assistant/system state via shape + label, not color alone (WCAG 1.4.1). */
export function StatusIndicator({ state, label, className }: StatusIndicatorProps) {
  return (
    <span className={cx(styles.root, className)} style={{ color: COLOR_VAR[state] }} role="status">
      <span className={cx(styles.shape, styles[state])} aria-hidden="true" />
      {label ?? LABEL[state]}
    </span>
  )
}
