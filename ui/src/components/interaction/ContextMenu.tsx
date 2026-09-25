import type { ReactNode } from 'react'
import { ContextMenu as BaseContextMenu } from '@base-ui/react/context-menu'
import styles from './Menu.module.css'
import type { MenuItemDef } from './DropdownMenu'

interface ContextMenuProps {
  children: ReactNode
  items: MenuItemDef[]
}

export function ContextMenu({ children, items }: ContextMenuProps) {
  return (
    <BaseContextMenu.Root>
      <BaseContextMenu.Trigger render={<div />}>{children}</BaseContextMenu.Trigger>
      <BaseContextMenu.Portal>
        <BaseContextMenu.Positioner>
          <BaseContextMenu.Popup className={styles.popup}>
            {items.map((item) => (
              <BaseContextMenu.Item key={item.label} className={styles.item} disabled={item.disabled} onClick={item.onSelect}>
                {item.label}
              </BaseContextMenu.Item>
            ))}
          </BaseContextMenu.Popup>
        </BaseContextMenu.Positioner>
      </BaseContextMenu.Portal>
    </BaseContextMenu.Root>
  )
}
