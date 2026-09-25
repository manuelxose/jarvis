import type { HTMLAttributes } from 'react'
import { cx } from '../../lib/cx'
import styles from './FloatingContainer.module.css'

/** Elevated surface for overlay cards, toasts, and other content that floats above the desktop. */
export function FloatingContainer({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cx(styles.floating, className)} {...rest} />
}
