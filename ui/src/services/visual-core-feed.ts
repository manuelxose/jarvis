import type { Connection, NormalizedEvent, NormalizedSnapshot } from '../components/jarvis/visual-core/visualCoreMachine.ts'
import { normalizeCoreEvent } from './core-signal-normalizer.ts'
import type { VisualCoreService } from './visual-core-service.ts'

const MAX_BUFFER = 512
type Feed = NormalizedEvent | NormalizedSnapshot

export function toMachineConnection(status: string): Connection {
  switch (status) {
    case 'connected': return 'connected'
    case 'connecting':
    case 'reconnecting': return 'connecting'
    case 'auth-failure': return 'auth-error'
    case 'incompatible-version': return 'incompatible'
    default: return 'disconnected'
  }
}

export interface VisualCoreFeed {
  accept(event: unknown): void
  resync(replay: Promise<unknown[]> | unknown[] | null): Promise<void>
  setStatus(status: string): void
  dispose(): void
}

/** Adapts one window's transport (live events, status, optional replay) onto a VisualCoreService. */
export function createVisualCoreFeed(service: VisualCoreService): VisualCoreFeed {
  let disposed = false
  let resyncing = false
  let epoch = 0
  let buffer: Feed[] = []
  let lastSession: string | null = null
  let lastSeq = -1

  const covered = (input: Feed): boolean => {
    if (input.session !== lastSession) return false
    return input.kind === 'snapshot' ? input.watermark < lastSeq : 'seq' in input && input.seq <= lastSeq
  }

  const ingest = (input: Feed) => {
    if (covered(input)) return
    if (!service.ingest(input)) return
    lastSession = input.session
    lastSeq = input.kind === 'snapshot' ? input.watermark : input.seq
  }

  const forget = () => { buffer = []; lastSession = null; lastSeq = -1 }

  return {
    accept(event) {
      if (disposed) return
      const input = normalizeCoreEvent(event)
      if (!input || input.kind === 'connection' || input.kind === 'tick') return
      if (!resyncing) return ingest(input)
      if (buffer.length >= MAX_BUFFER) buffer = []
      else buffer.push(input)
    },
    async resync(replay) {
      if (disposed) return
      const mine = ++epoch
      resyncing = true
      let items: unknown[] = []
      try { items = (await replay) ?? [] } catch { items = [] }
      if (disposed || mine !== epoch) return
      if (Array.isArray(items)) {
        for (const item of items) {
          const input = normalizeCoreEvent(item)
          if (input && input.kind !== 'connection' && input.kind !== 'tick') ingest(input)
        }
      }
      const pending = buffer
      buffer = []
      resyncing = false
      for (const input of pending) ingest(input)
    },
    setStatus(status) {
      if (disposed) return
      const connection = toMachineConnection(status)
      service.setConnection(connection)
      if (connection !== 'connected') {
        epoch++
        resyncing = false
        forget()
      }
    },
    dispose() {
      if (disposed) return
      disposed = true
      buffer = []
      service.dispose()
    },
  }
}
