import { bankSourceFileStem } from '../../utils/bankSourceFile'

/** Shown above the review grid when foreign-source rows were filtered out. */
export const RUN_ROW_FOREIGN_HIDDEN_NOTICE =
  "Some rows came from files that aren't in this run and are hidden. Reload the run to fix this."

/** Shown when the whole table is unusable / Approve must stay off without owned rows. */
export const RUN_ROW_MISMATCH_MESSAGE =
  "These rows don't belong to this run. Reload the run."

export type RunFileRef = {
  task_file_id: string
  original_filename?: string | null
}

function normalizeName(value: string): string {
  return value.trim().toLowerCase()
}

function sourceStemFromRow(row: Record<string, unknown>): string {
  const raw = String(
    row.source_file ?? row.file_position ?? row.filename ?? row.source_page ?? '',
  ).trim()
  if (!raw) return ''
  return normalizeName(bankSourceFileStem(raw))
}

function explicitFileId(row: Record<string, unknown>): string {
  return String(row.task_file_id ?? row.source_file_id ?? '').trim()
}

/** True when the row's source file can be matched to a file on the current run. */
export function rowBelongsToRunFiles(
  row: Record<string, unknown>,
  runFiles: RunFileRef[],
): boolean {
  if (!runFiles.length) return false

  const explicit = explicitFileId(row)
  if (explicit) {
    return runFiles.some(f => f.task_file_id === explicit)
  }

  const stem = sourceStemFromRow(row)
  if (!stem) {
    // Rows with no source marker are only allowed on single-file runs.
    return runFiles.length === 1
  }

  return runFiles.some(f => {
    const name = normalizeName(f.original_filename ?? '')
    if (!name) return false
    const fileStem = name.includes('.') ? name.slice(0, name.lastIndexOf('.')) : name
    // Strict equality / prefix — avoid substring false positives (e.g. "a" in "jan.pdf").
    return (
      name === stem ||
      fileStem === stem ||
      stem === name ||
      stem.startsWith(`${name} `) ||
      stem.startsWith(`${fileStem}.`) ||
      stem.startsWith(`${fileStem} `)
    )
  })
}

export type ReviewRowOwnership = {
  ok: boolean
  foreignCount: number
  ownedRows: Record<string, unknown>[]
  foreignRows: Record<string, unknown>[]
}

/** Split review rows into those owned by the run vs foreign (cross-run bleed). */
export function partitionRowsByRunOwnership(
  rows: Record<string, unknown>[],
  runFiles: RunFileRef[],
): ReviewRowOwnership {
  const ownedRows: Record<string, unknown>[] = []
  const foreignRows: Record<string, unknown>[] = []
  for (const row of rows) {
    if (rowBelongsToRunFiles(row, runFiles)) ownedRows.push(row)
    else foreignRows.push(row)
  }
  return {
    ok: foreignRows.length === 0,
    foreignCount: foreignRows.length,
    ownedRows,
    foreignRows,
  }
}

/**
 * Build the approve / resume payload using only rows whose source files belong
 * to the current run. Foreign rows are dropped so they cannot reach the API
 * via Approve, resume/continue, or any other caller of this helper.
 */
export function filterPayloadRowsToRunFiles(
  payload: Record<string, unknown>,
  runFiles: RunFileRef[],
  processingMode: string,
): { payload: Record<string, unknown>; foreignCount: number; ownedCount: number } {
  const mode = (processingMode || '').toUpperCase()
  const next: Record<string, unknown> = { ...payload }
  let foreignCount = 0
  let ownedCount = 0

  const filterKey = (key: 'bankTransactions' | 'arapTransactions') => {
    const rows = payload[key]
    if (!Array.isArray(rows)) return
    const part = partitionRowsByRunOwnership(
      rows.filter((r): r is Record<string, unknown> => r != null && typeof r === 'object'),
      runFiles,
    )
    next[key] = part.ownedRows
    foreignCount += part.foreignCount
    ownedCount += part.ownedRows.length
  }

  if (mode === 'BANK') filterKey('bankTransactions')
  else if (mode === 'AR' || mode === 'AP') filterKey('arapTransactions')
  else {
    // Unknown / mixed: scrub both keys if present.
    if (Array.isArray(payload.bankTransactions)) filterKey('bankTransactions')
    if (Array.isArray(payload.arapTransactions)) filterKey('arapTransactions')
  }

  return { payload: next, foreignCount, ownedCount }
}
