import type { ReactNode } from 'react'
import { StatusIndicator, type AssistantState } from '../foundations/StatusIndicator'
import { Panel } from '../layout/Panel'

interface StatusCardProps {
  title: string
  state: AssistantState
  description?: string
  action?: ReactNode
}

export function StatusCard({ title, state, description, action }: StatusCardProps) {
  return (
    <Panel>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <StatusIndicator state={state} />
          <h4 style={{ margin: '6px 0 2px', fontSize: 'var(--text-md)', fontWeight: 'var(--weight-semibold)' }}>{title}</h4>
          {description && <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: 'var(--text-sm)' }}>{description}</p>}
        </div>
        {action}
      </div>
    </Panel>
  )
}
