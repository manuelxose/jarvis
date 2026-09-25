import styles from './Timeline.module.css'

export interface TimelineEvent {
  id: string
  time: string
  text: string
  tone?: 'default' | 'warning' | 'danger'
}

export function Timeline({ events }: { events: TimelineEvent[] }) {
  return (
    <ol className={styles.list}>
      {events.map((event) => (
        <li key={event.id} className={styles.item} data-tone={event.tone === 'default' ? undefined : event.tone}>
          <div className={styles.time}>{event.time}</div>
          <div className={styles.text}>{event.text}</div>
        </li>
      ))}
    </ol>
  )
}
