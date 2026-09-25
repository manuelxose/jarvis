import { cx } from '../../lib/cx'

interface DividerProps {
  orientation?: 'horizontal' | 'vertical'
  className?: string
}

export function Divider({ orientation = 'horizontal', className }: DividerProps) {
  return (
    <hr
      role="separator"
      aria-orientation={orientation}
      className={cx(className)}
      style={{
        border: 'none',
        margin: 0,
        background: 'var(--border-subtle)',
        ...(orientation === 'horizontal' ? { height: 1, width: '100%' } : { width: 1, alignSelf: 'stretch' }),
      }}
    />
  )
}
