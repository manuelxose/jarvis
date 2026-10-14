/**
 * Renderer-side adapter over the Electron preload `jarvisDesktop.core` bridge.
 * The Core token never reaches the renderer; the main process owns the connection.
 *
 * Erasable TypeScript only, so Node can run this file directly (node-tests/).
 */
import { JARVIS_EVENT_NAMES } from '../protocol/events.ts'
import type { JarvisEvent } from '../protocol/events.ts'
import type { CoreConnectionState, JarvisCoreBridge } from '../shell/jarvis-desktop'

const KNOWN_NAMES: ReadonlySet<string> = new Set(JARVIS_EVENT_NAMES)

export interface DesktopTransport {
  subscribe(listener: (event: JarvisEvent) => void): () => void
  onState(listener: (state: CoreConnectionState) => void): () => void
  getState(): Promise<CoreConnectionState | null>
}

export function createDesktopTransport(core: JarvisCoreBridge): DesktopTransport {
  return {
    subscribe(listener) {
      return core.onEvent((event) => {
        if (!event || event.v !== 1 || !KNOWN_NAMES.has(event.name)) return
        try {
          listener(event)
        } catch {
          // one failing listener must not break the bridge subscription
        }
      })
    },
    onState: (listener) => core.onState(listener),
    getState: () => core.getState(),
  }
}

export async function sendDesktopControl(core: JarvisCoreBridge, action: 'activate' | 'sleep'): Promise<void> {
  if (action !== 'activate' && action !== 'sleep') throw new Error('Control is unavailable.')
  let result: Awaited<ReturnType<JarvisCoreBridge['control']>>
  try {
    result = await core.control(action)
  } catch {
    throw new Error('Control connection failed.')
  }
  if (!result || result.ok !== true) {
    const code = result && result.ok === false && typeof result.error === 'string' ? result.error : 'rejected'
    throw new Error(`Control request denied (${code}).`)
  }
}
