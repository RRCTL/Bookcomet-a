import type { SpreadsheetRow } from '../../components/EditableSpreadsheet'
import { spreadsheetRowsToArapTransactions } from '../workspace/buildSpreadsheetFromOcrResult'
import {
  buildBatchTablePayloadsFromRun,
  loadAllBatchTablePayloads,
} from '../nodeWorkspace/batchTableSnapshots'
import { workflowApi } from '../nodeWorkspace/workflowApi'
import { reconciliationApi } from '../../services/reconciliation'
import type { LedgerTransaction } from '../../types/reconciliation'
import { hydrateDebitCredit } from '../erpShell/arapDebitCredit'
import { ledgerAmountFromModuleTx, ledgerVoucherFromModuleTx } from './moduleReconKeys'

const INCLUDED_STATUSES = new Set(['coa_running', 'completed', 'done', 'saved'])

function dedupKey(module: string, voucher: string, date: string, amount: number): string {
  return `${module.toUpperCase()}|${voucher}|${date.slice(0, 10)}|${amount.toFixed(2)}`
}

function arapRowsFromPayload(
  payload: Record<string, unknown>,
  mode: 'AP' | 'AR',
): Record<string, unknown>[] {
  const direct = (payload.arapTransactions as Record<string, unknown>[]) ?? []
  if (direct.length) return direct
  const sheet = (payload.spreadsheetData as SpreadsheetRow[] | undefined) ?? []
  if (!sheet.length) return []
  return spreadsheetRowsToArapTransactions(sheet, mode) as unknown as Record<string, unknown>[]
}

export type SyncBooksLedgerOptions = {
  modes?: ('AP' | 'AR')[]
  /** When set, only import rows whose voucher / id_number matches one of these refs. */
  onlyVoucherRefs?: string[]
}

function rowMatchesVoucherRef(
  tx: Record<string, unknown>,
  refs: Set<string>,
): boolean {
  const voucher = ledgerVoucherFromModuleTx(tx).toLowerCase()
  const idNum = String(tx.id_number ?? tx.id ?? '').trim().toLowerCase()
  if (refs.has(voucher) || refs.has(idNum)) return true
  for (const r of refs) {
    if (!r) continue
    if (voucher && (voucher.includes(r) || r.includes(voucher))) return true
    if (idNum && (idNum.includes(r) || r.includes(idNum))) return true
  }
  return false
}

/** Import approved Books AP/AR rows missing from ledger_transactions (RECON DB source). */
export async function syncBooksLedgerForRecon(
  companyId: string,
  existing: LedgerTransaction[],
  options?: SyncBooksLedgerOptions,
): Promise<number> {
  const modes = options?.modes?.length ? options.modes : (['AP', 'AR'] as const)
  const onlyRefs = options?.onlyVoucherRefs?.length
    ? new Set(options.onlyVoucherRefs.map(r => r.trim().toLowerCase()).filter(Boolean))
    : null
  const seen = new Set(
    existing.map(t =>
      dedupKey(t.module ?? '', t.doc_id ?? '', t.book_date ?? '', Number(t.amount ?? 0)),
    ),
  )

  const pending: Record<'AP' | 'AR', Record<string, unknown>[]> = { AP: [], AR: [] }
  const runs = (await workflowApi.listRuns(companyId)).filter(r => !r.processing_removed_at)

  for (const mode of modes) {
    const candidates = runs.filter(
      r => r.processing_mode === mode && INCLUDED_STATUSES.has(r.run_status.toLowerCase()),
    )
    for (const summary of candidates) {
      const full = await workflowApi.getRun(companyId, summary.id)
      let loaded = await loadAllBatchTablePayloads(full, companyId)
      if (Object.keys(loaded).length === 0) loaded = buildBatchTablePayloadsFromRun(full)
      for (const payload of Object.values(loaded)) {
        for (const tx of arapRowsFromPayload(payload, mode)) {
          if (onlyRefs && !rowMatchesVoucherRef(tx, onlyRefs)) continue
          const voucher = ledgerVoucherFromModuleTx(tx)
          const date = String(tx.date ?? '').trim()
          const amount = ledgerAmountFromModuleTx(tx)
          if (!voucher || !date || amount == null) continue
          const key = dedupKey(mode, voucher, date, amount)
          if (seen.has(key)) continue
          seen.add(key)
          const sides = hydrateDebitCredit(tx, mode)
          pending[mode].push({
            voucher_no: voucher,
            transaction_type: String(tx.transaction_type ?? mode),
            amount,
            dr_cr: sides.dr_cr,
            currency: tx.currency ?? 'HKD',
            date: tx.date,
            payer: tx.payer,
            payee: tx.payee,
            bank: tx.bank,
            memo: tx.memo,
            category: tx.category ?? tx.account_code,
            client_row_id: String(tx.id_number ?? tx.id ?? voucher),
          })
        }
      }
    }
  }

  let imported = 0
  for (const mode of modes) {
    if (!pending[mode].length) continue
    const res = await reconciliationApi.importLedgerTransactions(pending[mode], undefined, mode)
    imported += res.stored_count ?? 0
  }
  return imported
}
