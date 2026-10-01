import { Dialog } from '@base-ui/react/dialog'
import { CONTROL_LABELS, type ControlAction } from '../../protocol/commands'
import styles from './ConfirmationCard.module.css'

interface ConfirmationCardProps {
  action: ControlAction | null
  pending: boolean
  available: boolean
  error: string | null
  onCancel: () => void
  onConfirm: () => void
}

/** The confirmation is distinct from ToolGateway's permission gate. */
export function ConfirmationCard({ action, pending, available, error, onCancel, onConfirm }: ConfirmationCardProps) {
  const copy = action ? CONTROL_LABELS[action] : null
  return (
    <Dialog.Root open={!!action} onOpenChange={(open) => { if (!open && !pending) onCancel() }}>
      <Dialog.Portal>
        <Dialog.Backdrop className={styles.backdrop} />
        <Dialog.Popup className={styles.card}>
          <div className={styles.eyebrow}>DAEMON CONTROL · CONFIRMATION</div>
          <Dialog.Title className={styles.title}>{copy?.title}</Dialog.Title>
          <Dialog.Description className={styles.description}>{copy?.consequence} This will send a live request to the daemon.</Dialog.Description>
          {!available && <p className={styles.error} role="status">Daemon is offline. No request can be sent.</p>}
          {error && <p className={styles.error} role="status">{error}</p>}
          {pending && <p className={styles.pending} role="status" aria-live="polite">Sending request…</p>}
          <div className={styles.actions}>
            <button type="button" className={styles.cancel} onClick={onCancel} disabled={pending}>Cancel</button>
            <button type="button" className={styles.confirm} onClick={onConfirm} disabled={pending || !action || !available}>
              {pending ? 'Sending…' : `Confirm ${action === 'sleep' ? 'Sleep' : 'Activate'}`}
            </button>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
