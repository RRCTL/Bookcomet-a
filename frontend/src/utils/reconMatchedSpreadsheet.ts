import type { SpreadsheetRow } from '../components/EditableSpreadsheet'
import type { MatchedGroupRow } from '../components/ReconciliationTable'

/** Coerce `bank_txn_ids` / `ledger_txn_ids` from storage or API to `string[]`. */
export function normalizeReconTxnIdList(raw: unknown): string[] {
  if (raw == null) return []
  if (typeof raw === 'string') {
    const s = raw.trim()
    return s ? [s] : []
  }
  if (Array.isArray(raw)) {
    return raw.filter(x => x != null && String(x).trim()).map(x => String(x).trim())
  }
  return []
}

/** Spreadsheet / CSV header for the matched-group column (visible GL voucher when known). */
export const RECON_GROUP_COL_HEADER = 'GL / Group ID' as const

/** Human-readable group column: GL voucher when loaded, else short id (internal id stays in _recon_group_id). */
export function reconGroupSheetLabel(groupId: string, glVoucherByGroupId?: Record<string, string>): string {
  const v = glVoucherByGroupId?.[groupId]?.trim()
  if (v) return v
  if (groupId.length > 12) return `${groupId.slice(0, 8)}…`
  return groupId
}

/**
 * Stable key for grouping / API — always prefer UUID on manual_match rows (visible column may be GL-000006).
 */
export function reconMatchedRowGroupKey(r: SpreadsheetRow): string {
  const a = r as SpreadsheetRow & { _recon_group_id?: string }
  return String(a._recon_group_id ?? r['Group ID'] ?? '')
}

/** OCR 配對ID column: show GL-000006 when we know the draft voucher for that reconciliation group id. */
export function formatMatchedIdForDisplay(
  raw: string | undefined | null,
  glVoucherByGroupId?: Record<string, string>,
): string {
  const s = String(raw ?? '').trim()
  if (!s) return ''
  const vn = glVoucherByGroupId?.[s]?.trim()
  if (vn) return vn
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s)) {
    return `${s.slice(0, 8)}…`
  }
  return s
}

/** Headers for group-backed matched rows (auto/AI rows may add legacy keys). */
export const RECON_MATCHED_SHEET_COLUMNS = [
  RECON_GROUP_COL_HEADER,
  'Match Type',
  'Side',
  'Voucher',
  'Amount',
  '幣別',
  '日期',
  '匹配狀態',
  'Rule',
  'Bank Total (grp)',
  'AR_AP Total (grp)',
  'Difference (grp)',
] as const

export type ReconMemberTxnType = 'bank' | 'ledger'

function snapAmount(snap: any): number {
  if (!snap) return 0
  const a = snap.amount
  if (typeof a === 'number') return a
  return parseFloat(String(a || 0)) || 0
}

function snapCurrency(snap: any, fallback: string): string {
  return (snap?.currency || fallback || 'HKD').trim() || 'HKD'
}

function snapDate(snap: any): string {
  if (!snap) return ''
  return (
    String(snap.bank_date || snap.book_date || snap.row?.['日期'] || snap.row?.date || '')
  ).trim()
}

