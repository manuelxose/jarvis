import { Badge } from '../foundations/Badge'
import { ProgressIndicator } from '../data/ProgressIndicator'

export interface TaskProgressData {
  id: string
  title: string
  percent: number | null
  status: 'pending' | 'running' | 'done' | 'blocked'
}

const TONE: Record<TaskProgressData['status'], 'neutral' | 'info' | 'success' | 'danger'> = {
  pending: 'neutral',
  running: 'info',
  done: 'success',
  blocked: 'danger',
}

export function TaskProgress({ task }: { task: TaskProgressData }) {
  return (
    <div style={{ marginBottom: 'var(--space-3)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
        <span style={{ fontSize: 'var(--text-sm)' }}>{task.title}</span>
        <Badge tone={TONE[task.status]}>{task.status}</Badge>
      </div>
      <ProgressIndicator label={task.title} percent={task.percent} />
    </div>
  )
}
