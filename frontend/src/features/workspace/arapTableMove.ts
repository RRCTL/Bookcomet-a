import type { SpreadsheetRow } from '../../components/EditableSpreadsheet'
import type { ARAPTransaction } from '../../components/ARAPReview'
import type { Message } from './types'
import type { ReconState } from '../../types/reconciliation'
import { isLedgerRowGlPosted } from '../../utils/glPostedOcrLock'
import {
  spreadsheetRowsToArapTransactions,
} from './buildSpreadsheetFromOcrResult'

const EMPTY_GL: ReadonlySet<string> = new Set()

/** Stable key for matching moved rows when removing from source (weak for empty id_number). */
export function arapRowIdentity(t: ARAPTransaction): string {
  const id = String(t.id_number ?? '').trim()
  if (id) return `id:${id}`
  return `e:${t.date ?? ''}|${t.amount ?? ''}|${t.memo ?? ''}|${t.source_file ?? ''}`
}

export function isArapRowReconLocked(
  row: ARAPTransaction,
  reconState: ReconState | undefined,
): boolean {
  return reconState?.[String(row.id_number ?? '').trim()]?.status === 'matched'
}

export function isArapRowGlPosted(
  row: ARAPTransaction,
  glPostedLedgerLockKeys: ReadonlySet<string> | undefined,
): boolean {
  return isLedgerRowGlPosted(row, glPostedLedgerLockKeys ?? EMPTY_GL)
}

/** Spec: locked + GL-posted rows cannot be moved. */
export function validateRowsMovable(
  rows: ARAPTransaction[],
  reconState: ReconState | undefined,
  glPostedLedgerLockKeys: ReadonlySet<string> | undefined,
): { ok: true } | { ok: false; reason: 'recon_locked' | 'gl_posted' } {
  for (const r of rows) {
    if (isArapRowReconLocked(r, reconState)) return { ok: false, reason: 'recon_locked' }
  }
  for (const r of rows) {
    if (isArapRowGlPosted(r, glPostedLedgerLockKeys)) return { ok: false, reason: 'gl_posted' }
  }
  return { ok: true }
}

/** Non-empty id_number already present on target (case-normalized). */
export function hasIdNumberConflict(
  targetTransactions: ARAPTransaction[] | undefined,
  movedRows: ARAPTransaction[],
): boolean {
  const targetIds = new Set(
    (targetTransactions ?? [])
      .map((t) => String(t.id_number ?? '').trim())
      .filter(Boolean),
  )
  for (const m of movedRows) {
    const id = String(m.id_number ?? '').trim()
    if (id && targetIds.has(id)) return true
  }
  return false
}

function filterSpreadsheetRemoveIdentities(
  rows: SpreadsheetRow[] | undefined,
  removed: Set<string>,
  taskProcessingMode: string,
): SpreadsheetRow[] | undefined {
  if (!rows?.length) return rows
  const next = rows.filter((row) => {
    const [txn] = spreadsheetRowsToArapTransactions([row], taskProcessingMode)
    const key = arapRowIdentity(txn)
    return !removed.has(key)
  })
  return next.length ? next : undefined
}

/** Minimal AR/AP txn → spreadsheet row for rows appended to a message that already had spreadsheetData. */
export function arapTransactionToSpreadsheetRow(t: ARAPTransaction): SpreadsheetRow {
  const id = String(t.id_number ?? '').trim() || `tmp-${Date.now()}`
  const stripPrefix = (x: string) => x.replace(/^(AR|AP)-/i, '')
  return {
    id,
    voucher_no: stripPrefix(id),
    transaction_type: String(t.transaction_type ?? 'AR').toUpperCase(),
    amount:
      t.amount != null && t.amount !== undefined
        ? String(Number(t.amount))
        : '',
    currency: t.currency ?? 'HKD',
    date: t.date ?? '',
    payer: t.payer ?? '',
    payee: t.payee ?? '',
    bank: t.bank ?? '',
    memo: t.memo ?? '',
    category: t.category ?? '',
    confidence: String(t.confidence ?? ''),
    file_position: t.source_file ?? '',
    account_code: t.account_code,
  }
}

export type ApplyArapMoveResult =
  | { ok: true; nextMessages: Message[] }
  | { ok: false; error: 'source_not_found' | 'target_not_found' | 'same_message' | 'id_conflict' }

/**
 * Move rows between two assistant messages' arapTransactions; sync spreadsheetData when present.
 * When source table becomes empty, strips arap + spreadsheet table fields (fileRefs/content preserved).
 */
export function applyArapMoveMessages(
  messages: Message[],
  sourceMessageId: string,
  targetMessageId: string,
  movedRows: ARAPTransaction[],
  taskProcessingMode: string,
): ApplyArapMoveResult {
  if (sourceMessageId === targetMessageId) return { ok: false, error: 'same_message' }
  const srcIdx = messages.findIndex((m) => m.id === sourceMessageId)
  const tgtIdx = messages.findIndex((m) => m.id === targetMessageId)
  if (srcIdx < 0) return { ok: false, error: 'source_not_found' }
  if (tgtIdx < 0) return { ok: false, error: 'target_not_found' }

  const source = messages[srcIdx]
  const target = messages[tgtIdx]
  const sourceArap = source.arapTransactions ?? []
  const targetArap = target.arapTransactions ?? []

  const movedKeys = new Set(movedRows.map(arapRowIdentity))
  const filteredSource = sourceArap.filter((tx) => !movedKeys.has(arapRowIdentity(tx)))

  if (hasIdNumberConflict(targetArap, movedRows)) {
    return { ok: false, error: 'id_conflict' }
  }

  const nextTargetArap = [...targetArap, ...movedRows]

  const nextSourceSpreadsheet = filterSpreadsheetRemoveIdentities(
    source.spreadsheetData,
    movedKeys,
    taskProcessingMode,
  )

  let nextTargetSpreadsheet: SpreadsheetRow[] | undefined
  if (target.spreadsheetData?.length && movedRows.length) {
    nextTargetSpreadsheet = [
      ...target.spreadsheetData,
      ...movedRows.map(arapTransactionToSpreadsheetRow),
    ]
  } else {
    nextTargetSpreadsheet = target.spreadsheetData
  }

  const outMessages: Message[] = messages.map((m) => {
    if (m.id === sourceMessageId) {
      if (filteredSource.length === 0) {
        const { arapTransactions: _a, arapFilename: _f, spreadsheetData: _s, ...rest } = m
        void _a
        void _f
        void _s
        return {
          ...rest,
          arapTransactions: undefined,
          arapFilename: undefined,
          spreadsheetData: undefined,
        }
      }
      return {
        ...m,
        arapTransactions: filteredSource,
        spreadsheetData: nextSourceSpreadsheet,
      }
    }
    if (m.id === targetMessageId) {
      return {
        ...m,
        arapTransactions: nextTargetArap,
        spreadsheetData:
          nextTargetSpreadsheet !== undefined ? nextTargetSpreadsheet : m.spreadsheetData,
      }
    }
    return m
  })

  return { ok: true, nextMessages: outMessages }
}
