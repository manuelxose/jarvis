import { Panel } from '../layout/Panel'

interface SparklineProps {
  values: number[] // 0-100
  color?: string
}

/** Minimal inline SVG sparkline — no charting dependency for a single-series trend line. */
function Sparkline({ values, color = 'var(--accent-primary)' }: SparklineProps) {
  const width = 240
  const height = 40
  const step = width / Math.max(1, values.length - 1)
  const points = values.map((v, i) => `${i * step},${height - (v / 100) * height}`).join(' ')
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Resource usage history">
      <polyline points={points} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

interface ChartContainerProps {
  title: string
  values: number[]
  color?: string
}

/** Reserved boundary for future richer charting (section 6 "resource usage history"). Uses a plain SVG sparkline today — no charting library added speculatively. */
export function ChartContainer({ title, values, color }: ChartContainerProps) {
  return (
    <Panel title={title}>
      <Sparkline values={values} color={color} />
    </Panel>
  )
}
