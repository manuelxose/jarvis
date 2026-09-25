import { useEffect, useState } from 'react'
import { AppFrame } from '../../components/layout/AppFrame'
import { Sidebar, type SidebarItem } from '../../components/layout/Sidebar'
import { TopBar } from '../../components/layout/TopBar'
import { Panel } from '../../components/layout/Panel'
import { IconButton } from '../../components/foundations/IconButton'
import { DropdownMenu } from '../../components/interaction/DropdownMenu'
import { CommandPalette, type Command } from '../../components/interaction/CommandPalette'
import { AgentActivityList } from '../../components/jarvis/AgentActivity'
import { StatusIndicator, type AssistantState } from '../../components/foundations/StatusIndicator'
import { mockAgents, mockSystemMetrics } from '../../mocks/fixtures'
import { OverviewWorkspace } from './workspaces/OverviewWorkspace'
import { ConversationWorkspace } from './workspaces/ConversationWorkspace'
import { MemoryWorkspace } from './workspaces/MemoryWorkspace'
import { SystemWorkspace } from './workspaces/SystemWorkspace'
import { AgentsWorkspace } from './workspaces/AgentsWorkspace'
import { DevelopmentWorkspace } from './workspaces/DevelopmentWorkspace'
import { MediaWorkspace } from './workspaces/MediaWorkspace'
import { SettingsWorkspace } from './workspaces/SettingsWorkspace'

const WORKSPACES: SidebarItem[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'conversation', label: 'Conversation' },
  { id: 'memory', label: 'Memory' },
  { id: 'system', label: 'System' },
  { id: 'agents', label: 'Agents' },
  { id: 'development', label: 'Development' },
  { id: 'media', label: 'Media' },
  { id: 'settings', label: 'Settings' },
]

const WORKSPACE_COMPONENTS: Record<string, () => React.JSX.Element> = {
  overview: OverviewWorkspace,
  conversation: ConversationWorkspace,
  memory: MemoryWorkspace,
  system: SystemWorkspace,
  agents: AgentsWorkspace,
  development: DevelopmentWorkspace,
  media: MediaWorkspace,
  settings: SettingsWorkspace,
}

const ASSISTANT_STATES: AssistantState[] = ['idle', 'listening', 'speaking', 'processing', 'executing', 'error', 'offline']

export function CommandCenter() {
  const [activeWorkspace, setActiveWorkspace] = useState('overview')
  const [assistantState, setAssistantState] = useState<AssistantState>('listening')
  const [paletteOpen, setPaletteOpen] = useState(false)

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPaletteOpen((open) => !open)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  const commands: Command[] = WORKSPACES.map((w) => ({
    id: w.id,
    label: `Go to ${w.label}`,
    run: () => setActiveWorkspace(w.id),
  }))

  const Workspace = WORKSPACE_COMPONENTS[activeWorkspace] ?? OverviewWorkspace

  return (
    <>
      <AppFrame
        topbar={
          <TopBar
            title="Jarvis Command Center"
            state={assistantState}
            actions={
              <>
                <DropdownMenu
                  trigger={
                    <IconButton aria-label="Simulate assistant state" title="Simulate assistant state">
                      ⋯
                    </IconButton>
                  }
                  items={ASSISTANT_STATES.map((s) => ({ label: s, onSelect: () => setAssistantState(s) }))}
                />
                <IconButton aria-label="Open command palette (Ctrl+K)" onClick={() => setPaletteOpen(true)}>
                  ⌘K
                </IconButton>
              </>
            }
          />
        }
        sidebar={<Sidebar items={WORKSPACES} activeId={activeWorkspace} onSelect={setActiveWorkspace} />}
        right={
          <>
            <Panel title="System">
              {mockSystemMetrics.map((m) => (
                <div key={m.label} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--text-sm)', padding: '4px 0' }}>
                  <span style={{ color: 'var(--text-secondary)' }}>{m.label}</span>
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{m.value}</span>
                </div>
              ))}
            </Panel>
            <Panel title="Agents">
              <AgentActivityList entries={mockAgents} />
            </Panel>
          </>
        }
      >
        {activeWorkspace !== 'overview' && (
          <div style={{ marginBottom: 'var(--space-4)' }}>
            <StatusIndicator state={assistantState} />
          </div>
        )}
        <Workspace />
      </AppFrame>
      <CommandPalette commands={commands} open={paletteOpen} onOpenChange={setPaletteOpen} />
    </>
  )
}
