import type { ReactNode } from 'react'
import { StatusIndicator, type AssistantState } from '../foundations/StatusIndicator'

interface TopBarProps {
  title: string
  state: AssistantState
  actions?: ReactNode
}

export function TopBar({ title, state, actions }: TopBarProps) {
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', fontWeight: 'var(--weight-semibold)', fontSize: 'var(--text-sm)', letterSpacing: 'var(--tracking-wide)' }}>
        <span
          aria-hidden="true"
          style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--accent-primary)', boxShadow: '0 0 8px var(--accent-primary)' }}
        />
        {title}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-4)' }}>
        <StatusIndicator state={state} />
        {actions}
      </div>
    </>
  )
}
