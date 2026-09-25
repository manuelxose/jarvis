import type { HTMLAttributes } from 'react'
import { cx } from '../../lib/cx'
import styles from './Badge.module.css'

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'info'
}

export function Badge({ tone = 'neutral', className, ...rest }: BadgeProps) {
  return <span className={cx(styles.badge, tone !== 'neutral' && styles[tone], className)} {...rest} />
}