/** Build one spreadsheet row per transaction from reconciliation groups. */
export function matchedGroupsToSpreadsheetRows(
  groups: MatchedGroupRow[],
  glVoucherByGroupId?: Record<string, string>,
): SpreadsheetRow[] {
  const rows: SpreadsheetRow[] = []
  let no = 1

  for (const grp of groups) {
    const bankIds = normalizeReconTxnIdList(grp.bank_txn_ids)
    const ledgerIds = normalizeReconTxnIdList(grp.ledger_txn_ids)
    const gCur = grp.currency || 'HKD'
    const { bank_total: grpBankTotal, ledger_total: grpLedgerTotal, difference: grpDiff } = grp
    const groupCol = reconGroupSheetLabel(grp.id, glVoucherByGroupId)

    const pushRow = (partial: Omit<SpreadsheetRow, 'No.'>) => {
      rows.push({ ...partial, 'No.': no } as unknown as SpreadsheetRow)
      no += 1
    }

    if (grp.is_same_mode) {
      const bankSnaps = grp.bank_txn_snapshots || []
      const ledgerSnaps = grp.ledger_txn_snapshots || []
      let firstInGroup = true

      bankIds.forEach((id, i) => {
        const snap = bankSnaps[i] as any
        const mode = snap?.recordMode || 'BANK'
        pushRow({
          id: `${grp.id}-b-${id}`,
          'Group ID': groupCol,
          'Match Type': grp.match_cardinality,
          Side: mode === 'BANK' ? 'BANK' : String(mode),
          Voucher: String(grp.bank_vouchers[i] || snap?.reference || id).slice(0, 120),
          Amount: snapAmount(snap),
          幣別: snapCurrency(snap, gCur),
          日期: snapDate(snap),
          匹配狀態: 'MATCHED',
          Rule: grp.rule_hit,
          'Bank Total (grp)': firstInGroup ? grpBankTotal : '',
          'AR_AP Total (grp)': firstInGroup ? grpLedgerTotal : '',
          'Difference (grp)': firstInGroup ? grpDiff : '',
          source: 'manual_match',
          _recon_group_id: grp.id,
          _recon_txn_id: id,
          _recon_txn_type: 'bank' as ReconMemberTxnType,
          _recon_is_legacy: grp.is_legacy,
        })
        firstInGroup = false
      })

      ledgerIds.forEach((id, i) => {
        const snap = ledgerSnaps[i] as any
        const mode = snap?.recordMode || 'AR'
        pushRow({
          id: `${grp.id}-l-${id}`,
          [RECON_GROUP_COL_HEADER]: groupCol,
          'Match Type': grp.match_cardinality,
          Side: mode === 'BANK' ? 'BANK' : mode === 'AP' ? 'AP' : 'AR',
          Voucher: String(grp.ledger_vouchers[i] || snap?.reference || id).slice(0, 120),
          Amount: snapAmount(snap),
          幣別: snapCurrency(snap, gCur),
          日期: snapDate(snap),
          匹配狀態: 'MATCHED',
          Rule: grp.rule_hit,
          'Bank Total (grp)': firstInGroup ? grpBankTotal : '',
          'AR_AP Total (grp)': firstInGroup ? grpLedgerTotal : '',
          'Difference (grp)': firstInGroup ? grpDiff : '',
          source: 'manual_match',
          _recon_group_id: grp.id,
          _recon_txn_id: id,
          _recon_txn_type: 'ledger' as ReconMemberTxnType,
          _recon_is_legacy: grp.is_legacy,
        })
        firstInGroup = false
      })
      continue
    }

    // Cross-mode & ledger-pending: one row per bank txn, one per ledger txn
    let firstInGroup = true
    const nB = bankIds.length
    const nL = ledgerIds.length

    bankIds.forEach((id, i) => {
      const snap = grp.bank_txn_snapshots?.[i] as any
      const amt =
        snap != null
          ? snapAmount(snap)
          : nB > 0
            ? Number(grpBankTotal) / nB
            : 0
      pushRow({
        id: `${grp.id}-bank-${id}`,
        [RECON_GROUP_COL_HEADER]: groupCol,
        'Match Type': grp.match_cardinality,
        Side: 'BANK',
        Voucher: String(grp.bank_vouchers[i] || snap?.reference || id).slice(0, 120),
        Amount: amt,
        幣別: snap ? snapCurrency(snap, gCur) : gCur,
        日期: snapDate(snap),
        匹配狀態: 'MATCHED',
        Rule: grp.rule_hit,
        'Bank Total (grp)': firstInGroup ? grpBankTotal : '',
        'AR_AP Total (grp)': firstInGroup ? grpLedgerTotal : '',
        'Difference (grp)': firstInGroup ? grpDiff : '',
        source: 'manual_match',
        _recon_group_id: grp.id,
        _recon_txn_id: id,
        _recon_txn_type: 'bank',
        _recon_is_legacy: grp.is_legacy,
      })
      firstInGroup = false
    })

    ledgerIds.forEach((id, i) => {
      const snap = grp.ledger_txn_snapshots?.[i] as any
      const mode = snap?.recordMode === 'AP' ? 'AP' : 'AR'
      const amt =
        snap != null
          ? snapAmount(snap)
          : nL > 0
            ? Number(grpLedgerTotal) / nL
            : 0
      pushRow({
        id: `${grp.id}-ledger-${id}`,
        [RECON_GROUP_COL_HEADER]: groupCol,
        'Match Type': grp.match_cardinality,
        Side: mode,
        Voucher: String(grp.ledger_vouchers[i] || snap?.reference || id).slice(0, 120),
        Amount: amt,
        幣別: snap ? snapCurrency(snap, gCur) : gCur,
        日期: snapDate(snap),
        匹配狀態: 'MATCHED',
        Rule: grp.rule_hit,
        'Bank Total (grp)': firstInGroup && nB === 0 ? grpBankTotal : '',
        'AR_AP Total (grp)': firstInGroup ? grpLedgerTotal : '',
        'Difference (grp)': firstInGroup ? grpDiff : '',
        source: 'manual_match',
        _recon_group_id: grp.id,
        _recon_txn_id: id,
        _recon_txn_type: 'ledger',
        _recon_is_legacy: grp.is_legacy,
      })
      firstInGroup = false
    })
  }

  return rows
}

