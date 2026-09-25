import type { AssistantState } from '../foundations/StatusIndicator'
import { StatusIndicator } from '../foundations/StatusIndicator'
import { VisualCorePlaceholder } from './VisualCorePlaceholder'

interface AssistantStatusProps {
  state: AssistantState
  headline: string
  detail?: string
  compact?: boolean
}

export function AssistantStatus({ state, headline, detail, compact = false }: AssistantStatusProps) {
  if (compact) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
        <VisualCorePlaceholder state={state} size={22} />
        <span style={{ fontSize: 'var(--text-sm)' }}>{headline}</span>
      </div>
    )
  }
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-5)' }}>
      <VisualCorePlaceholder state={state} size={120} />
      <div>
        <StatusIndicator state={state} />
        <h2 style={{ margin: '6px 0 4px', fontSize: 'var(--text-xl)' }}>{headline}</h2>
        {detail && <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: 'var(--text-sm)' }}>{detail}</p>}
      </div>
    </div>
  )
}
