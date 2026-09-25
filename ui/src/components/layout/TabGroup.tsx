import type { ReactNode } from 'react'
import { Tabs } from '@base-ui/react/tabs'
import styles from './TabGroup.module.css'

export interface TabDef {
  value: string
  label: string
  content: ReactNode
}

interface TabGroupProps {
  tabs: TabDef[]
  defaultValue?: string
}

export function TabGroup({ tabs, defaultValue }: TabGroupProps) {
  return (
    <Tabs.Root defaultValue={defaultValue ?? tabs[0]?.value}>
      <Tabs.List className={styles.list}>
        {tabs.map((tab) => (
          <Tabs.Tab key={tab.value} value={tab.value} className={styles.tab}>
            {tab.label}
          </Tabs.Tab>
        ))}
      </Tabs.List>
      {tabs.map((tab) => (
        <Tabs.Panel key={tab.value} value={tab.value}>
          {tab.content}
        </Tabs.Panel>
      ))}
    </Tabs.Root>
  )
}
