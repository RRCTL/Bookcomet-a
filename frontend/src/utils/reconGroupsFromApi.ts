import type { MatchedGroupRow } from '../components/ReconciliationTable'
import { normalizeReconTxnIdList } from './reconMatchedSpreadsheet'

/** Map GET /reconciliation/groups items to UI `MatchedGroupRow`. */
export function mapApiReconciliationGroupsToMatched(groups: any[]): MatchedGroupRow[] {
  return groups.map((g: any) => {
    const bank_txn_ids = normalizeReconTxnIdList(g.bank_txn_ids)
    const ledger_txn_ids = normalizeReconTxnIdList(g.ledger_txn_ids)
    return {
      id: g.id,
      match_cardinality: g.match_cardinality,
      bank_txn_ids,
      ledger_txn_ids,
      bank_vouchers: (g.bank_txns ?? []).map((t: any) => t.reference || t.id.slice(0, 8)),
      ledger_vouchers: (g.ledger_txns ?? []).map((t: any) => t.reference || t.id.slice(0, 8)),
      bank_total: g.total_bank_amount,
      ledger_total: g.total_ledger_amount,
      difference: g.difference,
      confidence: null,
      rule_hit: 'manual',
      is_same_mode: g.is_same_mode,
      is_legacy: false,
      bank_txn_snapshots: g.bank_txns ?? [],
      ledger_txn_snapshots: g.ledger_txns ?? [],
      currency: g.bank_txns?.[0]?.currency || g.ledger_txns?.[0]?.currency,
      created_at: typeof g.created_at === 'string' ? g.created_at : null,
    }
  })
}
