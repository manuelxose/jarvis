import type { ButtonHTMLAttributes } from 'react'
import { cx } from '../../lib/cx'
import styles from './IconButton.module.css'

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Required — an icon-only control must have an accessible name. */
  'aria-label': string
}

export function IconButton({ className, ...rest }: IconButtonProps) {
  return <button className={cx(styles.iconButton, className)} {...rest} />
}
