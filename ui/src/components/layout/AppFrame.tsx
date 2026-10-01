import type { ReactNode } from 'react'
import styles from './AppFrame.module.css'

interface AppFrameProps {
  topbar: ReactNode
  sidebar: ReactNode
  right?: ReactNode
  /** Announced politely to assistive tech whenever it changes (connection / assistant state). */
  liveMessage?: string
  children: ReactNode
}

/** The Command Center grid shell: topbar spanning full width, sidebar nav, center workspace, optional right rail. */
export function AppFrame({ topbar, sidebar, right, liveMessage, children }: AppFrameProps) {
  return (
    <div className={styles.frame}>
      <a className={styles.skipLink} href="#main">
        Skip to main content
      </a>
      <div className={styles.srOnly} role="status" aria-live="polite" data-testid="live-status">
        {liveMessage}
      </div>
      <header className={styles.topbar}>{topbar}</header>
      <nav className={styles.sidebar} aria-label="Workspaces" tabIndex={-1}>
        {sidebar}
      </nav>
      <main id="main" className={styles.center} tabIndex={-1}>
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
