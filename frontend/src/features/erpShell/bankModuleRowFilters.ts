import type { FlatRow } from './useModuleTransactions'
import { bankSourceFileStem } from '../../utils/bankSourceFile'
import { txSourceLabel } from '../../utils/rowSourceLabel'

const BANK_ACCOUNT_TYPE_KEYS = ['account_type', '賬戶類型', '帳戶類型', '账户类型'] as const

export type BankModuleFilters = {
  account: string
  batch: string
  description: string
  status: 'all' | 'reconciled' | 'unreconciled' | 'open'
  dateFrom: string
  dateTo: string
}

export type BankBatchOption = { key: string; label: string }

export type BankModuleSectionHeaders = {
  fileHeader?: string | null
  accountHeader?: string | null
}

export type BankReconStatus = 'reconciled' | 'unreconciled' | 'open'

export function bankAccountTypeOf(tx: Record<string, unknown>): string {
  for (const key of BANK_ACCOUNT_TYPE_KEYS) {
    const v = tx[key]
    if (v != null && String(v).trim() !== '') return String(v).trim()
  }
  return ''
}

export function bankDescriptionOf(tx: Record<string, unknown>): string {
  const v = tx.particulars ?? tx.description
  return v == null ? '' : String(v)
}

export function bankDateOf(tx: Record<string, unknown>): string {
  const v = tx.date ?? tx.transaction_date
  return v == null ? '' : String(v).slice(0, 10)
}

export function bankReconStatusOf(tx: Record<string, unknown>): BankReconStatus {
  const matched = String(tx.matched_id ?? '').trim()
  if (matched) return 'reconciled'
  if (tx.needs_review === true) return 'open'
  return 'unreconciled'
}

function inDateRange(iso: string, from: string, to: string): boolean {
  if (!iso) return true
  const d = iso.slice(0, 10)
  if (from && d < from.slice(0, 10)) return false
  if (to && d > to.slice(0, 10)) return false
  return true
}

export function deriveBankAccountOptions(rows: FlatRow[]): string[] {
  const set = new Set<string>()
  for (const row of rows) {
    const v = bankAccountTypeOf(row.tx)
    if (v) set.add(v)
  }
  return Array.from(set).sort()
}

export function bankBatchPartitionKey(row: FlatRow): string {
  return `${row.runId}::${row.batchId}`
}

export function bankAccountPartitionKey(row: FlatRow): string {
  return `${bankAccountTypeOf(row.tx)}|${String(row.tx.account_number ?? '').trim()}`
}

export function bankSortGroupKey(row: FlatRow): string {
  return `${bankBatchPartitionKey(row)}::${bankAccountPartitionKey(row)}`
}

export function bankBatchPartitionLabel(row: FlatRow): string {
  const stem = bankSourceFileStem(txSourceLabel(row.tx, row.filename))
  const title = row.runTitle?.trim() || 'Batch'
  return stem ? `${title} · ${stem}` : title
}

export function bankAccountPartitionLabel(row: FlatRow): string {
  const type = bankAccountTypeOf(row.tx) || 'Unknown account'
  const num = String(row.tx.account_number ?? '').trim()
  return num ? `${type} · Acct: ${num}` : type
}

export function deriveBankBatchOptions(rows: FlatRow[]): BankBatchOption[] {
  const seen = new Map<string, string>()
  for (const row of rows) {
    const key = bankBatchPartitionKey(row)
    if (!seen.has(key)) seen.set(key, bankBatchPartitionLabel(row))
  }
  return Array.from(seen.entries())
    .map(([key, label]) => ({ key, label }))
    .sort((a, b) => a.label.localeCompare(b.label))
}

export function bankModuleSectionHeaders(
  row: FlatRow,
  prev: FlatRow | null,
): BankModuleSectionHeaders {
  const showFile = !prev || bankBatchPartitionKey(row) !== bankBatchPartitionKey(prev)
  const showAccount =
    showFile || !prev || bankAccountPartitionKey(row) !== bankAccountPartitionKey(prev)
  return {
    fileHeader: showFile ? bankBatchPartitionLabel(row) : null,
    accountHeader: showAccount ? bankAccountPartitionLabel(row) : null,
  }
}

export function filterBankModuleRows(rows: FlatRow[], filters: BankModuleFilters): FlatRow[] {
  const descQ = filters.description.trim().toLowerCase()
  return rows.filter(row => {
    const tx = row.tx
    if (filters.batch && bankBatchPartitionKey(row) !== filters.batch) return false
    if (filters.account && bankAccountTypeOf(tx) !== filters.account) return false
    if (descQ) {
      const hay = bankDescriptionOf(tx).toLowerCase()
      if (!hay.includes(descQ)) return false
    }
    if (filters.status !== 'all' && bankReconStatusOf(tx) !== filters.status) return false
    if (!inDateRange(bankDateOf(tx), filters.dateFrom, filters.dateTo)) return false
    return true
  })
}
