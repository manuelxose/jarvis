import { cx } from '../../lib/cx'
import styles from './ConversationMessage.module.css'

export interface ConversationMessageData {
  id: string
  role: 'you' | 'jarvis' | 'tool'
  author: string
  timestamp: string
  text: string
}

export function ConversationMessage({ message }: { message: ConversationMessageData }) {
  return (
    <div className={cx(styles.message, message.role === 'you' && styles.user)}>
      <div className={styles.meta}>
        {message.author} · {message.timestamp}
      </div>
      {message.text}
    </div>
  )
}
