import { useState } from 'react'
import { Panel } from '../../../components/layout/Panel'
import { Toolbar } from '../../../components/layout/Toolbar'
import { Button } from '../../../components/foundations/Button'
import { SystemHealthPanel } from '../../../components/jarvis/SystemHealth'
import { Skeleton } from '../../../components/foundations/LoadingIndicator'
import { EmptyState } from '../../../components/data/EmptyState'
import { mockSystemMetrics } from '../../../mocks/fixtures'

type ConnectionState = 'connected' | 'loading' | 'error' | 'disconnected'

const OPTIONS: ConnectionState[] = ['connected', 'loading', 'error', 'disconnected']

/** Demonstrates the required loading/error/disconnected/populated states (section 11) — not simulated telemetry, a state switcher for review. */
export function SystemWorkspace() {
  const [state, setState] = useState<ConnectionState>('connected')

  return (
    <div>
      <Toolbar aria-label="Simulate connection state">
        {OPTIONS.map((opt) => (
          <Button key={opt} variant={state === opt ? 'primary' : 'default'} aria-pressed={state === opt} onClick={() => setState(opt)}>
            {opt}
          </Button>
        ))}
      </Toolbar>
      {state === 'loading' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 'var(--space-4)' }}>
          {[0, 1, 2].map((i) => (
            <Panel key={i}>
              <Skeleton width="60%" />
              <div style={{ height: 8 }} />
              <Skeleton width="100%" />
            </Panel>
          ))}
        </div>
      )}
      {state === 'error' && (
        <Panel>
          <EmptyState title="System telemetry unavailable" description="The Jarvis Core bridge returned an error while reading host metrics. Retry, or check src/jarvis/observability/metrics.py." />
        </Panel>
      )}
      {state === 'disconnected' && (
        <Panel>
          <EmptyState title="Not connected to Jarvis Core" description="No transport is wired yet this phase — see docs/architecture/frontend-architecture.md's Jarvis UI Bridge section." />
        </Panel>
      )}
      {state === 'connected' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 'var(--space-4)' }}>
          {mockSystemMetrics.map((m) => (
            <Panel title={m.label} key={m.label}>
              <SystemHealthPanel metrics={[m]} />
            </Panel>
          ))}
        </div>
      )}
    </div>
  )
}
