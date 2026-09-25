import type { ReactNode } from 'react'

interface EmptyStateProps {
  title: string
  description?: string
  action?: ReactNode
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 'var(--space-2)', padding: 'var(--space-8)', textAlign: 'center', color: 'var(--text-secondary)' }}>
      <div style={{ width: 40, height: 40, borderRadius: '50%', border: '1px dashed var(--border-strong)' }} aria-hidden="true" />
      <p style={{ margin: 0, color: 'var(--text-primary)', fontWeight: 'var(--weight-medium)' }}>{title}</p>
      {description && <p style={{ margin: 0, fontSize: 'var(--text-sm)' }}>{description}</p>}
      {action}
    </div>
  )
}
