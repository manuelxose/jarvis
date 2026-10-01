import { AssistantStatus } from '../../../components/jarvis/AssistantStatus'
import type { AssistantState } from '../../../components/foundations/StatusIndicator'
import { Panel } from '../../../components/layout/Panel'
import { ConversationMessage } from '../../../components/jarvis/ConversationMessage'
import { mockConversation, mockDevelopment } from '../../../mocks/fixtures'

export interface WorkspaceProps {
  state: AssistantState
  speaking: boolean
}

export function OverviewWorkspace({ state, speaking }: WorkspaceProps) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-5)' }}>
      <Panel>
        <AssistantStatus state={state} speaking={speaking} headline="Ready for the next command" detail="Two-clap activation confirmed 40ms ago · confidence 0.92" />
      </Panel>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
        <Panel title="Conversation">
          {mockConversation.slice(0, 2).map((m) => (
            <div key={m.id} style={{ marginBottom: 'var(--space-2)' }}>
              <ConversationMessage message={m} />
            </div>
          ))}
        </Panel>
        <Panel title="Development">
          {mockDevelopment
            .filter((d) => d.label !== 'Repos')
            .map((d) => (
              <div key={d.label} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--text-sm)', padding: '4px 0' }}>
                <span style={{ color: 'var(--text-secondary)' }}>{d.label}</span>
                <span style={{ fontFamily: 'var(--font-mono)' }}>{d.value}</span>
              </div>
            ))}
        </Panel>
      </div>
    </div>
  )
}
