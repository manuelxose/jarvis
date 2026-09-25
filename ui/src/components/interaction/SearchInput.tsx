import type { InputHTMLAttributes } from 'react'
import styles from './SearchInput.module.css'

interface SearchInputProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string
}

export function SearchInput({ label, className: _className, ...rest }: SearchInputProps) {
  return (
    <div className={styles.field}>
      <span aria-hidden="true" style={{ color: 'var(--text-disabled)', fontSize: 'var(--text-sm)' }}>
        ⌕
      </span>
      <input type="search" aria-label={label} className={styles.input} {...rest} />
    </div>
  )
}
