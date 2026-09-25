import { useState } from 'react'
import { Panel } from '../../../components/layout/Panel'
import { SearchInput } from '../../../components/interaction/SearchInput'
import { EmptyState } from '../../../components/data/EmptyState'
import { mockMemory } from '../../../mocks/fixtures'

export function MemoryWorkspace() {
  const [query, setQuery] = useState('')
  const filtered = mockMemory.recent.filter((f) => f.text.toLowerCase().includes(query.toLowerCase()))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <Panel title="Memory">
        <div style={{ display: 'flex', gap: 'var(--space-6)', marginBottom: 'var(--space-4)', fontSize: 'var(--text-sm)' }}>
          <div>
            <div style={{ color: 'var(--text-secondary)' }}>Facts</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--text-lg)' }}>{mockMemory.factCount}</div>
          </div>
          <div>
            <div style={{ color: 'var(--text-secondary)' }}>Last write</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 'var(--text-lg)' }}>{mockMemory.lastWrite}</div>
          </div>
        </div>
        <SearchInput label="Search memory" placeholder="Search remembered facts…" value={query} onChange={(e) => setQuery(e.target.value)} />
      </Panel>
      <Panel title="Recent — distinct from current conversation context">
        {filtered.length === 0 ? (
          <EmptyState title="No matching facts" />
        ) : (
          filtered.map((f) => (
            <p key={f.id} style={{ fontSize: 'var(--text-sm)', borderBottom: '1px solid var(--border-subtle)', padding: 'var(--space-2) 0' }}>
              {f.text}
            </p>
          ))
        )}
      </Panel>
    </div>
  )
}
