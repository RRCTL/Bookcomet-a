/** Shared AI chat table patch application (id_number + field + value). */

export type AiTablePatch = { id_number: string; field: string; value: unknown }

export function applyTablePatchesToRows<T extends { id_number?: string }>(
  rows: T[],
  patches: AiTablePatch[],
  isRowLocked?: (row: T) => boolean,
): T[] {
  return rows.map(row => {
    if (isRowLocked?.(row)) return row
    const rowPatches = patches.filter(p => p.id_number === (row.id_number ?? ''))
    if (rowPatches.length === 0) return row
    let updated = { ...row } as T
    rowPatches.forEach(p => {
      updated = { ...updated, [p.field]: p.value } as T
    })
    return updated
  })
}
