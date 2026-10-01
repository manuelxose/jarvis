import { useEffect, useState } from 'react'
import type { AssistantState } from '../components/foundations/StatusIndicator'
import type { JarvisEvent, SystemMetrics } from '../protocol/events'
import { foldOrbSignal, INITIAL_SIGNAL } from '../components/jarvis/orb/orbModel'
import { getLaunchEndpoint } from '../services/endpoint'
import { createSseTransport } from '../services/sse-transport'

export type ConnectionStatus = 'connected' | 'connecting' | 'offline'
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

function metricRows(event: SystemMetrics): MetricRow[] {
  const rows = [
    { label: 'CPU', value: `${event.cpu_percent.toFixed(0)}%` },
    { label: 'RAM', value: `${event.ram_percent.toFixed(0)}%` },
  ]
  const gpu = event.gpu?.util_percent
  if (typeof gpu === 'number') rows.push({ label: 'GPU', value: `${gpu.toFixed(0)}%` })
  return rows
}

/** Live connection state, assistant state and system metrics from the daemon's SSE stream. */
export function useJarvisEvents(): JarvisEventsState {
  const [state, setState] = useState<JarvisEventsState>(() => ({
    status: getLaunchEndpoint() ? 'connecting' : 'offline',
    voiceState: INITIAL_SIGNAL.state,
    speaking: INITIAL_SIGNAL.speaking,
    metrics: null,
  }))

  useEffect(() => {
    const target = getLaunchEndpoint()
    if (!target) return
    const transport = createSseTransport(target.url, target.token)
    const started = Date.now()
    let hasConnected = false

    const unsubscribe = transport.subscribe((event: JarvisEvent) => {
      setState((prev) => {
        const next = { ...prev, status: 'connected' as const }
        const signal = foldOrbSignal({ state: prev.voiceState, speaking: prev.speaking }, event)
        next.voiceState = signal.state
        next.speaking = signal.speaking
        if (event.name === 'system.metrics') next.metrics = metricRows(event)
        return next
      })
    })
    const poll = setInterval(() => {
      if (transport.connected) hasConnected = true
      const status = transport.connected ? 'connected' : hasConnected || Date.now() - started >= 3000 ? 'offline' : 'connecting'
      setState((prev) => (prev.status === status ? prev : { ...prev, status }))
    }, 500)

    return () => {
      clearInterval(poll)
      unsubscribe()
      transport.close()
    }
  }, [])

  return state
}
