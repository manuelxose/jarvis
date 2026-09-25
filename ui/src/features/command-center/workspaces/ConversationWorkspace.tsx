import { useState } from 'react'
import { ConversationMessage } from '../../../components/jarvis/ConversationMessage'
import { StreamingResponsePlaceholder } from '../../../components/jarvis/StreamingResponsePlaceholder'
import { SearchInput } from '../../../components/interaction/SearchInput'
import { EmptyState } from '../../../components/data/EmptyState'
import { mockConversation } from '../../../mocks/fixtures'

export function ConversationWorkspace() {
  const [query, setQuery] = useState('')
  const filtered = mockConversation.filter((m) => m.text.toLowerCase().includes(query.toLowerCase()))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)', height: '100%' }}>
      <SearchInput label="Search conversation" placeholder="Search conversation…" value={query} onChange={(e) => setQuery(e.target.value)} />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
        {filtered.length === 0 ? (
          <EmptyState title="No matching messages" description="Try a different search term." />
        ) : (
          filtered.map((m) => <ConversationMessage key={m.id} message={m} />)
        )}
        <StreamingResponsePlaceholder />
      </div>
    </div>
  )
}
