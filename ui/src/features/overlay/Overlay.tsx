import { useState } from 'react'
import { FloatingContainer } from '../../components/layout/FloatingContainer'
import { VisualCorePlaceholder } from '../../components/jarvis/VisualCorePlaceholder'
import { StatusIndicator } from '../../components/foundations/StatusIndicator'
import { ConversationMessage } from '../../components/jarvis/ConversationMessage'
import { Button } from '../../components/foundations/Button'
import { EmptyState } from '../../components/data/EmptyState'
import { mockConversation } from '../../mocks/fixtures'
import styles from './Overlay.module.css'

export type OverlayMode = 'compact-idle' | 'listening' | 'speaking' | 'expanded' | 'notification' | 'processing' | 'error' | 'disconnected'

const MODES: OverlayMode[] = ['compact-idle', 'listening', 'speaking', 'processing', 'expanded', 'notification', 'error', 'disconnected']

function OverlayCard({ mode }: { mode: OverlayMode }) {
  switch (mode) {
    case 'compact-idle':
      return (
        <FloatingContainer className={styles.compact}>
          <VisualCorePlaceholder state="idle" size={22} />
          <StatusIndicator state="idle" />
        </FloatingContainer>
      )
    case 'listening':
      return (
        <FloatingContainer className={styles.compact}>
          <VisualCorePlaceholder state="listening" size={22} />
          <StatusIndicator state="listening" />
        </FloatingContainer>
      )
    case 'speaking':
      return (
        <FloatingContainer style={{ width: 320 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
            <VisualCorePlaceholder state="speaking" size={28} />
            <StatusIndicator state="speaking" />
          </div>
          <p className={styles.caption}>Oxlint is clean — zero warnings. Starting the visual regression run now.</p>
        </FloatingContainer>
      )
    case 'processing':
      return (
        <FloatingContainer className={styles.compact}>
          <VisualCorePlaceholder state="processing" size={22} />
          <StatusIndicator state="processing" label="Working on it" />
        </FloatingContainer>
      )
    case 'expanded':
      return (
        <FloatingContainer className={styles.expanded}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', marginBottom: 'var(--space-3)' }}>
            <VisualCorePlaceholder state="listening" size={28} />
            <StatusIndicator state="listening" />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)', maxHeight: 260, overflowY: 'auto' }} tabIndex={0}>
            {mockConversation.map((m) => (
              <ConversationMessage key={m.id} message={m} />
            ))}
          </div>
        </FloatingContainer>
      )
    case 'notification':
      return (
        <FloatingContainer style={{ width: 320 }}>
          <StatusIndicator state="notification" label="Task finished" />
          <p className={styles.caption}>gsd-exec completed T04 replan-slice on milestone/M008.</p>
        </FloatingContainer>
      )
    case 'error':
      return (
        <FloatingContainer style={{ width: 320 }}>
          <StatusIndicator state="error" />
          <p className={styles.caption}>hermes-child lost the Ollama connection. Retrying in 8s — say "cancel" to stop.</p>
        </FloatingContainer>
      )
    case 'disconnected':
      return (
        <FloatingContainer style={{ width: 320 }}>
          <EmptyState title="Jarvis is offline" description="No transport connected. Check the daemon and try again." />
        </FloatingContainer>
      )
  }
}

/** Desktop Overlay prototype. The state strip at the bottom-left is a demo control for this design review — the production overlay has no chrome of its own. */
export function Overlay() {
  const [mode, setMode] = useState<OverlayMode>('listening')
  return (
    <div className={styles.desktop} data-overlay-root>
      <OverlayCard mode={mode} />
      <div className={styles.controls} role="group" aria-label="Simulate overlay state">
        {MODES.map((m) => (
          <Button key={m} variant={mode === m ? 'primary' : 'default'} aria-pressed={mode === m} onClick={() => setMode(m)}>
            {m}
          </Button>
        ))}
      </div>
    </div>
  )
}
