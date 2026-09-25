import { NotificationProvider, NotificationViewport } from '../components/interaction/Notification'
import { CommandCenter } from '../features/command-center/CommandCenter'

/** Root composition: Command Center prototype (Phase 2). See src/app-overlay/ for the Desktop Overlay entry point. */
export function App() {
  return (
    <NotificationProvider>
      <CommandCenter />
      <NotificationViewport />
    </NotificationProvider>
  )
}
