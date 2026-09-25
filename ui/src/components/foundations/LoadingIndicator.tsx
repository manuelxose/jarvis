import { cx } from '../../lib/cx'
import styles from './LoadingIndicator.module.css'

interface SpinnerProps {
  label?: string
  className?: string
}

export function Spinner({ label = 'Loading', className }: SpinnerProps) {
  return <span role="status" aria-label={label} className={cx(styles.spinner, className)} />
}

interface SkeletonProps {
  width?: string | number
  className?: string
}

export function Skeleton({ width = '100%', className }: SkeletonProps) {
  return <span aria-hidden="true" className={cx(styles.skeleton, className)} style={{ display: 'block', width }} />
}
