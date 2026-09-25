import type { ReactElement } from 'react'
import { Tooltip as BaseTooltip } from '@base-ui/react/tooltip'
import styles from './Tooltip.module.css'

interface TooltipProps {
  label: string
  /** A single focusable element — Base UI merges its own props into it rather than wrapping it. */
  children: ReactElement
}

/** Accessible tooltip (focus + hover, Esc-dismissable) — wraps Base UI, styled with our tokens. */
export function Tooltip({ label, children }: TooltipProps) {
  return (
    <BaseTooltip.Root>
      <BaseTooltip.Trigger render={children} />
      <BaseTooltip.Portal>
        <BaseTooltip.Positioner sideOffset={6}>
          <BaseTooltip.Popup className={styles.popup}>{label}</BaseTooltip.Popup>
        </BaseTooltip.Positioner>
      </BaseTooltip.Portal>
    </BaseTooltip.Root>
  )
}
