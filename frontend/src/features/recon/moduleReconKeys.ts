import { hydrateDebitCredit } from '../erpShell/arapDebitCredit'

/** Matched / reconciled Books rows are locked (no edit or delete in modules). */
export function isModuleTxnLocked(tx: Record<string, unknown> | null | undefined): boolean {
  if (!tx) return false
  return String(tx.matched_id ?? '').trim().length > 0
}

const HKD_ALIASES = new Set([
  'HKD',
  'HK$',
  'HK',
  'HONG KONG DOLLAR',
  'HONGKONG DOLLAR',
  'HONG KONG DOLLARS',
  '港元',
  '港幣',
  '港币',
])

/** Normalize currency so bank OCR labels (港元) match ledger ISO HKD for Match. */
export function normalizeReconCurrency(raw: string | undefined | null): string {
  const c = String(raw ?? '').trim()
  if (!c) return 'HKD'
  if (HKD_ALIASES.has(c) || HKD_ALIASES.has(c.toUpperCase())) return 'HKD'
  return c.toUpperCase()
}

/** Normalize date strings so module (`2025/01/02`) and DB ISO (`2025-01-02`) share one key. */
export function normalizeReconDate(raw: string): string {
  const text = String(raw ?? '').trim()
  if (!text) return ''
  const iso = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/)
  if (iso) {
    return `${iso[1]}-${iso[2]!.padStart(2, '0')}-${iso[3]!.padStart(2, '0')}`
  }
  const slashYmd = text.match(/^(\d{4})\/(\d{1,2})\/(\d{1,2})/)
  if (slashYmd) {
    return `${slashYmd[1]}-${slashYmd[2]!.padStart(2, '0')}-${slashYmd[3]!.padStart(2, '0')}`
  }
  const dmy = text.match(/^(\d{1,2})[/-](\d{1,2})[/-](\d{4})/)
  if (dmy) {
    return `${dmy[3]}-${dmy[2]!.padStart(2, '0')}-${dmy[1]!.padStart(2, '0')}`
  }
  return text.slice(0, 10)
}

export function bankReconDedupKey(date: string, amount: number, account: string): string {
  return `${normalizeReconDate(date)}|${Number(amount).toFixed(2)}|${account.trim().toLowerCase()}`
}

export function ledgerReconDedupKey(
  module: string,
  voucher: string,
  date: string,
  amount: number,
): string {
  return `${module.toUpperCase()}|${voucher.trim()}|${normalizeReconDate(date)}|${Number(amount).toFixed(2)}`
}

export function bankAmountFromModuleTx(tx: Record<string, unknown>): number | null {
  const dep = Number(tx.deposit ?? 0)
  const wit = Number(tx.withdrawal ?? 0)
  if (dep || wit) return dep - wit
  // Some imports/OCR rows use debit/credit instead of deposit/withdrawal.
  const debit = Number(tx.debit ?? 0)
  const credit = Number(tx.credit ?? 0)
  if (debit || credit) return debit - credit
  const n = Number(tx.amount)
  return Number.isNaN(n) ? null : n
}

export function ledgerAmountFromModuleTx(tx: Record<string, unknown>): number | null {
  return hydrateDebitCredit(tx).amount
}

/** Stable voucher for Books→recon; synthesizes one for manual rows without id_number. */
export function ledgerVoucherFromModuleTx(tx: Record<string, unknown>): string {
  const direct = String(tx.voucher_no ?? tx.id_number ?? tx.invoice_number ?? '').trim()
  if (direct) return direct
  const date = normalizeReconDate(String(tx.date ?? ''))
  const amount = ledgerAmountFromModuleTx(tx)
  if (!date || amount == null) return ''
  const batch = String(tx.upload_batch_id ?? '').trim() || 'x'
  const party = String(tx.payee ?? tx.payer ?? '')
    .trim()
    .slice(0, 40)
  return `MANUAL-${batch}-${date}-${amount.toFixed(2)}-${party}`
}

export type KeyedUnreconciledRow = {
  id: string
  status: string
  key: string
}

/**
 * Unreconciled DB rows to delete so recon matches Books modules:
 * - key missing from modules
 * - duplicate keys (keep first, purge extras)
 * Empty moduleKeys means "modules not loaded" — never treat everything as orphan.
 */
export function selectUnreconciledOrphanIds(
  dbRows: KeyedUnreconciledRow[],
  moduleKeys: Set<string>,
): string[] {
  if (moduleKeys.size === 0) return []
  const orphans: string[] = []
  const keptByKey = new Map<string, string>()
  for (const row of dbRows) {
    const status = (row.status || '').toLowerCase()
    if (status !== 'unreconciled') continue
    if (!moduleKeys.has(row.key)) {
      orphans.push(row.id)
      continue
    }
    if (keptByKey.has(row.key)) {
      orphans.push(row.id)
      continue
    }
    keptByKey.set(row.key, row.id)
  }
  return orphans
}

/**
 * IDs to preserve across Books→recon sync wipe.
 * Includes matched/partial still in modules and the first unreconciled row per
 * module key (so cancel→unreconciled does not mint a new UUID on rematch).
 */
export function selectKeepIdsForModuleSync(
  dbRows: KeyedUnreconciledRow[],
  moduleKeys: Set<string>,
): string[] {
  const orphans = new Set(selectUnreconciledOrphanIds(dbRows, moduleKeys))
  return dbRows
    .filter(row => {
      if (!moduleKeys.has(row.key)) return false
      const status = (row.status || '').toLowerCase()
      if (status === 'matched' || status === 'partial') return true
      return status === 'unreconciled' && !orphans.has(row.id)
    })
    .map(row => row.id)
}
