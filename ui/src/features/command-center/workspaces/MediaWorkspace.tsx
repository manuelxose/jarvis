import { useState } from 'react'
import { Panel } from '../../../components/layout/Panel'
import { MediaControl } from '../../../components/jarvis/MediaControl'
import { mockMedia } from '../../../mocks/fixtures'

export function MediaWorkspace() {
  const [isPlaying, setIsPlaying] = useState(mockMedia.isPlaying)

  return (
    <Panel title="Media">
      <MediaControl
        track={mockMedia.track}
        isPlaying={isPlaying}
        onTogglePlay={() => setIsPlaying((p) => !p)}
        onNext={() => {}}
        onPrev={() => {}}
      />
    </Panel>
  )
}
