import type { ReactElement } from 'react'
import { Menu as BaseMenu } from '@base-ui/react/menu'
import styles from './Menu.module.css'

export interface MenuItemDef {
  label: string
  onSelect: () => void
  disabled?: boolean
}

interface DropdownMenuProps {
  /** A single focusable element (e.g. an IconButton) — Base UI merges its own props into it rather than wrapping it, so the DOM has one interactive control, not two nested ones. */
  trigger: ReactElement
  items: MenuItemDef[]
}

export function DropdownMenu({ trigger, items }: DropdownMenuProps) {
  return (
    <BaseMenu.Root>
      <BaseMenu.Trigger render={trigger} />
      <BaseMenu.Portal>
        <BaseMenu.Positioner sideOffset={6}>
          <BaseMenu.Popup className={styles.popup}>
            {items.map((item) => (
              <BaseMenu.Item key={item.label} className={styles.item} disabled={item.disabled} onClick={item.onSelect}>
                {item.label}
              </BaseMenu.Item>
            ))}
          </BaseMenu.Popup>
        </BaseMenu.Positioner>
      </BaseMenu.Portal>
    </BaseMenu.Root>
  )
}
