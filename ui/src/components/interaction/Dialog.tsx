import type { ReactElement, ReactNode } from 'react'
import { Dialog as BaseDialog } from '@base-ui/react/dialog'
import styles from './Dialog.module.css'

interface DialogProps {
  /** A single focusable element — Base UI merges its own props into it rather than wrapping it. */
  trigger: ReactElement
  title: string
  description?: string
  children?: ReactNode
  actions?: ReactNode
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

/** Confirmation / detail dialog — focus-trapped, Esc-dismissable, labelled by title. */
export function Dialog({ trigger, title, description, children, actions, open, onOpenChange }: DialogProps) {
  return (
    <BaseDialog.Root open={open} onOpenChange={onOpenChange}>
      <BaseDialog.Trigger render={trigger} />
      <BaseDialog.Portal>
        <BaseDialog.Backdrop className={styles.backdrop} />
        <BaseDialog.Popup className={styles.popup}>
          <BaseDialog.Title className={styles.title}>{title}</BaseDialog.Title>
          {description && <BaseDialog.Description className={styles.description}>{description}</BaseDialog.Description>}
          {children}
          {actions && <div className={styles.actions}>{actions}</div>}
        </BaseDialog.Popup>
      </BaseDialog.Portal>
    </BaseDialog.Root>
  )
}
