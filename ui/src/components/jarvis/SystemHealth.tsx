import { MetricDisplay } from '../data/MetricDisplay'
import { ChartContainer } from '../data/ChartContainer'

export interface SystemMetric {
  label: string
  value: string
  percent: number
  history: number[]
}

export function SystemHealthPanel({ metrics }: { metrics: SystemMetric[] }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <div>
        {metrics.map((m) => (
          <MetricDisplay
            key={m.label}
            label={m.label}
            value={m.value}
            percent={m.percent}
            tone={m.percent > 85 ? 'danger' : m.percent > 65 ? 'warning' : 'default'}
          />
        ))}
      </div>
      {metrics[0] && <ChartContainer title={`${metrics[0].label} history`} values={metrics[0].history} />}
    </div>
  )
}
