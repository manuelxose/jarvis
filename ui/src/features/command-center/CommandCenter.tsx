import { useEffect, useRef, useState } from 'react'
import type { DesktopMode } from '../../shell/jarvis-desktop'
import { ConfirmationCard } from '../../components/interaction/ConfirmationCard'
import controlStyles from '../../components/interaction/ConfirmationCard.module.css'
import type { ControlAction } from '../../protocol/commands'
import { sendControl } from '../../services/command-client'
import { AppFrame } from '../../components/layout/AppFrame'
import { Sidebar, type SidebarItem } from '../../components/layout/Sidebar'
import { TopBar } from '../../components/layout/TopBar'
import { Panel } from '../../components/layout/Panel'
import { IconButton } from '../../components/foundations/IconButton'
import { EmptyState } from '../../components/data/EmptyState'
import { CommandPalette, type Command } from '../../components/interaction/CommandPalette'
import { AgentActivityList } from '../../components/jarvis/AgentActivity'
import { StatusIndicator, type AssistantState } from '../../components/foundations/StatusIndicator'
import { mockAgents } from '../../mocks/fixtures'
import { useJarvisEvents, type ConnectionStatus } from '../../app/useJarvisEvents'
import { OverviewWorkspace, type WorkspaceProps } from './workspaces/OverviewWorkspace'
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

const WORKSPACE_COMPONENTS: Record<string, (props: WorkspaceProps) => React.JSX.Element> = {
  overview: OverviewWorkspace,
  conversation: ConversationWorkspace,
  memory: MemoryWorkspace,
  system: SystemWorkspace,
  agents: AgentsWorkspace,
  development: DevelopmentWorkspace,
  media: MediaWorkspace,
  settings: SettingsWorkspace,
}

const CONNECTION_LABEL: Record<ConnectionStatus, string> = {
  connected: 'Connected',
  connecting: 'Connecting…',
  reconnecting: 'Reconnecting…',
  disconnected: 'Disconnected — Jarvis Core not running',
  'auth-failure': 'Authentication failed',
  'incompatible-version': 'Incompatible Jarvis Core version',
}

function ConnectionIndicator({ status }: { status: ConnectionStatus }) {
  return (
    <span
      data-testid="connection-status"
      role="status"
      aria-live="polite"
      style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--space-2)', fontSize: 'var(--text-sm)', color: status !== 'connected' && status !== 'connecting' && status !== 'reconnecting' ? 'var(--state-offline)' : 'var(--text-secondary)' }}
    >
      <span
        aria-hidden="true"
        style={{ width: 8, height: 8, borderRadius: status === 'connected' ? '50%' : 2, background: status === 'connected' ? 'var(--accent-primary)' : 'transparent', border: '1px solid currentColor' }}
      />
      {CONNECTION_LABEL[status]}
    </span>
  )
}

