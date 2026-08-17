import type { MatchedGroupRow, PartialTransaction } from '../components/ReconciliationTable'
import type { ReconciliationStats } from '../types/reconciliation'
import { reconGroupSheetLabel, RECON_GROUP_COL_HEADER } from './reconMatchedSpreadsheet'

// ─── Types ──────────────────────────────────────────────────────────────────

export interface ExportFilters {
  dateFrom: string   // YYYY-MM-DD or ''
  dateTo: string     // YYYY-MM-DD or ''
  matchType: 'all' | 'auto' | 'manual'
}

export interface ExportSummaryStats {
  total_bank_txns: number
  total_ledger_txns: number
  matched: number
  unmatched_bank: number
  unmatched_ledger: number
  match_rate: number
}

// ─── Helpers ────────────────────────────────────────────────────────────────

/** Quote a CSV cell value, escaping internal double-quotes. */
function q(v: string | number | null | undefined): string {
  const s = v == null ? '' : String(v)
  return `"${s.replace(/"/g, '""')}"`
}

/** Format a date string (ISO or date-only) to YYYY-MM-DD, or return '' if falsy. */
function fmtDate(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toISOString().slice(0, 10)
}

/** Format a number to 2 decimal places. */
function fmtAmt(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return ''
  return n.toFixed(2)
}

/** Today's date as YYYY-MM-DD. */
export function todayISO(): string {
  return new Date().toISOString().slice(0, 10)
}

// ─── Filtering ───────────────────────────────────────────────────────────────

function inDateRange(dateStr: string | null | undefined, from: string, to: string): boolean {
  if (!from && !to) return true
  const d = fmtDate(dateStr)
  if (!d) return true
  if (from && d < from) return false
  if (to && d > to) return false
  return true
}

function matchesMatchType(ruleHit: string, confidence: number | null, filter: 'all' | 'auto' | 'manual'): boolean {
  if (filter === 'all') return true
  if (filter === 'manual') return confidence === null || ruleHit === 'manual'
  // 'auto' = any rule-based hit
  return confidence !== null && ruleHit !== 'manual'
}

export interface FilteredExportData {
  groups: MatchedGroupRow[]
  partials: PartialTransaction[]
  unmatchedBank: any[]
  unmatchedLedger: any[]
}

/** Apply date range + match type filters to export data. */
export function applyExportFilters(
  groups: MatchedGroupRow[],
  partials: PartialTransaction[],
  unmatchedBank: any[],
  unmatchedLedger: any[],
  filters: ExportFilters
): FilteredExportData {
  const { dateFrom, dateTo, matchType } = filters

  const filteredGroups = groups.filter(g => {
    if (!matchesMatchType(g.rule_hit, g.confidence, matchType)) return false
    // Date filter: use the group's first bank voucher date if available — fall back to always include
    return true
  })

  const filteredPartials = partials.filter(pt =>
    inDateRange(pt.bank_date, dateFrom, dateTo)
  )

  const filteredBank = unmatchedBank.filter(t =>
    inDateRange(t.bank_date ?? t.date, dateFrom, dateTo)
  )

  const filteredLedger = unmatchedLedger.filter(t =>
    inDateRange(t.book_date ?? t.date, dateFrom, dateTo)
  )

  return { groups: filteredGroups, partials: filteredPartials, unmatchedBank: filteredBank, unmatchedLedger: filteredLedger }
}

// ─── CSV builder ─────────────────────────────────────────────────────────────

/**
 * Build a full reconciliation report CSV string.
 *
 * Structure:
 *   Header / summary block
 *   === MATCHED TRANSACTIONS ===
 *   === PARTIAL MATCHES ===
 *   === UNMATCHED BANK TRANSACTIONS ===
 *   === UNMATCHED AR/AP TRANSACTIONS ===
 */
