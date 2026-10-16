import { useEffect, useState } from 'react'
import type { AssistantState } from '../components/foundations/StatusIndicator'
import type { JarvisEvent, SystemMetrics } from '../protocol/events'
import { foldOrbSignal, INITIAL_SIGNAL } from '../components/jarvis/orb/orbModel'
import { getLaunchEndpoint } from '../services/endpoint'
import { createSseTransport } from '../services/sse-transport'
import { createDesktopTransport } from '../services/desktop-transport'
import { createVisualCoreService, type VisualCoreService, type VisualCoreSource } from '../services/visual-core-service'
import { createVisualCoreFeed } from '../services/visual-core-feed'

export type ConnectionStatus = 'connected' | 'connecting' | 'reconnecting' | 'disconnected' | 'auth-failure' | 'incompatible-version'

const STATUSES: readonly string[] = ['connected', 'connecting', 'reconnecting', 'disconnected', 'auth-failure', 'incompatible-version']
export interface MetricRow {
  label: string
  value: string
}
export interface JarvisEventsState {
  status: ConnectionStatus
  voiceState: AssistantState
  speaking: boolean
  metrics: MetricRow[] | null
}
export type JarvisEventsResult = JarvisEventsState & { core: VisualCoreSource }

function metricRows(event: SystemMetrics): MetricRow[] {
  const rows = [
    { label: 'CPU', value: `${event.cpu_percent.toFixed(0)}%` },
    { label: 'RAM', value: `${event.ram_percent.toFixed(0)}%` },
  ]
  const gpu = event.gpu?.util_percent
  if (typeof gpu === 'number') rows.push({ label: 'GPU', value: `${gpu.toFixed(0)}%` })
  return rows
}

function foldEvent(prev: JarvisEventsState, event: JarvisEvent): JarvisEventsState {
  const signal = foldOrbSignal({ state: prev.voiceState, speaking: prev.speaking }, event)
  return {
    ...prev,
    voiceState: signal.state,
    speaking: signal.speaking,
    metrics: event.name === 'system.metrics' ? metricRows(event) : prev.metrics,
  }
}

function applyStatus(prev: JarvisEventsState, status: ConnectionStatus): JarvisEventsState {
  if (prev.status === status) return prev
  const next = { ...prev, status }
  if (status !== 'connected') {
    next.voiceState = INITIAL_SIGNAL.state
    next.speaking = INITIAL_SIGNAL.speaking
  }
  return next
}

/** Live connection state, assistant state and system metrics: Electron Core bridge when present, else the browser SSE stream. */
export function useJarvisEvents(): JarvisEventsResult {
  const [state, setState] = useState<JarvisEventsState>(() => ({
    status: window.jarvisDesktop?.core ? 'connecting' : getLaunchEndpoint() ? 'connecting' : 'disconnected',
    voiceState: INITIAL_SIGNAL.state,
    speaking: INITIAL_SIGNAL.speaking,
    metrics: null,
  }))

  const [visualCore, setVisualCore] = useState<VisualCoreService>(() => createVisualCoreService())

  useEffect(() => {
    // StrictMode re-runs effects after cleanup disposed the service; never reuse a disposed one.
    const service = visualCore.getDiagnostics().disposed ? createVisualCoreService() : visualCore
    if (service !== visualCore) setVisualCore(service)
    const feed = createVisualCoreFeed(service)
    const core = window.jarvisDesktop?.core
    if (core) {
      const transport = createDesktopTransport(core)
      let active = true
      let lastStatus = ''
      const accept = (status: unknown) => {
        if (active && typeof status === 'string' && STATUSES.includes(status)) {
          if (status !== lastStatus) {
            feed.setStatus(status)
            if (status === 'connected') void feed.resync(core.getReplay?.() ?? null)
            lastStatus = status
          }
          setState((prev) => applyStatus(prev, status as ConnectionStatus))
        }
      }
      const offState = transport.onState(accept)
      const offEvent = transport.subscribe((event) => {
        feed.accept(event)
        // Events are only trusted while the bridge reports connected.
        setState((prev) => (prev.status === 'connected' ? foldEvent(prev, event) : prev))
      })
      void transport.getState().then(accept).catch(() => accept('disconnected'))
      return () => {
        active = false
        offState()
        offEvent()
        feed.dispose()
      }
    }

    const target = getLaunchEndpoint()
    if (!target) {
      feed.dispose()
      return
    }
    const transport = createSseTransport(target.url, target.token)
    const started = Date.now()
    let hasConnected = false
    let lastFeedStatus = ''
    const setFeedStatus = (status: string) => {
      if (status === lastFeedStatus) return
      lastFeedStatus = status
      feed.setStatus(status)
    }

    const unsubscribe = transport.subscribe((event: JarvisEvent) => {
      setFeedStatus('connected')
      feed.accept(event)
      setState((prev) => foldEvent(applyStatus(prev, 'connected'), event))
    })
    const poll = setInterval(() => {
      if (transport.connected) hasConnected = true
      const status = transport.connected ? 'connected' : hasConnected || Date.now() - started >= 3000 ? 'disconnected' : 'connecting'
      setFeedStatus(status)
      setState((prev) => applyStatus(prev, status))
    }, 500)

    return () => {
      clearInterval(poll)
      unsubscribe()
      transport.close()
      feed.dispose()
    }
  }, [])

  return { ...state, core: visualCore }
}
