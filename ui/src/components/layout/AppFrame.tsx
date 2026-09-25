import type { ReactNode } from 'react'
import styles from './AppFrame.module.css'

interface AppFrameProps {
  topbar: ReactNode
  sidebar: ReactNode
  right?: ReactNode
  children: ReactNode
}

/** The Command Center grid shell: topbar spanning full width, sidebar nav, center workspace, optional right rail. */
export function AppFrame({ topbar, sidebar, right, children }: AppFrameProps) {
  return (
    <div className={styles.frame}>
      <header className={styles.topbar}>{topbar}</header>
      <nav className={styles.sidebar} aria-label="Primary navigation" tabIndex={0}>
        {sidebar}
      </nav>
      <main className={styles.center} tabIndex={0}>
        {children}
      </main>
      {right && (
        <aside className={styles.right} aria-label="System and activity" tabIndex={0}>
          {right}
        </aside>
      )}
    </div>
  )
}
