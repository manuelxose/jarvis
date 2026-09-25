import styles from './MetricDisplay.module.css'

interface ProgressIndicatorProps {
  label: string
  percent: number | null // null = indeterminate
}

export function ProgressIndicator({ label, percent }: ProgressIndicatorProps) {
  return (
    <div>
      <div className={styles.row}>
        <span className={styles.label}>{label}</span>
        <span className={styles.value}>{percent === null ? '…' : `${Math.round(percent)}%`}</span>
      </div>
      <div
        className={styles.bar}
        role="progressbar"
        aria-label={label}
        aria-valuenow={percent ?? undefined}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <span
          className={styles.fill}
          style={percent === null ? { width: '40%', animation: 'none' } : { width: `${percent}%` }}
        />
      </div>
    </div>
  )
}
