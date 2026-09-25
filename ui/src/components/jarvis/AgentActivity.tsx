import type { AssistantState } from '../foundations/StatusIndicator'
import { StatusIndicator } from '../foundations/StatusIndicator'
import styles from './AgentActivity.module.css'

export interface AgentActivityEntry {
  id: string
  name: string
  detail: string
  state: AssistantState
}

export function AgentActivityList({ entries }: { entries: AgentActivityEntry[] }) {
  if (entries.length === 0) {
    return <p style={{ color: 'var(--text-secondary)', fontSize: 'var(--text-sm)' }}>No agents running.</p>
  }
  return (
    <div>
      {entries.map((entry) => (
        <div className={styles.row} key={entry.id}>
          <StatusIndicator state={entry.state} label=" " />
          <span className={styles.name}>{entry.name}</span>
          <span className={styles.detail} title={entry.detail}>
            {entry.detail}
          </span>
        </div>
      ))}
    </div>
  )
}
