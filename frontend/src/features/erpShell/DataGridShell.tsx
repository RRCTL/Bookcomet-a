import { Fragment, useMemo, useState, type ReactNode } from 'react'

export type Column<T> = {
  key: string
  header: string
  /** Cell renderer. Falls back to String(value(row)) when omitted. */
  render?: (row: T) => ReactNode
  /** Sort/CSV key. When omitted the column is not sortable. */
  value?: (row: T) => string | number | null
  numeric?: boolean
}

export type GridSectionHeaders = {
  fileHeader?: string | null
  accountHeader?: string | null
}

type Props<T> = {
  columns: Column<T>[]
  rows: T[]
  getRowId: (row: T) => string
  /** Optional per-row visual flag (e.g. needs review). */
  rowFlag?: (row: T) => boolean
  selectedIds: Set<string>
  onToggleSelect: (id: string) => void
  onToggleAll: (ids: string[], select: boolean) => void
  loading?: boolean
  error?: string | null
  emptyText?: string
  /** When set, column sort applies within each group; group order is preserved. */
  groupKey?: (row: T) => string
  /** Optional two-level section headers (e.g. batch file, then bank account). */
  sectionHeaders?: (row: T, prev: T | null) => GridSectionHeaders | null
  /** When provided, rows that pass `canExpand` get an expand chevron that
   *  reveals a full-width panel rendered by this function. */
  renderExpanded?: (row: T) => ReactNode
  canExpand?: (row: T) => boolean
}

type SortState = { key: string; dir: 'asc' | 'desc' } | null

export function DataGridShell<T>({
  columns,
  rows,
  getRowId,
  rowFlag,
  selectedIds,
  onToggleSelect,
  onToggleAll,
  loading,
  error,
  emptyText = 'No records.',
  groupKey,
  sectionHeaders,
  renderExpanded,
  canExpand,
}: Props<T>) {
  const [sort, setSort] = useState<SortState>(null)
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())
  const expandable = Boolean(renderExpanded)
  const colCount = (expandable ? 1 : 0) + 1 + columns.length

  const sortedRows = useMemo(() => {
    if (!sort) return rows
    const col = columns.find(c => c.key === sort.key)
    if (!col?.value) return rows
    const dir = sort.dir === 'asc' ? 1 : -1
    const compare = (a: T, b: T) => {
      const va = col.value!(a)
      const vb = col.value!(b)
      if (va == null && vb == null) return 0
      if (va == null) return 1
      if (vb == null) return -1
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir
      return String(va).localeCompare(String(vb)) * dir
    }
    if (!groupKey) return [...rows].sort(compare)
    const groupOrder: string[] = []
    const buckets = new Map<string, T[]>()
    for (const row of rows) {
      const k = groupKey(row)
      if (!buckets.has(k)) {
        buckets.set(k, [])
        groupOrder.push(k)
      }
      buckets.get(k)!.push(row)
    }
    const out: T[] = []
    for (const k of groupOrder) {
      out.push(...[...buckets.get(k)!].sort(compare))
    }
    return out
  }, [rows, sort, columns, groupKey])

  const toggleSort = (col: Column<T>) => {
    if (!col.value) return
    setSort(prev =>
      prev?.key === col.key
        ? { key: col.key, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
        : { key: col.key, dir: 'asc' },
    )
  }

  const visibleIds = sortedRows.map(getRowId)
  const allSelected = visibleIds.length > 0 && visibleIds.every(id => selectedIds.has(id))

  if (loading) return <div className="erp-empty">Loading...</div>
  if (error) return <div className="erp-empty">{error}</div>
  if (rows.length === 0) return <div className="erp-empty">{emptyText}</div>

  return (
    <div className="erp-gridwrap">
      <table className="erp-table">
        <thead>
          <tr>
            {expandable && <th style={{ width: 28, cursor: 'default' }} />}
            <th style={{ width: 32, cursor: 'default' }}>
              <input
                type="checkbox"
                checked={allSelected}
                onChange={e => onToggleAll(visibleIds, e.target.checked)}
                aria-label="Select all"
              />
            </th>
            {columns.map(col => (
              <th
                key={col.key}
                onClick={() => toggleSort(col)}
                style={{ cursor: col.value ? 'pointer' : 'default' }}
              >
                {col.header}
                {sort?.key === col.key && (
                  <span className="erp-arr">{sort.dir === 'asc' ? '\u25B2' : '\u25BC'}</span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row, index) => {
            const id = getRowId(row)
            const prev = index > 0 ? sortedRows[index - 1] : null
            const headers = sectionHeaders?.(row, prev) ?? null
            const selected = selectedIds.has(id)
            const rowExpandable = expandable && (canExpand?.(row) ?? true)
            const expanded = expandedIds.has(id)
            return (
              <Fragment key={id}>
                {headers?.fileHeader && (
                  <tr className="erp-section-file">
                    <td colSpan={colCount}>{headers.fileHeader}</td>
                  </tr>
                )}
                {headers?.accountHeader && (
                  <tr className="erp-section-account">
                    <td colSpan={colCount}>{headers.accountHeader}</td>
                  </tr>
                )}
                <tr className={`${selected ? 'sel' : ''} ${rowFlag?.(row) ? 'flag' : ''}`.trim()}>
                  {expandable && (
                    <td style={{ textAlign: 'center' }}>
                      {rowExpandable && (
                        <button
                          type="button"
                          className="erp-expand-btn"
                          aria-label={expanded ? 'Collapse' : 'Expand'}
                          onClick={() =>
                            setExpandedIds(prev => {
                              const next = new Set(prev)
                              if (next.has(id)) next.delete(id)
                              else next.add(id)
                              return next
                            })
                          }
                        >
                          {expanded ? '\u25BE' : '\u25B8'}
                        </button>
                      )}
                    </td>
                  )}
                  <td>
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={() => onToggleSelect(id)}
                      aria-label="Select row"
                    />
                  </td>
                  {columns.map(col => (
                    <td key={col.key} className={col.numeric ? 'erp-num-c' : undefined}>
                      {col.render ? col.render(row) : String(col.value?.(row) ?? '')}
                    </td>
                  ))}
                </tr>
                {rowExpandable && expanded && (
                  <tr className="erp-expand-row">
                    <td colSpan={colCount}>
                      <div className="erp-expand-panel">{renderExpanded!(row)}</div>
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
