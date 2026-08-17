import { reconciliationApi } from '../../services/reconciliation'
import type { BankTransaction } from '../../types/reconciliation'

function bankDedupKey(date: string, amount: number, account: string): string {
  return `${date.slice(0, 10)}|${amount.toFixed(2)}|${account.trim().toLowerCase()}`
}

/** Import selected Books bank rows into bank_transactions for RECON matching. */
export async function importScopedBankForRecon(
  existing: BankTransaction[],
  rows: Record<string, unknown>[],
): Promise<number> {
  if (!rows.length) return 0

  const seen = new Set(
    existing.map(t =>
      bankDedupKey(String(t.bank_date ?? ''), Number(t.amount ?? 0), String(t.account_id ?? '')),
    ),
  )

  const pending: Record<string, unknown>[] = []
  for (const tx of rows) {
    const dep = Number(tx.deposit ?? 0)
    const wit = Number(tx.withdrawal ?? 0)
    const amount = dep || wit ? dep - wit : Number(tx.amount ?? 0)
    const date = String(tx.date ?? tx.transaction_date ?? '').trim()
    const account = String(tx.account_type ?? tx.bank ?? '').trim() || 'UNKNOWN'
    if (!date || Number.isNaN(amount)) continue
    const key = bankDedupKey(date, amount, account)
    if (seen.has(key)) continue
    seen.add(key)
    pending.push({
      date,
      amount,
      currency: tx.currency ?? 'HKD',
      account_id: account,
      description: tx.particulars ?? tx.description ?? tx.description_raw ?? '',
      reference: tx.id_number ?? tx.reference ?? '',
      client_row_id: String(tx.id_number ?? tx.id ?? ''),
      account_category: tx.account_code ?? tx.category ?? tx.account_category ?? '',
    })
  }

  if (!pending.length) return 0
  const res = await reconciliationApi.importBankTransactions(pending)
  return res.stored_count ?? 0
}