/** Rows from auto / AI match that are not backed by reconMatchedGroups. */
export function isReconNonGroupMatchedRow(r: SpreadsheetRow): boolean {
  const s = (r as any).source
  return s === 'reconciliation_auto_match' || s === 'ai_match'
}

/**
 * Drop preserved auto/AI sheet rows that reference a bank or ledger txn id already
 * represented in a manual multi-match group — avoids duplicate lines in Matched Results.
 */
export function filterPreservedMatchedRowsCoveredByGroups(
  preserved: SpreadsheetRow[],
  groups: MatchedGroupRow[],
): SpreadsheetRow[] {
  if (!groups.length) return preserved
  const groupBank = new Set<string>()
  const groupLedger = new Set<string>()
  for (const g of groups) {
    for (const id of normalizeReconTxnIdList(g.bank_txn_ids)) {
      if (id) groupBank.add(id)
    }
    for (const id of normalizeReconTxnIdList(g.ledger_txn_ids)) {
      if (id) groupLedger.add(id)
    }
  }
  if (groupBank.size === 0 && groupLedger.size === 0) return preserved
  return preserved.filter((r) => {
    const row = r as SpreadsheetRow & { bank_txn_id?: string; ledger_txn_id?: string }
    const bid = String(row.bank_txn_id ?? '').trim()
    const lid = String(row.ledger_txn_id ?? '').trim()
    if (lid && groupLedger.has(lid)) return false
    if (bid && groupBank.has(bid)) return false
    return true
  })
}

/** Stable key: one ledger/bank txn should not appear twice in Matched Results. */
function reconParticipantDedupeKey(r: SpreadsheetRow): string {
  const a = r as SpreadsheetRow & {
    _recon_txn_type?: string
    _recon_txn_id?: string
    ledger_txn_id?: string
    bank_txn_id?: string
  }
  const tt = a._recon_txn_type
  const tid = String(a._recon_txn_id ?? '').trim()
  if (tt && tid) return `${tt}:${tid}`
  const lid = String(a.ledger_txn_id ?? '').trim()
  if (lid) return `ledger:${lid}`
  const bid = String(a.bank_txn_id ?? '').trim()
  if (bid) return `bank:${bid}`
  return ''
}

/** Prefer full matches (e.g. N:1) over ledger-pending 0:n rows; prefer group-backed rows over orphan auto/AI. */
function matchTypePreferenceRank(mt: unknown): number {
  const s = String(mt ?? '')
  if (/^0:\d+$/i.test(s)) return 0
  return 1
}

function reconRowPreferenceRank(r: SpreadsheetRow): number {
  const a = r as { source?: string; _recon_group_id?: string }
  let base = matchTypePreferenceRank(r['Match Type'])
  if (a.source === 'manual_match' || Boolean(a._recon_group_id)) base += 2
  return base
}

function preferReconMatchedParticipantRow(a: SpreadsheetRow, b: SpreadsheetRow): SpreadsheetRow {
  const rb = reconRowPreferenceRank(b)
  const ra = reconRowPreferenceRank(a)
  if (rb !== ra) return rb > ra ? b : a
  return a
}

/**
 * Collapse duplicate participant lines (e.g. same ledger id in both an old 0:1 group and a new N:1 group,
 * or a non-group row that still carried the same txn id).
 */
