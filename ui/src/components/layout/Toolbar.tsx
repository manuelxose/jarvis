import type { HTMLAttributes } from 'react'
import { cx } from '../../lib/cx'

export function Toolbar({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      role="toolbar"
      className={cx(className)}
      style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginBottom: 'var(--space-4)' }}
      {...rest}
    />
  )
}
