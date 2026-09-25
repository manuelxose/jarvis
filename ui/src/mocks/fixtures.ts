/**
 * Deterministic design-phase fixtures. Explicitly mock — never presented as
 * live telemetry or agent execution (section 11). Separated from any future
 * production data provider so swapping the source is a provider change, not
 * a component rewrite.
 */
import type { ConversationMessageData } from '../components/jarvis/ConversationMessage'
import type { AgentActivityEntry } from '../components/jarvis/AgentActivity'
import type { SystemMetric } from '../components/jarvis/SystemHealth'
import type { TaskProgressData } from '../components/jarvis/TaskProgress'
import type { TimelineEvent } from '../components/data/Timeline'
import type { LogEntry } from '../components/data/LogView'

export const mockConversation: ConversationMessageData[] = [
  { id: 'm1', role: 'you', author: 'You', timestamp: '14:02', text: 'run the phase 2 build and tell me if oxlint is clean on jarvis/ui/src/design-system' },
  { id: 'm2', role: 'jarvis', author: 'Jarvis', timestamp: '14:02', text: 'oxlint finished in 340ms — zero warnings across 14 files (src/design-system/, src/components/). Want me to also re-run the Playwright visual regression suite?' },
  { id: 'm3', role: 'you', author: 'You', timestamp: '14:03', text: 'yes, and check the milestone/M008-futuristic-voice-interface worktree for uncommitted changes first' },
  { id: 'm4', role: 'jarvis', author: 'Jarvis', timestamp: '14:03', text: 'worktree clean. running playwright test --project=desktop-1080p,desktop-1440p now.' },
]

export const mockLog: LogEntry[] = mockConversation.map((m) => ({ id: m.id, timestamp: m.timestamp, source: m.author, text: m.text }))

export const mockAgents: AgentActivityEntry[] = [
  { id: 'a1', name: 'gsd-exec', detail: 'T04 replan-slice · milestone/M008 · S02', state: 'processing' },
  { id: 'a2', name: 'hermes-child', detail: 'degraded fallback — ollama unreachable, retry in 8s', state: 'error' },
  { id: 'a3', name: 'playwright', detail: 'visual regression · desktop-1080p, desktop-1440p', state: 'executing' },
  { id: 'a4', name: 'oxlint', detail: 'idle — last run 340ms, zero warnings', state: 'idle' },
]

export const mockTasks: TaskProgressData[] = [
  { id: 't1', title: 'Design tokens — real values', percent: 100, status: 'done' },
  { id: 't2', title: 'Component library — foundations/layout/data/interaction/jarvis', percent: 100, status: 'done' },
  { id: 't3', title: 'Command Center navigable prototype', percent: 70, status: 'running' },
  { id: 't4', title: 'Accessibility + visual regression pass', percent: null, status: 'pending' },
]

export const mockSystemMetrics: SystemMetric[] = [
  { label: 'CPU', value: '34%', percent: 34, history: [22, 28, 31, 26, 40, 34, 38, 34] },
  { label: 'RAM', value: '61%', percent: 61, history: [50, 54, 58, 60, 59, 63, 61, 61] },
  { label: 'GPU', value: '12%', percent: 12, history: [8, 10, 14, 9, 12, 15, 11, 12] },
]

export const mockDevelopment: { label: string; value: string }[] = [
  { label: 'Milestone', value: 'M008 · S02' },
  { label: 'Worktree', value: 'clean' },
  { label: 'Branch', value: 'main' },
  { label: 'VS Code', value: '2 windows' },
  { label: 'Repos', value: 'jarvis, jarvis/ui' },
]

export const mockTimeline: TimelineEvent[] = [
  { id: 'e1', time: '14:03:02', text: 'playwright test started (desktop-1080p, desktop-1440p)' },
  { id: 'e2', time: '14:02:41', text: 'worktree check: clean' },
  { id: 'e3', time: '14:02:12', text: 'oxlint finished — zero warnings', tone: 'default' },
  { id: 'e4', time: '13:58:04', text: 'hermes-child degraded fallback triggered', tone: 'warning' },
]

export const mockMemory = {
  factCount: 128,
  lastWrite: '3m ago',
  recent: [
    { id: 'f1', text: 'Jarvis UI targets Windows desktop first; WSL is the dev environment only.' },
    { id: 'f2', text: 'Command Palette shortcut is Ctrl/Cmd+K across both Overlay and Command Center.' },
    { id: 'f3', text: 'Visual core placeholder must not import a 3D/WebGL dependency this phase.' },
  ],
}

export const mockMedia = {
  track: 'Focus — Instrumental Mix',
  isPlaying: true,
}
