import type { ReactNode } from 'react'
import { Toast } from '@base-ui/react/toast'
import styles from './Notification.module.css'

export const NotificationProvider = Toast.Provider
export const useNotify = Toast.useToastManager

export function NotificationViewport() {
  const { toasts } = Toast.useToastManager()
  return (
    <Toast.Portal>
      <Toast.Viewport className={styles.viewport}>
        {toasts.map((toast) => (
          <Toast.Root key={toast.id} toast={toast} className={styles.toast}>
            <Toast.Title className={styles.title} />
            <Toast.Description className={styles.description} />
          </Toast.Root>
        ))}
      </Toast.Viewport>
    </Toast.Portal>
  )
}

export function withNotifications(children: ReactNode) {
  return (
    <NotificationProvider>
      {children}
      <NotificationViewport />
    </NotificationProvider>
  )
}
