import styles from './ConversationMessage.module.css'
import { Skeleton } from '../foundations/LoadingIndicator'

/** Shown while a Jarvis response is streaming in but no tokens have rendered yet. Not live text — real streaming replaces this node, it never fakes partial content. */
export function StreamingResponsePlaceholder() {
  return (
    <div className={styles.message} aria-busy="true" aria-label="Jarvis is responding">
      <div className={styles.meta}>Jarvis · streaming…</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <Skeleton width="92%" />
        <Skeleton width="68%" />
      </div>
    </div>
  )
}
