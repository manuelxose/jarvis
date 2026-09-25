import { Panel } from '../../../components/layout/Panel'
import { Timeline } from '../../../components/data/Timeline'
import { mockDevelopment, mockTimeline } from '../../../mocks/fixtures'

export function DevelopmentWorkspace() {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
      <Panel title="GSD Pi milestone">
        {mockDevelopment.map((d) => (
          <div key={d.label} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--text-sm)', padding: '4px 0' }}>
            <span style={{ color: 'var(--text-secondary)' }}>{d.label}</span>
            <span style={{ fontFamily: 'var(--font-mono)' }}>{d.value}</span>
          </div>
        ))}
      </Panel>
      <Panel title="Development activity">
        <Timeline events={mockTimeline} />
      </Panel>
    </div>
  )
}
