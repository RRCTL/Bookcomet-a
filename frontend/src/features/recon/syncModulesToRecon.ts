import {
  buildBatchTablePayloadsFromRun,
  loadAllBatchTablePayloads,
  persistBatchTableSnapshot,
  frozenPresetForBatch,
} from '../nodeWorkspace/batchTableSnapshots'
import { workflowApi, type WorkflowRun } from '../nodeWorkspace/workflowApi'
import { reconciliationApi } from '../../services/reconciliation'
import type { BankTransaction, LedgerTransaction } from '../../types/reconciliation'
import { hydrateDebitCredit } from '../erpShell/arapDebitCredit'
import { coalesceBankAccountTypeRows } from '../../utils/bankAccountTypeCoalesce'
import {
  bankAmountFromModuleTx,
  bankReconDedupKey,
  ledgerAmountFromModuleTx,
  ledgerReconDedupKey,
  ledgerVoucherFromModuleTx,
  normalizeReconDate,
  selectKeepIdsForModuleSync,
} from './moduleReconKeys'

const INCLUDED_STATUSES = new Set(['coa_running', 'completed', 'done', 'saved'])

type ModuleBankRow = {
  key: string
  importRow: Record<string, unknown>
  run: WorkflowRun
  batchId: string
  tx: Record<string, unknown>
}

type ModuleLedgerRow = {
  key: string
  mode: 'AP' | 'AR'
  importRow: Record<string, unknown>
  run: WorkflowRun
  batchId: string
  tx: Record<string, unknown>
}

/** Same source as Books module grids — arapTransactions only (no spreadsheet fallback). */
function arapRowsFromPayload(payload: Record<string, unknown>): Record<string, unknown>[] {
  return (payload.arapTransactions as Record<string, unknown>[]) ?? []
}

async function loadModeRuns(companyId: string, mode: string): Promise<WorkflowRun[]> {
  const all = (await workflowApi.listRuns(companyId)).filter(r => !r.processing_removed_at)
  const summaries = all.filter(
    r =>
      (r.processing_mode || '').toUpperCase() === mode &&
      INCLUDED_STATUSES.has(r.run_status.toLowerCase()),
  )
  const out: WorkflowRun[] = []
  for (const s of summaries) {
    out.push(await workflowApi.getRun(companyId, s.id))
  }
  return out
}

async function collectModuleBankRows(companyId: string): Promise<ModuleBankRow[]> {
  const runs = await loadModeRuns(companyId, 'BANK')
  const out: ModuleBankRow[] = []
  for (const run of runs) {
    let loaded = await loadAllBatchTablePayloads(run, companyId)
    if (Object.keys(loaded).length === 0) loaded = buildBatchTablePayloadsFromRun(run)
    for (const [batchId, payload] of Object.entries(loaded)) {
      const raw = (payload.bankTransactions as Record<string, unknown>[] | undefined) ?? []
      const rows = coalesceBankAccountTypeRows(raw.map(t => ({ ...t }))) as Record<string, unknown>[]
      for (const tx of rows) {
        const amount = bankAmountFromModuleTx(tx)
        const date = normalizeReconDate(String(tx.date ?? tx.transaction_date ?? ''))
        const account = String(tx.account_type ?? tx.bank ?? '').trim() || 'UNKNOWN'
        if (!date || amount == null) continue
        const key = bankReconDedupKey(date, amount, account)
        out.push({
          key,
          run,
          batchId,
          tx,
          importRow: {
            date,
            amount,
            currency: tx.currency ?? 'HKD',
            account_id: account,
            description: tx.particulars ?? tx.description ?? tx.description_raw ?? '',
            reference: tx.id_number ?? tx.reference ?? '',
            client_row_id: String(tx.id_number ?? tx.id ?? key),
            account_category: tx.account_code ?? tx.category ?? tx.account_category ?? '',
          },
        })
      }
    }
  }
  return out
}

async function collectModuleLedgerRows(companyId: string): Promise<ModuleLedgerRow[]> {
  const out: ModuleLedgerRow[] = []
  for (const mode of ['AP', 'AR'] as const) {
    const runs = await loadModeRuns(companyId, mode)
    for (const run of runs) {
      let loaded = await loadAllBatchTablePayloads(run, companyId)
      if (Object.keys(loaded).length === 0) loaded = buildBatchTablePayloadsFromRun(run)
      for (const [batchId, payload] of Object.entries(loaded)) {
        for (const tx of arapRowsFromPayload(payload)) {
          const voucher = ledgerVoucherFromModuleTx(tx)
          const date = normalizeReconDate(String(tx.date ?? ''))
          const amount = ledgerAmountFromModuleTx(tx)
          if (!voucher || !date || amount == null) continue
          const key = ledgerReconDedupKey(mode, voucher, date, amount)
          const sides = hydrateDebitCredit(tx, mode)
          out.push({
            key,
            mode,
            run,
            batchId,
            tx,
            importRow: {
              voucher_no: voucher,
              transaction_type: String(tx.transaction_type ?? mode),
              amount,
              dr_cr: sides.dr_cr,
              currency: tx.currency ?? 'HKD',
              date,
              payer: tx.payer,
              payee: tx.payee,
              bank: tx.bank,
              memo: tx.memo,
              // Prefer account_code (CoA code); category holds the display name.
              category: tx.account_code ?? tx.category,
              client_row_id: String(tx.id_number ?? tx.id ?? voucher),
            },
          })
        }
      }
    }
  }
  return out
}

