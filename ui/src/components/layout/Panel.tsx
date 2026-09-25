import type { HTMLAttributes, ReactNode } from 'react'
import { cx } from '../../lib/cx'
import styles from './Panel.module.css'

interface PanelProps extends HTMLAttributes<HTMLElement> {
  title?: string
  action?: ReactNode
}

export function Panel({ title, action, children, className, ...rest }: PanelProps) {
  return (
    <section className={cx(styles.panel, className)} aria-label={title} {...rest}>
      {title && (
        <div className={styles.header}>
          <h3 className={styles.title}>{title}</h3>
          {action}
        </div>
      )}
      {children}
    </section>
  )
}
