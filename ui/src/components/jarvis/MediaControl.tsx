import { IconButton } from '../foundations/IconButton'

interface MediaControlProps {
  track: string
  isPlaying: boolean
  onTogglePlay: () => void
  onNext: () => void
  onPrev: () => void
}

export function MediaControl({ track, isPlaying, onTogglePlay, onNext, onPrev }: MediaControlProps) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
      <IconButton aria-label="Previous track" onClick={onPrev}>
        ⏮
      </IconButton>
      <IconButton aria-label={isPlaying ? 'Pause' : 'Play'} aria-pressed={isPlaying} onClick={onTogglePlay}>
        {isPlaying ? '⏸' : '▶'}
      </IconButton>
      <IconButton aria-label="Next track" onClick={onNext}>
        ⏭
      </IconButton>
      <span style={{ fontSize: 'var(--text-sm)', color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 160 }}>
        {track}
      </span>
    </div>
  )
}
