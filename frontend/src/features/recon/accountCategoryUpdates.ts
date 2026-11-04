export type AccountCategoryBulkUpdate = {
  source: 'bank' | 'ledger'
  txn_id: string
  account_category: string
}

export type ModuleRowForAccountSync = {
  key: string
  account_code?: string | null
  locked?: boolean
}

export type ExistingTxnForAccountSync = {
  id: string
  key: string
  account_category?: string | null
}

/**
 * Build account-category bulk updates for module rows that already exist in recon/GL.
 * Prefers `account_code` (CoA code). Skips reconciled, posted, missing, and unchanged rows.
 */
export function accountCategoryUpdatesForExistingRows(args: {
  source: 'bank' | 'ledger'
  moduleRows: ModuleRowForAccountSync[]
  dbRows: ExistingTxnForAccountSync[]
  postedIds?: ReadonlySet<string>
}): AccountCategoryBulkUpdate[] {
  const dbByKey = new Map<string, ExistingTxnForAccountSync>()
  for (const row of args.dbRows) {
    const id = String(row.id ?? '').trim()
    const key = String(row.key ?? '').trim()
    if (!id || !key || dbByKey.has(key)) continue
    dbByKey.set(key, { ...row, id, key })
  }

  const posted = args.postedIds ?? new Set<string>()
  const seen = new Set<string>()
  const out: AccountCategoryBulkUpdate[] = []
  for (const row of args.moduleRows) {
    if (row.locked) continue
    const db = dbByKey.get(row.key)
    if (!db) continue
    const tid = db.id
    if (!tid || seen.has(tid) || posted.has(tid)) continue
    seen.add(tid)
    const next = String(row.account_code ?? '').trim()
    const was = String(db.account_category ?? '').trim()
    if (next === was) continue
    out.push({ source: args.source, txn_id: tid, account_category: next })
  }
  return out
}
