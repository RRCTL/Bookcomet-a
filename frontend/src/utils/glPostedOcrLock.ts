import type { MatchedGroupRow } from '../components/ReconciliationTable'

function addKey(set: Set<string>, v: unknown) {
  const s = String(v ?? '').trim()
  if (s) set.add(s)
}

/** All string keys that identify a bank row tied to a POSTED primary GL for its match group. */
export function buildGlPostedBankLockKeys(
  groups: MatchedGroupRow[],
  statusByGroupId: Record<string, string>,
): Set<string> {
  const keys = new Set<string>()
  for (const g of groups) {
    if (statusByGroupId[g.id] !== 'posted') continue
    for (const id of g.bank_txn_ids ?? []) addKey(keys, id)
    for (const v of g.bank_vouchers ?? []) addKey(keys, v)
    for (const snap of g.bank_txn_snapshots ?? []) {
      const o = snap as Record<string, unknown>
      addKey(keys, o.id_number)
      addKey(keys, o.reference)
      addKey(keys, o.voucher_no)
      addKey(keys, (o as { bank_txn_id?: string }).bank_txn_id)
      addKey(keys, (o as { db_id?: string }).db_id)
      const br = o.row as Record<string, unknown> | undefined
      if (br && typeof br === 'object') {
        addKey(keys, br.id_number)
        addKey(keys, br['憑證號'])
        addKey(keys, br.reference)
        addKey(keys, br.voucher_no)
      }
    }
  }
  return keys
}

export function buildGlPostedLedgerLockKeys(
  groups: MatchedGroupRow[],
  statusByGroupId: Record<string, string>,
): Set<string> {
  const keys = new Set<string>()
  for (const g of groups) {
    if (statusByGroupId[g.id] !== 'posted') continue
    for (const id of g.ledger_txn_ids ?? []) addKey(keys, id)
    for (const v of g.ledger_vouchers ?? []) addKey(keys, v)
    for (const snap of g.ledger_txn_snapshots ?? []) {
      const o = snap as Record<string, unknown>
      addKey(keys, o.id_number)
      addKey(keys, o.reference)
      addKey(keys, o.voucher_no)
      addKey(keys, o.doc_id)
      addKey(keys, (o as { ledger_txn_id?: string }).ledger_txn_id)
      addKey(keys, (o as { db_id?: string }).db_id)
      const lr = o.row as Record<string, unknown> | undefined
      if (lr && typeof lr === 'object') {
        addKey(keys, lr.id_number)
        addKey(keys, lr['憑證號'])
        addKey(keys, lr.reference)
        addKey(keys, lr.voucher_no)
      }
    }
  }
  return keys
}

export function isBankRowGlPosted(row: any, keys: ReadonlySet<string>): boolean {
  if (!keys.size) return false
  const candidates = [
    row?.db_id,
    row?.bank_txn_id,
    row?.id_number,
    row?.reference,
    row?.voucher_no,
  ]
    .map((x) => String(x ?? '').trim())
    .filter(Boolean)
  return candidates.some((c) => keys.has(c))
}

export function isLedgerRowGlPosted(row: any, keys: ReadonlySet<string>): boolean {
  if (!keys.size) return false
  const candidates = [
    row?.db_id,
    row?.ledger_txn_id,
    row?.id_number,
    row?.reference,
    row?.voucher_no,
    row?.doc_id,
  ]
    .map((x) => String(x ?? '').trim())
    .filter(Boolean)
  const normalized = candidates.flatMap((c) => {
    const out = [c]
    const stripped = c.replace(/^(AR|AP)-/i, '')
    if (stripped !== c) out.push(stripped)
    return out
  })
  return normalized.some((c) => keys.has(c))
}