function bankDbKey(t: BankTransaction): string {
  return bankReconDedupKey(
    String(t.bank_date ?? ''),
    Number(t.amount ?? 0),
    String(t.account_id ?? ''),
  )
}

function ledgerDbKey(t: LedgerTransaction): string {
  return ledgerReconDedupKey(
    String(t.module ?? ''),
    String(t.doc_id ?? ''),
    String(t.book_date ?? ''),
    Number(t.amount ?? 0),
  )
}

async function writeMatchedIdToModules(
  companyId: string,
  bankModule: ModuleBankRow[],
  ledgerModule: ModuleLedgerRow[],
  bankDb: BankTransaction[],
  ledgerDb: LedgerTransaction[],
): Promise<void> {
  const bankStatusByKey = new Map<string, string>()
  for (const t of bankDb) {
    const st = String(t.status ?? '').toLowerCase()
    if (st === 'matched' || st === 'partial') bankStatusByKey.set(bankDbKey(t), t.id)
  }
  const ledgerStatusByKey = new Map<string, string>()
  for (const t of ledgerDb) {
    const st = String(t.status ?? '').toLowerCase()
    if (st === 'matched' || st === 'partial') ledgerStatusByKey.set(ledgerDbKey(t), t.id)
  }

  type BatchRef = { run: WorkflowRun; batchId: string; mode: 'BANK' | 'AP' | 'AR' }
  const batches = new Map<string, BatchRef>()
  for (const row of bankModule) {
    batches.set(`${row.run.id}::${row.batchId}`, { run: row.run, batchId: row.batchId, mode: 'BANK' })
  }
  for (const row of ledgerModule) {
    batches.set(`${row.run.id}::${row.batchId}`, {
      run: row.run,
      batchId: row.batchId,
      mode: row.mode,
    })
  }

  for (const edit of batches.values()) {
    let loaded = await loadAllBatchTablePayloads(edit.run, companyId)
    if (Object.keys(loaded).length === 0) loaded = buildBatchTablePayloadsFromRun(edit.run)
    const payload = { ...(loaded[edit.batchId] ?? {}) }
    let changed = false
    if (edit.mode === 'BANK') {
      const txs = ((payload.bankTransactions as Record<string, unknown>[]) ?? []).map(tx => {
        const amount = bankAmountFromModuleTx(tx)
        const date = normalizeReconDate(String(tx.date ?? tx.transaction_date ?? ''))
        const account = String(tx.account_type ?? tx.bank ?? '').trim() || 'UNKNOWN'
        if (!date || amount == null) return tx
        const key = bankReconDedupKey(date, amount, account)
        const nextMatched = bankStatusByKey.get(key) ?? ''
        if (String(tx.matched_id ?? '') === nextMatched) return tx
        changed = true
        return { ...tx, matched_id: nextMatched }
      })
      payload.bankTransactions = txs
    } else {
      const txs = arapRowsFromPayload(payload).map(tx => {
        const voucher = ledgerVoucherFromModuleTx(tx)
        const date = normalizeReconDate(String(tx.date ?? ''))
        const amount = ledgerAmountFromModuleTx(tx)
        if (!voucher || !date || amount == null) return tx
        const key = ledgerReconDedupKey(edit.mode, voucher, date, amount)
        const nextMatched = ledgerStatusByKey.get(key) ?? ''
        if (String(tx.matched_id ?? '') === nextMatched) return tx
        changed = true
        const next = { ...tx, matched_id: nextMatched }
        if (nextMatched) next.payment_status = 'Reconciled'
        else if (String(tx.payment_status ?? '') === 'Reconciled') next.payment_status = ''
        return next
      })
      payload.arapTransactions = txs
    }
    if (!changed) continue
    await persistBatchTableSnapshot(
      edit.run,
      edit.batchId,
      payload,
      frozenPresetForBatch(edit.run, edit.batchId),
      companyId,
      { fromModule: true },
    )
  }
}

export type SyncModulesToReconResult = {
  importedBank: number
  importedLedger: number
  purgedBank: number
  purgedLedger: number
}

type PoolWipeResult = { purged_bank: number; purged_ledger: number }

/**
 * Prefer full rebuild (any-status purge). Fall back so an old backend / missing
 * endpoint does not block importing Books rows into the panels.
 */
