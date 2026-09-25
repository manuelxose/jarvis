import { Panel } from '../../../components/layout/Panel'
import { Badge } from '../../../components/foundations/Badge'

export function SettingsWorkspace() {
  return (
    <Panel title="Appearance">
      <p style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)', maxWidth: 480 }}>
        Jarvis honours the OS <code>prefers-reduced-motion</code> setting automatically — all motion tokens
        collapse to 0ms. There is one design direction and one density this phase (see{' '}
        <code>docs/design/jarvis-design-system.md</code>); theme switching and density presets are deferred
        to a later phase, not built speculatively here.
      </p>
      <div style={{ display: 'flex', gap: 'var(--space-2)', marginTop: 'var(--space-3)' }}>
        <Badge tone="info">Direction: Adaptive Command</Badge>
        <Badge>Density: standard</Badge>
      </div>
    </Panel>
  )
}