export function dedupeReconMatchedSheetRowsByTxnParticipant(rows: SpreadsheetRow[]): SpreadsheetRow[] {
  const out: SpreadsheetRow[] = []
  const keyToIndex = new Map<string, number>()
  for (const r of rows) {
    const key = reconParticipantDedupeKey(r)
    if (!key) {
      out.push(r)
      continue
    }
    const idx = keyToIndex.get(key)
    if (idx === undefined) {
      keyToIndex.set(key, out.length)
      out.push(r)
    } else {
      out[idx] = preferReconMatchedParticipantRow(out[idx]!, r)
    }
  }
  return out
}

/**
 * Ledger-only 0:n groups that are strict subsets of a bank+ledger group (same tenant txn ids)
 * linger in the DB after upgrading to N:1 — drop them so Matched Results / GL don't duplicate.
 */
export function filterSubsumedLedgerPendingGroups(groups: MatchedGroupRow[]): MatchedGroupRow[] {
  const fullLedgerIds = new Set<string>()
  for (const g of groups) {
    const nb = normalizeReconTxnIdList(g.bank_txn_ids).length
    const nl = normalizeReconTxnIdList(g.ledger_txn_ids).length
    if (nb > 0 && nl > 0) {
      for (const id of normalizeReconTxnIdList(g.ledger_txn_ids)) fullLedgerIds.add(String(id))
    }
  }
  if (fullLedgerIds.size === 0) return groups

  return groups.filter((g) => {
    const nb = normalizeReconTxnIdList(g.bank_txn_ids).length
    const nl = normalizeReconTxnIdList(g.ledger_txn_ids).length
    if (nb > 0) return true
    if (nl === 0) return true
    const card = String(g.match_cardinality ?? '')
    if (!/^0:\d+$/i.test(card)) return true

    const ids = normalizeReconTxnIdList(g.ledger_txn_ids).map((id) => String(id))
    if (ids.length === 0) return true
    const allCovered = ids.every((id) => fullLedgerIds.has(id))
    return !allCovered
  })
}

/** DB is authoritative for group ids; enrich snapshots from local; drop local groups that overlap DB txns; subsumed 0:n prune. */
export function mergeReconGroupsFromApiAndLocal(
  dbGroups: MatchedGroupRow[],
  prev: MatchedGroupRow[],
): MatchedGroupRow[] {
  const dbIds = new Set(dbGroups.map((g) => g.id))
  const prevById = new Map(prev.map((g) => [g.id, g]))

  const enrichedDb = dbGroups.map((g) => {
    const local = prevById.get(g.id)
    if (!local) return g
    const preferLocalBank =
      (local.bank_txn_snapshots?.length ?? 0) > (g.bank_txn_snapshots?.length ?? 0)
    const preferLocalLedger =
      (local.ledger_txn_snapshots?.length ?? 0) > (g.ledger_txn_snapshots?.length ?? 0)
    return {
      ...g,
      bank_txn_snapshots: preferLocalBank ? local.bank_txn_snapshots : g.bank_txn_snapshots,
      ledger_txn_snapshots: preferLocalLedger ? local.ledger_txn_snapshots : g.ledger_txn_snapshots,
    }
  })

  const dbTxnIds = new Set<string>()
  for (const g of enrichedDb) {
    for (const id of normalizeReconTxnIdList(g.bank_txn_ids)) dbTxnIds.add(String(id))
    for (const id of normalizeReconTxnIdList(g.ledger_txn_ids)) dbTxnIds.add(String(id))
  }

  const prevOnly = prev.filter((g) => {
    if (dbIds.has(g.id)) return false
    const ids = [
      ...normalizeReconTxnIdList(g.bank_txn_ids),
      ...normalizeReconTxnIdList(g.ledger_txn_ids),
    ].map(String)
    if (ids.some((id) => dbTxnIds.has(id))) return false
    return true
  })

  return filterSubsumedLedgerPendingGroups([...enrichedDb, ...prevOnly])
}

/** Merge group-derived rows with preserved non-group rows; renumber No. */
export function mergeReconMatchedSheetRows(
  fromGroups: SpreadsheetRow[],
  preserved: SpreadsheetRow[],
): SpreadsheetRow[] {
  const merged = [...fromGroups, ...preserved]
  const deduped = dedupeReconMatchedSheetRowsByTxnParticipant(merged)
  return deduped.map((r, i) => ({ ...r, 'No.': i + 1 }))
}
