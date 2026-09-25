import type { ButtonHTMLAttributes } from 'react'
import { cx } from '../../lib/cx'
import styles from './Button.module.css'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'default' | 'primary' | 'danger'
}

export function Button({ variant = 'default', className, ...rest }: ButtonProps) {
  return (
    <button
      className={cx(styles.button, variant === 'primary' && styles.primary, variant === 'danger' && styles.danger, className)}
      {...rest}
    />
  )
}
