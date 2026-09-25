import styles from './MetricDisplay.module.css'

interface MetricDisplayProps {
  label: string
  value: string
  /** 0-100. Omit for a metric with no meaningful bar (e.g. a byte count). */
  percent?: number
  tone?: 'default' | 'warning' | 'danger'
}

export function MetricDisplay({ label, value, percent, tone = 'default' }: MetricDisplayProps) {
  return (
    <div>
      <div className={styles.row}>
        <span className={styles.label}>{label}</span>
        <span className={styles.value}>{value}</span>
      </div>
      {percent !== undefined && (
        <div
          className={styles.bar}
          role="progressbar"
          aria-label={label}
          aria-valuenow={Math.round(percent)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <span className={styles.fill} data-tone={tone === 'default' ? undefined : tone} style={{ width: `${Math.min(100, Math.max(0, percent))}%` }} />
        </div>
      )}
    </div>
  )
}
