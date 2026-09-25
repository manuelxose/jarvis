import { useMemo, useState } from 'react'
import { Dialog as BaseDialog } from '@base-ui/react/dialog'
import dialogStyles from './Dialog.module.css'
import styles from './CommandPalette.module.css'

export interface Command {
  id: string
  label: string
  run: () => void
}

interface CommandPaletteProps {
  commands: Command[]
  open: boolean
  onOpenChange: (open: boolean) => void
}

/** Global keyboard-accessible command palette. Open state is owned by the caller so a Ctrl/Cmd+K shortcut can drive it. */
export function CommandPalette({ commands, open, onOpenChange }: CommandPaletteProps) {
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)

  const filtered = useMemo(
    () => commands.filter((c) => c.label.toLowerCase().includes(query.toLowerCase())),
    [commands, query],
  )

  function handleOpenChange(next: boolean) {
    if (!next) {
      setQuery('')
      setActiveIndex(0)
    }
    onOpenChange(next)
  }

  function handleQueryChange(next: string) {
    setQuery(next)
    setActiveIndex(0)
  }

  return (
    <BaseDialog.Root open={open} onOpenChange={handleOpenChange}>
      <BaseDialog.Portal>
        <BaseDialog.Backdrop className={dialogStyles.backdrop} />
        <BaseDialog.Popup className={styles.popup} aria-label="Command palette">
          <input
            autoFocus
            className={styles.input}
            placeholder="Type a command…"
            value={query}
            role="combobox"
            aria-expanded="true"
            aria-controls="command-palette-list"
            aria-activedescendant={filtered[activeIndex] ? `cmd-${filtered[activeIndex].id}` : undefined}
            onChange={(e) => handleQueryChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') {
                e.preventDefault()
                setActiveIndex((i) => Math.min(i + 1, filtered.length - 1))
              } else if (e.key === 'ArrowUp') {
                e.preventDefault()
                setActiveIndex((i) => Math.max(i - 1, 0))
              } else if (e.key === 'Enter' && filtered[activeIndex]) {
                filtered[activeIndex].run()
                handleOpenChange(false)
              }
            }}
          />
          <ul className={styles.list} id="command-palette-list" role="listbox">
            {filtered.map((command, i) => (
              <li
                key={command.id}
                id={`cmd-${command.id}`}
                role="option"
                aria-selected={i === activeIndex}
                className={styles.option}
                onMouseEnter={() => setActiveIndex(i)}
                onClick={() => {
                  command.run()
                  handleOpenChange(false)
                }}
              >
                {command.label}
              </li>
            ))}
            {filtered.length === 0 && (
              <li style={{ padding: 'var(--space-3)', color: 'var(--text-secondary)', fontSize: 'var(--text-sm)' }}>No matching commands</li>
            )}
          </ul>
        </BaseDialog.Popup>
      </BaseDialog.Portal>
    </BaseDialog.Root>
  )
}
