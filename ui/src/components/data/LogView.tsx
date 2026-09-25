import styles from './LogView.module.css'

export interface LogEntry {
  id: string
  timestamp: string
  source: string
  text: string
}

interface LogViewProps {
  entries: LogEntry[]
  /** Set only when entries genuinely stream in real time. Mock data must stay non-live (no fake "live" telemetry). */
  live?: boolean
}

export function LogView({ entries, live = false }: LogViewProps) {
  return (
    <div className={styles.log} role="log" aria-live={live ? 'polite' : 'off'}>
      {entries.map((entry) => (
        <div className={styles.line} key={entry.id}>
          <span className={styles.timestamp}>{entry.timestamp}</span>
          <span className={styles.source} data-role={entry.source.toLowerCase()}>
            {entry.source}
          </span>
          <span>{entry.text}</span>
        </div>
      ))}
    </div>
  )
}