export function buildReconCsv(
  groups: MatchedGroupRow[],
  partials: PartialTransaction[],
  unmatchedBank: any[],
  unmatchedLedger: any[],
  stats: ExportSummaryStats | ReconciliationStats | null,
  filters: ExportFilters,
  glVoucherByGroupId?: Record<string, string>,
): string {
  const lines: string[] = []

  // ── Report header ──────────────────────────────────────────────────────────
  lines.push('Reconciliation Report')
  lines.push(`Generated,${todayISO()}`)

  if (filters.dateFrom || filters.dateTo) {
    lines.push(`Period Filter,${filters.dateFrom || 'start'} ~ ${filters.dateTo || 'end'}`)
  }
  if (filters.matchType !== 'all') {
    lines.push(`Match Type Filter,${filters.matchType}`)
  }

  if (stats) {
    lines.push(`Match Rate,${stats.match_rate}%`)
    lines.push(
      `Total Bank,${stats.total_bank_txns},Total Ledger,${stats.total_ledger_txns},` +
      `Matched,${stats.matched},Unmatched Bank,${stats.unmatched_bank},Unmatched Ledger,${stats.unmatched_ledger}`
    )
  } else {
    lines.push(`Matched Groups,${groups.length},Partial Matches,${partials.length},Unmatched Bank,${unmatchedBank.length},Unmatched Ledger,${unmatchedLedger.length}`)
  }

  lines.push('')

  // ── Matched groups ─────────────────────────────────────────────────────────
  lines.push(`=== MATCHED TRANSACTIONS (${groups.length}) ===`)
  lines.push([
    q(RECON_GROUP_COL_HEADER), q('Cardinality'),
    q('Bank Voucher(s)'), q('Bank Total'), q('Bank Currency'),
    q('AR/AP Voucher(s)'), q('AR/AP Total'), q('AR/AP Currency'),
    q('Difference'), q('Decision'), q('Match Type'),
  ].join(','))

  for (const g of groups) {
    const decision = g.confidence === null ? 'manual' : 'auto'
    lines.push([
      q(reconGroupSheetLabel(g.id, glVoucherByGroupId)),
      q(g.match_cardinality),
      q(g.bank_vouchers.join(', ')),
      fmtAmt(g.bank_total),
      q(g.currency ?? ''),
      q(g.ledger_vouchers.join(', ')),
      fmtAmt(g.ledger_total),
      q(g.currency ?? ''),
      fmtAmt(g.difference),
      q(decision),
      q(g.rule_hit),
    ].join(','))
  }

  lines.push('')

  // ── Partial matches ────────────────────────────────────────────────────────
  if (partials.length > 0) {
    lines.push(`=== PARTIAL MATCHES (${partials.length}) ===`)
    lines.push([
      q('Transaction ID'), q('Date'), q('Bank Voucher / Description'),
      q('Bank Amount'), q('Currency'), q('Remaining Balance'),
      q(RECON_GROUP_COL_HEADER), q('Group Cardinality'),
    ].join(','))

    for (const pt of partials) {
      const gid = String(pt.group?.id ?? '').trim()
      lines.push([
        q(pt.id),
        q(fmtDate(pt.bank_date)),
        q(pt.reference ?? pt.description_raw),
        fmtAmt(pt.amount),
        q(pt.currency),
        fmtAmt(pt.group?.difference),
        q(gid ? reconGroupSheetLabel(gid, glVoucherByGroupId) : ''),
        q(pt.group?.match_cardinality ?? ''),
      ].join(','))
    }

    lines.push('')
  }

  // ── Unmatched bank ────────────────────────────────────────────────────────
  lines.push(`=== UNMATCHED BANK TRANSACTIONS (${unmatchedBank.length}) ===`)
  lines.push([q('ID'), q('Date'), q('Amount'), q('Currency'), q('Description'), q('Reference'), q('Batch')].join(','))

  for (const t of unmatchedBank) {
    lines.push([
      q(t.id),
      q(fmtDate(t.bank_date ?? t.date)),
      fmtAmt(Number(t.amount)),
      q(t.currency ?? ''),
      q(t.description_raw ?? t.description ?? ''),
      q(t.reference ?? ''),
      q(t.import_batch_id ?? ''),
    ].join(','))
  }

  lines.push('')

  // ── Unmatched ledger ──────────────────────────────────────────────────────
  lines.push(`=== UNMATCHED AR/AP TRANSACTIONS (${unmatchedLedger.length}) ===`)
  lines.push([q('ID'), q('Date'), q('Amount'), q('Currency'), q('Counterparty'), q('Doc Type'), q('Reference'), q('Batch')].join(','))

  for (const t of unmatchedLedger) {
    lines.push([
      q(t.id),
      q(fmtDate(t.book_date ?? t.date)),
      fmtAmt(Number(t.amount)),
      q(t.currency ?? ''),
      q(t.counterparty ?? ''),
      q(t.doc_type ?? ''),
      q(t.reference ?? ''),
      q(t.import_batch_id ?? ''),
    ].join(','))
  }

  return lines.join('\n')
}

// ─── Download helper ─────────────────────────────────────────────────────────

/** Create a Blob and trigger a browser download. Adds BOM for Excel UTF-8 compatibility. */
export function downloadBlob(filename: string, content: string, type: string): void {
  const blob = new Blob(['\uFEFF' + content], { type })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
  URL.revokeObjectURL(a.href)
}