export function CommandCenter() {
  const [activeWorkspace, setActiveWorkspace] = useState('overview')
  const { status, voiceState, speaking, metrics, core } = useJarvisEvents()
  const assistantState: AssistantState = status === 'connected' ? voiceState : 'offline'
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [desktopMode, setDesktopMode] = useState<DesktopMode | null>(null)
  const [desktopMessage, setDesktopMessage] = useState('')
  const fullscreen = desktopMode === 'COMMAND_CENTER_FULLSCREEN'
  const fullscreenLabel = fullscreen ? 'Exit fullscreen' : 'Enter fullscreen'

  useEffect(() => {
    const bridge = window.jarvisDesktop
    if (!bridge) return
    let active = true
    let notified = false
    let initialExpired = false
    const accept = (mode: DesktopMode | null) => {
      if (active && (mode === 'COMMAND_CENTER_WINDOWED' || mode === 'COMMAND_CENTER_FULLSCREEN' || mode === 'OVERLAY_COMPACT' || mode === 'OVERLAY_EXPANDED' || mode === 'HIDDEN')) {
        setDesktopMode(mode)
        setDesktopMessage('')
        return true
      }
      return false
    }
    const timer = window.setTimeout(() => {
      initialExpired = true
      if (active && !notified) setDesktopMessage('Desktop mode unavailable. Browser review remains usable.')
    }, 2000)
    let unsubscribe: (() => void) | undefined
    try {
      unsubscribe = bridge.onModeChanged((mode) => { if (accept(mode)) notified = true })
      void bridge.getMode().then((mode) => {
        if (active && !notified && !initialExpired && !accept(mode)) setDesktopMessage('Desktop mode unavailable. Browser review remains usable.')
      }).catch(() => {
        if (active && !notified && !initialExpired) setDesktopMessage('Desktop mode unavailable. Browser review remains usable.')
      }).finally(() => window.clearTimeout(timer))
    } catch {
      queueMicrotask(() => { if (active) setDesktopMessage('Desktop mode unavailable. Browser review remains usable.') })
      window.clearTimeout(timer)
    }
    return () => { active = false; window.clearTimeout(timer); unsubscribe?.() }
  }, [])

  useEffect(() => {
    const settings = window.jarvisDesktop?.settings
    if (!settings) return
    return settings.onOpenRequested(() => setActiveWorkspace('settings'))
  }, [])

  function requestDesktopMode(mode: DesktopMode) {
    const bridge = window.jarvisDesktop
    if (!bridge) {
      setDesktopMessage('Desktop controls are unavailable in browser review.')
      return
    }
    try {
      bridge.requestMode(mode)
      // Fire-and-forget IPC is not confirmation. Only host notifications change labels.
      setDesktopMessage('Desktop mode requested; waiting for native confirmation.')
    } catch {
      setDesktopMessage('Desktop mode request failed. Try again.')
    }
  }
  const [action, setAction] = useState<ControlAction | null>(null)
  const [pending, setPending] = useState(false)
  const [controlError, setControlError] = useState<string | null>(null)
  const [controlMessage, setControlMessage] = useState('')
  const request = useRef<AbortController | null>(null)
  const trigger = useRef<HTMLButtonElement | null>(null)

  useEffect(() => () => { request.current?.abort(); request.current = null }, [])

  function openControl(next: ControlAction, button: HTMLButtonElement) {
    if (status !== 'connected' || request.current) return
    trigger.current = button
    setControlError(null)
    setAction(next)
  }

  function closeControl() {
    if (request.current) return
    setAction(null)
    setControlError(null)
    requestAnimationFrame(() => trigger.current?.focus())
  }

  async function confirmControl() {
    if (!action || status !== 'connected' || request.current) return
    const selected = action
    const controller = new AbortController()
    request.current = controller // synchronous lock against repeated clicks
    setPending(true)
    setControlError(null)
    setControlMessage('')
    try {
      await sendControl(selected, { signal: controller.signal })
      if (request.current !== controller) return
      setControlMessage(`${selected === 'sleep' ? 'Sleep' : 'Activation'} request accepted by daemon. This does not confirm a completed voice turn.`)
      closeControlAfterRequest()
    } catch (error) {
      if (request.current === controller) setControlError(error instanceof Error ? error.message : 'Control request failed.')
    } finally {
      if (request.current === controller) {
        request.current = null
        setPending(false)
      }
    }
  }

  function closeControlAfterRequest() {
    setAction(null)
    requestAnimationFrame(() => trigger.current?.focus())
  }

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

  const commands: Command[] = [
    ...WORKSPACES.map((w) => ({
      id: w.id,
      label: `Go to ${w.label}`,
      run: () => setActiveWorkspace(w.id),
    })),
    { id: 'overlay', label: 'Return to compact overlay', run: () => requestDesktopMode('OVERLAY_COMPACT') },
    { id: 'fullscreen', label: fullscreenLabel, run: () => requestDesktopMode(fullscreen ? 'COMMAND_CENTER_WINDOWED' : 'COMMAND_CENTER_FULLSCREEN') },
  ]

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
                <ConnectionIndicator status={status} />
                {window.jarvisDesktop && <>
                  <IconButton aria-label="Return to compact overlay" title="Return to compact overlay" onClick={() => requestDesktopMode('OVERLAY_COMPACT')}>↙</IconButton>
                  <IconButton aria-label={fullscreenLabel} title={fullscreenLabel} onClick={() => requestDesktopMode(fullscreen ? 'COMMAND_CENTER_WINDOWED' : 'COMMAND_CENTER_FULLSCREEN')}>{fullscreen ? '⊡' : '⛶'}</IconButton>
                </>}
                <button type="button" className={controlStyles.toolbarControl} disabled={status !== 'connected' || pending} onClick={(e) => openControl('activate', e.currentTarget)}>Activate</button>
                <button type="button" className={controlStyles.toolbarControl} disabled={status !== 'connected' || pending} onClick={(e) => openControl('sleep', e.currentTarget)}>Sleep</button>
                <IconButton aria-label="Open command palette (Ctrl+K)" onClick={() => setPaletteOpen(true)}>
                  ⌘K
                </IconButton>
              </>
            }
          />
        }
        liveMessage={`${CONNECTION_LABEL[status]}. ${controlMessage || controlError || (pending ? 'Sending control request.' : '')}`}
        sidebar={<Sidebar items={WORKSPACES} activeId={activeWorkspace} onSelect={setActiveWorkspace} />}
        right={
          <>
            <Panel title="System">
              {metrics ? (
                metrics.map((m) => (
                  <div key={m.label} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 'var(--text-sm)', padding: '4px 0' }}>
                    <span style={{ color: 'var(--text-secondary)' }}>{m.label}</span>
                    <span style={{ fontFamily: 'var(--font-mono)' }}>{m.value}</span>
                  </div>
                ))
              ) : (
                <EmptyState title="No live metrics" description="Waiting for system.metrics from the daemon." />
              )}
            </Panel>
            <Panel title="Agents (sample data)">
              <AgentActivityList entries={mockAgents} />
            </Panel>
          </>
        }
      >
        {desktopMessage && <p className={controlStyles.result} role="status">{desktopMessage}</p>}
        {controlMessage && <p className={controlStyles.result} role="status">{controlMessage}</p>}
        {activeWorkspace !== 'overview' && (
          <div style={{ marginBottom: 'var(--space-4)' }}>
            <StatusIndicator state={assistantState} />
          </div>
        )}
        <Workspace state={assistantState} speaking={status === 'connected' && speaking} core={core} />
      </AppFrame>
      <CommandPalette commands={commands} open={paletteOpen} onOpenChange={setPaletteOpen} />
      <ConfirmationCard action={action} pending={pending} available={status === 'connected'} error={controlError} onCancel={closeControl} onConfirm={confirmControl} />
    </>
  )
}
