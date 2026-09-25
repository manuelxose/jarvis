import type { ReactNode } from 'react'
import styles from './Table.module.css'

export interface TableColumn<T> {
  key: string
  header: string
  render: (row: T) => ReactNode
}

interface TableProps<T> {
  caption: string
  columns: TableColumn<T>[]
  rows: T[]
  rowKey: (row: T) => string
}

export function Table<T>({ caption, columns, rows, rowKey }: TableProps<T>) {
  return (
    <table className={styles.table}>
      <caption style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>{caption}</caption>
      <thead>
        <tr>
          {columns.map((col) => (
            <th key={col.key} scope="col">
              {col.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={rowKey(row)}>
            {columns.map((col) => (
              <td key={col.key}>{col.render(row)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
