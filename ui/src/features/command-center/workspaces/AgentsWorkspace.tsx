import { Panel } from '../../../components/layout/Panel'
import { AgentActivityList } from '../../../components/jarvis/AgentActivity'
import { TaskProgress } from '../../../components/jarvis/TaskProgress'
import { mockAgents, mockTasks } from '../../../mocks/fixtures'

export function AgentsWorkspace() {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
      <Panel title="Running agents">
        <AgentActivityList entries={mockAgents} />
      </Panel>
      <Panel title="Task progress">
        {mockTasks.map((t) => (
          <TaskProgress key={t.id} task={t} />
        ))}
      </Panel>
    </div>
  )
}