async function wipePoolForRebuild(
  keepBankIds: string[],
  keepLedgerIds: string[],
): Promise<{ wipe: PoolWipeResult; skipKeysFromAllDb: boolean }> {
  try {
    const wipe = await reconciliationApi.purgeExceptKept({
      keep_bank_txn_ids: keepBankIds,
      keep_ledger_txn_ids: keepLedgerIds,
    })
    return { wipe, skipKeysFromAllDb: false }
  } catch (err) {
    console.warn('[RECON] purge-except-kept failed; trying clear-unreconciled-pool:', err)
  }
  try {
    const wipe = await reconciliationApi.clearUnreconciledPool({ bank: true, ledger: true })
    return { wipe, skipKeysFromAllDb: false }
  } catch (err) {
    console.warn('[RECON] clear-unreconciled-pool failed; import-only (no wipe):', err)
  }
  return { wipe: { purged_bank: 0, purged_ledger: 0 }, skipKeysFromAllDb: true }
}

/**
 * Make recon DB mirror Books modules (source of truth for which rows exist):
 * keep matched/partial and non-orphan unreconciled rows that still exist in modules
 * (preserves IDs after cancel→unreconciled so rematch works); permanently delete
 * unreconciled orphans/duplicates + matched orphans; re-import missing module rows.
 */
export async function syncModulesToRecon(companyId: string): Promise<SyncModulesToReconResult> {
  const [bankModule, ledgerModule, bankDbBefore, ledgerDbBefore] = await Promise.all([
    collectModuleBankRows(companyId),
    collectModuleLedgerRows(companyId),
    reconciliationApi.getBankTransactions() as Promise<BankTransaction[]>,
    reconciliationApi.getLedgerTransactions() as Promise<LedgerTransaction[]>,
  ])

  const moduleBankKeys = new Set(bankModule.map(r => r.key))
  const moduleLedgerKeys = new Set(ledgerModule.map(r => r.key))

  const keepBankIds = selectKeepIdsForModuleSync(
    bankDbBefore.map(t => ({ id: t.id, status: String(t.status ?? ''), key: bankDbKey(t) })),
    moduleBankKeys,
  )
  const keepLedgerIds = selectKeepIdsForModuleSync(
    ledgerDbBefore.map(t => ({ id: t.id, status: String(t.status ?? ''), key: ledgerDbKey(t) })),
    moduleLedgerKeys,
  )

  const { wipe, skipKeysFromAllDb } = await wipePoolForRebuild(keepBankIds, keepLedgerIds)
  const purgedBank = wipe.purged_bank ?? 0
  const purgedLedger = wipe.purged_ledger ?? 0

  // After a successful wipe, skip only kept matched keys. If wipe APIs are unavailable,
  // skip every existing DB key so we still import missing module rows.
  const keepBankKeys = new Set(
    skipKeysFromAllDb
      ? bankDbBefore.map(bankDbKey)
      : bankDbBefore.filter(t => keepBankIds.includes(t.id)).map(bankDbKey),
  )
  const keepLedgerKeys = new Set(
    skipKeysFromAllDb
      ? ledgerDbBefore.map(ledgerDbKey)
      : ledgerDbBefore.filter(t => keepLedgerIds.includes(t.id)).map(ledgerDbKey),
  )

  const seenBank = new Set<string>()
  const pendingBank = bankModule
    .filter(r => {
      if (keepBankKeys.has(r.key) || seenBank.has(r.key)) return false
      seenBank.add(r.key)
      return true
    })
    .map(r => r.importRow)

  let importedBank = 0
  if (pendingBank.length) {
    const res = await reconciliationApi.importBankTransactions(pendingBank)
    importedBank = res.stored_count ?? 0
  }

  const pendingByMode: Record<'AP' | 'AR', Record<string, unknown>[]> = { AP: [], AR: [] }
  const queuedLedger = new Set<string>()
  for (const row of ledgerModule) {
    if (keepLedgerKeys.has(row.key)) continue
    const qk = `${row.mode}|${row.key}`
    if (queuedLedger.has(qk)) continue
    queuedLedger.add(qk)
    pendingByMode[row.mode].push(row.importRow)
  }
  let importedLedger = 0
  for (const mode of ['AP', 'AR'] as const) {
    if (!pendingByMode[mode].length) continue
    const res = await reconciliationApi.importLedgerTransactions(pendingByMode[mode], undefined, mode)
    importedLedger += (res.stored_count ?? 0) + (res.updated_count ?? 0)
  }

  const [bankDb, ledgerDb] = await Promise.all([
    reconciliationApi.getBankTransactions() as Promise<BankTransaction[]>,
    reconciliationApi.getLedgerTransactions() as Promise<LedgerTransaction[]>,
  ])

  try {
    await writeMatchedIdToModules(companyId, bankModule, ledgerModule, bankDb, ledgerDb)
  } catch (err) {
    console.warn('[RECON] matched_id write-back failed:', err)
  }

  return { importedBank, importedLedger, purgedBank, purgedLedger }
}
