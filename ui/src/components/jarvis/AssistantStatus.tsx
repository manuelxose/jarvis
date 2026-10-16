import type { AssistantState } from '../foundations/StatusIndicator'
import type { VisualCoreSource } from '../../services/visual-core-service'
import { VisualCore } from './visual-core/VisualCore'
import { VisualCorePlaceholder } from './VisualCorePlaceholder'

interface AssistantStatusProps {
  state: AssistantState
  headline: string
  detail?: string
  compact?: boolean
  core?: VisualCoreSource
}

export function AssistantStatus({ state, headline, detail, compact = false, core }: AssistantStatusProps) {
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
      <VisualCore source={core} variant="command-center" />
      <div>
        <h2 style={{ margin: '0 0 4px', fontSize: 'var(--text-xl)' }}>{headline}</h2>
        {detail && <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: 'var(--text-sm)' }}>{detail}</p>}
      </div>
    </div>
  )
}
