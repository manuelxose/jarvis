/**
 * SSE implementation of JarvisEventTransport: consumes the daemon's
 * token-protected `GET /events?token=...` stream on 127.0.0.1. The endpoint
 * and token come from the daemon control command `ui`. Frames that are not
 * valid JSON, have the wrong version, or carry an unknown name are dropped.
 *
 * Erasable TypeScript only, so Node can run this file directly (node-tests/).
 */
import { JARVIS_EVENT_NAMES } from '../protocol/events.ts'
import type { JarvisEvent, JarvisEventTransport } from '../protocol/events.ts'

const KNOWN_NAMES: ReadonlySet<string> = new Set(JARVIS_EVENT_NAMES)

export function createSseTransport(
  url: string,
  token: string,
  EventSourceImpl: typeof EventSource = globalThis.EventSource,
): JarvisEventTransport & { close(): void } {
  const listeners = new Set<(event: JarvisEvent) => void>()
  const source = new EventSourceImpl(`${url}?token=${encodeURIComponent(token)}`)
  let connected = false

  source.onopen = () => {
    connected = true
  }
  source.onerror = () => {
    connected = false
  }
  source.onmessage = (message: MessageEvent) => {
    let data: unknown
    try {
      data = JSON.parse(String(message.data))
    } catch {
      return
    }
    if (typeof data !== 'object' || data === null) return
    const { v, name } = data as { v?: unknown; name?: unknown }
    if (v !== 1 || typeof name !== 'string' || !KNOWN_NAMES.has(name)) return
    for (const listener of [...listeners]) {
      try {
        listener(data as JarvisEvent)
      } catch {
        // one failing listener must not starve the others
      }
    }
  }

  return {
    get connected() {
      return connected
    },
    subscribe(listener) {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
    close() {
      connected = false
      source.close()
    },
  }
}
