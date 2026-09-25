import styles from './Sidebar.module.css'

export interface SidebarItem {
  id: string
  label: string
}

interface SidebarProps {
  items: SidebarItem[]
  activeId: string
  onSelect: (id: string) => void
}

export function Sidebar({ items, activeId, onSelect }: SidebarProps) {
  return (
    <ul className={styles.list}>
      {items.map((item) => (
        <li key={item.id}>
          <button
            type="button"
            className={styles.item}
            aria-current={item.id === activeId ? 'page' : undefined}
            onClick={() => onSelect(item.id)}
          >
            <span className={styles.dot} aria-hidden="true" />
            {item.label}
          </button>
        </li>
      ))}
    </ul>
  )
}
