import './ReconciliationTable.css'
import React, { useState, useCallback } from 'react'
import { reconciliationApi } from '../services/reconciliation'
import type { MatchCardinality, MultiMatchResponse } from '../types/reconciliation'
import {
  buildReconCsv,
  downloadBlob,
  applyExportFilters,
  todayISO,
  type ExportFilters,
} from '../utils/reconExport'
import { reconGroupSheetLabel } from '../utils/reconMatchedSpreadsheet'

// ─── Data shapes passed from parent ────────────────────────────────────────

export interface PartialTxnGroup {
  id: string
  match_cardinality: MatchCardinality
  total_bank_amount: number
  total_ledger_amount: number
  difference: number
  bank_member_ids: string[]
  ledger_member_ids: string[]
}

export interface PartialTransaction {
  id: string
  bank_date: string | null
  amount: number
  currency: string
  description_raw: string
  reference?: string | null
  group: PartialTxnGroup | null
  /** Display voucher strings resolved by parent */
  bank_member_vouchers?: string[]
  ledger_member_vouchers?: string[]
}

export interface MatchedGroupRow {
  /** Unique identifier — group_id for multi-match, match_id for legacy 1:1 */
  id: string
  match_cardinality: MatchCardinality
  bank_vouchers: string[]       // display strings (voucher numbers or IDs)
  ledger_vouchers: string[]
  bank_txn_ids: string[]        // actual DB IDs for unmatch calls
  ledger_txn_ids: string[]
  bank_total: number
  ledger_total: number
  difference: number
  confidence: number | null     // null for manual groups
  rule_hit: string
  partial_remainder_txn_id?: string | null
  partial_remainder_amount?: number | null
  currency?: string
  /** true = legacy 1:1 (no group); false = multi-match group */
  is_legacy: boolean
  /** Snapshots of the raw transaction objects at match time — used to restore the
   *  unmatched pool when the group is later deleted (unmatch). */
  bank_txn_snapshots?: any[]
  ledger_txn_snapshots?: any[]
  /** True when both sides are the same mode (e.g. BANK vs BANK, AR vs AR).
   *  Causes 已配對交易 to render one row per individual transaction instead of
   *  collapsing everything into a single summary row. */
  is_same_mode?: boolean
  /** ISO timestamp from GET /reconciliation/groups when the group was created. */
  created_at?: string | null
}

interface ReconciliationResultsProps {
  /** New grouped format — used when available */
  matchedGroups?: MatchedGroupRow[]
  /** Legacy flat format (fallback when matchedGroups is not provided) */
  matchedPairs?: any[]
  /** PARTIAL-status bank transactions — shown in matched table as 部分配對 rows */
  partialTransactions?: PartialTransaction[]
  unmatchedBank: any[]
  unmatchedLedger: any[]
  /** Called after a successful match or clear; receives IDs + API result for parent state update */
  onMatchComplete?: (matchedBankIds: string[], matchedLedgerIds: string[], result?: MultiMatchResponse) => void
  /** Called after a group member is unmatched so parent can remove it from matched state */
  onGroupRemoved?: (groupId: string) => void
  /** Clear matched-id map / unlock UIDs for txn(s) returned to unmatched */
  onGroupUnmatched?: (bankTxnIds: string[], ledgerTxnIds: string[], groupId: string) => void
  /** Reload `matchedGroups` from the server after a partial member removal */
  onMatchedGroupsRefresh?: () => Promise<void>
  onRestoreMemberToUnmatched?: (grp: MatchedGroupRow, txnId: string, txnType: 'bank' | 'ledger') => void
  /** RECON right panel: hide export + 已配對交易 + inline partials (lifted to parent). */
  embeddedInReconRightPanel?: boolean
  companyId?: string
  currency?: string
  /** Maps reconciliation group UUID → GL-000006 for display */
  glVoucherNoByGroupId?: Record<string, string>
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function fmtAmt(n: number): string {
  return n.toLocaleString('en-HK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function cardinalityLabel(c: MatchCardinality): string {
  if (c === 'GL:1') return 'GL only'
  return c
}

// ─── Main component ─────────────────────────────────────────────────────────

export function ReconciliationTable({
  matchedGroups,
  matchedPairs,
  partialTransactions = [],
  unmatchedBank,
  unmatchedLedger,
  onMatchComplete,
  onGroupRemoved,
  onGroupUnmatched,
  onMatchedGroupsRefresh,
  onRestoreMemberToUnmatched,
  embeddedInReconRightPanel = false,
  currency = 'HKD',
  glVoucherNoByGroupId,
}: ReconciliationResultsProps) {
  const groupLabel = (gid: string) => reconGroupSheetLabel(gid, glVoucherNoByGroupId)
  // Cross-table checkbox selection state
  const [selectedBankIds, setSelectedBankIds] = useState<Set<string>>(new Set())
  const [selectedLedgerIds, setSelectedLedgerIds] = useState<Set<string>>(new Set())
  // Expanded partial transaction IDs (inline group breakdown)
  const [expandedPartialIds, setExpandedPartialIds] = useState<Set<string>>(new Set())
  const [fabLoading, setFabLoading] = useState(false)
  const [fabNotice, setFabNotice] = useState<string | null>(null)
  const [unmatchLoading, setUnmatchLoading] = useState<string | null>(null)

  // Export panel state
  const [showExportPanel, setShowExportPanel] = useState(false)
  const [exportDateFrom, setExportDateFrom] = useState('')
  const [exportDateTo, setExportDateTo] = useState('')
  const [exportMatchType, setExportMatchType] = useState<ExportFilters['matchType']>('all')

  const toggleBank = useCallback((id: string) => {
    setSelectedBankIds(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }, [])

  const toggleLedger = useCallback((id: string) => {
    setSelectedLedgerIds(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }, [])

  const clearSelection = useCallback(() => {
    setSelectedBankIds(new Set())
    setSelectedLedgerIds(new Set())
    setFabNotice(null)
  }, [])

  const togglePartialExpand = useCallback((id: string) => {
    setExpandedPartialIds(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }, [])

  // FAB visibility: show whenever at least one transaction is selected on either side.
  // Same-mode reconciliation (AR vs AR, BANK vs BANK) can produce ledger-only or
  // bank-only selections when the smart classifier assigns both tasks to opposite sides.
  const showFAB = selectedBankIds.size >= 1 || selectedLedgerIds.size >= 1
  const canMultiMatch = (selectedBankIds.size >= 1 && selectedLedgerIds.size >= 1)
    || (selectedBankIds.size >= 1 && selectedLedgerIds.size === 0 && selectedBankIds.size >= 2)
    || (selectedLedgerIds.size >= 1 && selectedBankIds.size === 0 && selectedLedgerIds.size >= 2)
  const canClear = selectedBankIds.size >= 1 && selectedLedgerIds.size === 0 && selectedBankIds.size < 2

  const handleMultiMatch = useCallback(async () => {
    setFabLoading(true)
    setFabNotice(null)
    const bankIds = Array.from(selectedBankIds)
    const ledgerIds = Array.from(selectedLedgerIds)

    // For same-mode matching where only one side has selections, send both halves
    // split between bank_txn_ids and ledger_txn_ids so the backend can look them up
    // in the appropriate table via its flexible fallback lookup.
    let finalBankIds = bankIds
    let finalLedgerIds = ledgerIds
    if (bankIds.length === 0 && ledgerIds.length >= 2) {
      // Ledger-only (AR vs AR, AP vs AP): split into two halves
      const half = Math.ceil(ledgerIds.length / 2)
      finalBankIds = ledgerIds.slice(0, half)
      finalLedgerIds = ledgerIds.slice(half)
    } else if (ledgerIds.length === 0 && bankIds.length >= 2) {
      // Bank-only with ≥2 selected — treated as BANK vs BANK match (not "clear")
      const half = Math.ceil(bankIds.length / 2)
      finalBankIds = bankIds.slice(0, half)
      finalLedgerIds = bankIds.slice(half)
    }

    try {
      const result = await reconciliationApi.multiManualMatch({
        bank_txn_ids: finalBankIds,
        ledger_txn_ids: finalLedgerIds,
      })
      clearSelection()
      if (result.difference !== 0) {
        const sign = result.difference > 0 ? '+' : ''
        setFabNotice(
          `Partial split: ${sign}${currency} ${fmtAmt(Math.abs(result.difference))} returned to unmatched pool`
        )
      }
      // Always pass the actual split IDs so the parent can correctly locate
      // each transaction in the right pool (bank vs ledger) for same-mode matches.
      onMatchComplete?.(finalBankIds, finalLedgerIds, result)
    } catch (err: any) {
      setFabNotice(`Match failed: ${err.message}`)
    } finally {
      setFabLoading(false)
    }
  }, [selectedBankIds, selectedLedgerIds, clearSelection, onMatchComplete, currency])

  const handleClearBank = useCallback(async () => {
    setFabLoading(true)
    setFabNotice(null)
    const bankIds = Array.from(selectedBankIds)
    try {
      const result = await reconciliationApi.clearBankTransactions({ bank_txn_ids: bankIds })
      clearSelection()
      // Re-use onMatchComplete with empty ledgerIds so parent removes bank txns from unmatched
      onMatchComplete?.(bankIds, [], result)
    } catch (err: any) {
      setFabNotice(`Clear failed: ${err.message}`)
    } finally {
      setFabLoading(false)
    }
  }, [selectedBankIds, clearSelection, onMatchComplete])

  const handleGroupUnmatchMember = useCallback(
    async (groupId: string, txnId: string, txnType: 'bank' | 'ledger', isLegacy: boolean) => {
      const key = `${groupId}-${txnId}`
      setUnmatchLoading(key)
      try {
        if (isLegacy) {
          await reconciliationApi.unmatch({ match_id: groupId, reason: 'user_removed_from_table' })
          onGroupRemoved?.(groupId)
        } else {
          const res = await reconciliationApi.groupUnmatchMember({
            group_id: groupId,
            txn_id: txnId,
            txn_type: txnType,
            reason: 'user_removed_from_table',
          })
          if (res.group_dissolved) {
            onGroupRemoved?.(groupId)
          } else {
            onGroupUnmatched?.(
              txnType === 'bank' ? [txnId] : [],
              txnType === 'ledger' ? [txnId] : [],
              groupId,
            )
            const g = matchedGroups?.find(x => x.id === groupId)
            if (g) onRestoreMemberToUnmatched?.(g, txnId, txnType)
            await onMatchedGroupsRefresh?.()
          }
        }
      } catch (err: any) {
        alert(`Unmatch failed: ${err.message}`)
      } finally {
        setUnmatchLoading(null)
      }
    },
    [
      onGroupRemoved,
      onGroupUnmatched,
      onMatchedGroupsRefresh,
      onRestoreMemberToUnmatched,
      matchedGroups,
    ],
  )

  // ── Build display groups from legacy flat pairs if matchedGroups not provided ──
  const displayGroups: MatchedGroupRow[] = matchedGroups
    ? matchedGroups
    : (matchedPairs || []).map((m: any) => ({
        id: m.id || `${m.bank_txn?.id}-${m.ledger_txn?.id}`,
        match_cardinality: '1:1' as MatchCardinality,
        bank_vouchers: [m.bank_txn?.reference || m.bank_txn?.id || '-'],
        ledger_vouchers: [m.ledger_txn?.reference || m.ledger_txn?.id || '-'],
        bank_txn_ids: [m.bank_txn?.id].filter(Boolean),
        ledger_txn_ids: [m.ledger_txn?.id].filter(Boolean),
        bank_total: Number(m.bank_txn?.amount ?? 0),
        ledger_total: Number(m.ledger_txn?.amount ?? 0),
        difference: 0,
        confidence: m.score ?? null,
        rule_hit: m.match_type || '-',
        partial_remainder_txn_id: null,
        is_legacy: true,
      }))

  // ── Export handlers ──────────────────────────────────────────────────────
  const handleDownloadCsv = () => {
    const filters: ExportFilters = { dateFrom: exportDateFrom, dateTo: exportDateTo, matchType: exportMatchType }
    const filtered = applyExportFilters(displayGroups, partialTransactions, unmatchedBank, unmatchedLedger, filters)
    const csv = buildReconCsv(
      filtered.groups,
      filtered.partials,
      filtered.unmatchedBank,
      filtered.unmatchedLedger,
      null,
      filters,
      glVoucherNoByGroupId,
    )
    downloadBlob(`recon-report-${todayISO()}.csv`, csv, 'text/csv')
  }

  const handlePrint = () => {
    window.print()
  }

  const hasMatched = displayGroups.length > 0 || partialTransactions.length > 0
  const hasUnmatchedBank = (unmatchedBank?.length ?? 0) > 0
  const hasUnmatchedLedger = (unmatchedLedger?.length ?? 0) > 0
  const isEmpty = embeddedInReconRightPanel
    ? false
    : !hasMatched && !hasUnmatchedBank && !hasUnmatchedLedger

  return (
    <div className="recon-table-container">

      {!embeddedInReconRightPanel && (
        <>
          <div className="recon-print-header">
            <h2>Reconciliation Report</h2>
            <p>Generated: {todayISO()}</p>
            <p>
              Matched: {displayGroups.length + partialTransactions.length} &nbsp;|&nbsp;
              Unmatched Bank: {unmatchedBank.length} &nbsp;|&nbsp;
              Unmatched AR/AP: {unmatchedLedger.length}
            </p>
          </div>

          <div className="recon-export-toolbar">
            <button
              type="button"
              className="recon-export-toggle-btn"
              onClick={() => setShowExportPanel(p => !p)}
            >
              {showExportPanel ? '▲ Hide Export' : '▼ Export Report'}
            </button>
          </div>

          {showExportPanel && (
            <div className="recon-export-panel">
              <div className="recon-export-filters">
                <label className="recon-export-label">
                  Date From
                  <input
                    type="date"
                    className="recon-export-input"
                    value={exportDateFrom}
                    onChange={e => setExportDateFrom(e.target.value)}
                  />
                </label>
                <label className="recon-export-label">
                  Date To
                  <input
                    type="date"
                    className="recon-export-input"
                    value={exportDateTo}
                    onChange={e => setExportDateTo(e.target.value)}
                  />
                </label>
                <label className="recon-export-label">
                  Match Type
                  <select
                    className="recon-export-select"
                    value={exportMatchType}
                    onChange={e => setExportMatchType(e.target.value as ExportFilters['matchType'])}
                  >
                    <option value="all">All</option>
                    <option value="auto">Auto only</option>
                    <option value="manual">Manual only</option>
                  </select>
                </label>
              </div>
              <div className="recon-export-actions">
                <button type="button" className="recon-export-btn recon-export-btn-csv" onClick={handleDownloadCsv}>
                  Download CSV
                </button>
                <button type="button" className="recon-export-btn recon-export-btn-print" onClick={handlePrint}>
                  Print / PDF
                </button>
              </div>
            </div>
          )}
        </>
      )}

      {/* ── Matched Groups + Partial Transactions Section ───────────── */}
      {!embeddedInReconRightPanel && hasMatched && (
        <div className="recon-section">
          <h3>Matched transactions ({displayGroups.length + partialTransactions.length})</h3>
          <div className="recon-matched-scroll">
          <table className="recon-table recon-table-matched">
            <thead>
              <tr>
                <th title="GL voucher (e.g. GL-000006) when known; hover a cell for full group id">GL / Group</th>
                <th>Type</th>
                <th>Bank Voucher(s)</th>
                <th>AR/AP Voucher(s)</th>
                <th className="amount">Bank Total</th>
                <th className="amount">AR/AP Total</th>
                <th className="amount">Diff</th>
                <th>Confidence</th>
                <th>Rule</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {displayGroups.map((grp) => {
                // ── Same-mode (BANK vs BANK, AR vs AR): one row per individual transaction ──
                if (grp.is_same_mode) {
                  const txnRows = [
                    ...grp.bank_txn_ids.map((id, idx) => ({
                      id,
                      txnType: 'bank' as const,
                      voucher: grp.bank_vouchers[idx] || id.slice(0, 8),
                      amount: ((grp.bank_txn_snapshots?.[idx]) as any)?.amount ?? 0,
                      rowIdx: idx,
                    })),
                    ...grp.ledger_txn_ids.map((id, idx) => ({
                      id,
                      txnType: 'ledger' as const,
                      voucher: grp.ledger_vouchers[idx] || id.slice(0, 8),
                      amount: ((grp.ledger_txn_snapshots?.[idx]) as any)?.amount ?? 0,
                      rowIdx: grp.bank_txn_ids.length + idx,
                    })),
                  ]
                  return (
                    <React.Fragment key={grp.id}>
                      {txnRows.map((txnRow) => (
                        <tr key={txnRow.id} className="recon-group-row">
                          {/* Group ID + Type only on the first row of the group */}
                          <td className="recon-group-id" title={grp.id}>
                            {txnRow.rowIdx === 0 ? groupLabel(grp.id) : ''}
                          </td>
                          <td>
                            {txnRow.rowIdx === 0 && (
                              <span className="recon-cardinality-badge">
                                {cardinalityLabel(grp.match_cardinality)}
                              </span>
                            )}
                          </td>
                          {/* Individual transaction voucher chip */}
                          <td>
                            <span className="recon-voucher-chip">
                              {txnRow.voucher}
                              <button
                                className="recon-member-remove-btn"
                                title="Remove this transaction from group"
                                disabled={unmatchLoading === `${grp.id}-${txnRow.id}`}
                                onClick={() =>
                                  handleGroupUnmatchMember(grp.id, txnRow.id, txnRow.txnType, grp.is_legacy)
                                }
                              >
                                ×
                              </button>
                            </span>
                          </td>
                          <td></td>{/* AR/AP Voucher — N/A for same-mode */}
                          {/* Individual transaction amount (supports negative for withdrawals) */}
                          <td className="amount">{fmtAmt(txnRow.amount)}</td>
                          <td></td>{/* AR/AP Total — N/A */}
                          <td className={`amount ${txnRow.rowIdx === 0 && grp.difference !== 0 ? 'recon-diff-nonzero' : ''}`}>
                            {txnRow.rowIdx === 0
                              ? grp.difference !== 0
                                ? `${grp.difference > 0 ? '+' : ''}${fmtAmt(grp.difference)}`
                                : '—'
                              : ''}
                          </td>
                          <td className="confidence">
                            {txnRow.rowIdx === 0
                              ? grp.confidence !== null && grp.confidence !== undefined
                                ? `${(grp.confidence * 100).toFixed(1)}%`
                                : 'Manual'
                              : ''}
                          </td>
                          <td>{txnRow.rowIdx === 0 ? grp.rule_hit : ''}</td>
                          <td></td>
                        </tr>
                      ))}
                    </React.Fragment>
                  )
                }

                // ── Cross-mode (BANK vs AR/AP): one summary row per group ──────────────
                return (
                  <React.Fragment key={grp.id}>
                    <tr className="recon-group-row">
                      <td className="recon-group-id" title={grp.id}>
                        {groupLabel(grp.id)}
                      </td>
                      <td>
                        <span className="recon-cardinality-badge">
                          {cardinalityLabel(grp.match_cardinality)}
                        </span>
                      </td>
                      <td>
                        <div className="recon-voucher-list">
                          {grp.bank_txn_ids.map((id, idx) => {
                            const key = `${grp.id}-${id}`
                            return (
                              <span key={id} className="recon-voucher-chip">
                                {grp.bank_vouchers[idx] || id.slice(0, 8)}
                                <button
                                  className="recon-member-remove-btn"
                                  title="Remove this transaction from group"
                                  disabled={unmatchLoading === key}
                                  onClick={() =>
                                    handleGroupUnmatchMember(grp.id, id, 'bank', grp.is_legacy)
                                  }
                                >
                                  ×
                                </button>
                              </span>
                            )
                          })}
                        </div>
                      </td>
                      <td>
                        <div className="recon-voucher-list">
                          {grp.ledger_txn_ids.map((id, idx) => {
                            const key = `${grp.id}-${id}`
                            return (
                              <span key={id} className="recon-voucher-chip">
                                {grp.ledger_vouchers[idx] || id.slice(0, 8)}
                                <button
                                  className="recon-member-remove-btn"
                                  title="Remove this transaction from group"
                                  disabled={unmatchLoading === key}
                                  onClick={() =>
                                    handleGroupUnmatchMember(grp.id, id, 'ledger', grp.is_legacy)
                                  }
                                >
                                  ×
                                </button>
                              </span>
                            )
                          })}
                        </div>
                      </td>
                      <td className="amount">{fmtAmt(grp.bank_total)}</td>
                      <td className="amount">{fmtAmt(grp.ledger_total)}</td>
                      <td className={`amount ${grp.difference !== 0 ? 'recon-diff-nonzero' : ''}`}>
                        {grp.difference !== 0
                          ? `${grp.difference > 0 ? '+' : ''}${fmtAmt(grp.difference)}`
                          : '—'}
                      </td>
                      <td className="confidence">
                        {grp.confidence !== null && grp.confidence !== undefined
                          ? `${(grp.confidence * 100).toFixed(1)}%`
                          : 'Manual'}
                      </td>
                      <td>{grp.rule_hit}</td>
                      <td></td>
                    </tr>
                  </React.Fragment>
                )
              })}

              {/* ── 部分配對 rows ── */}
              {partialTransactions.map((pt) => {
                const isExpanded = expandedPartialIds.has(pt.id)
                const grp = pt.group
                return (
                  <>
                    <tr
                      key={pt.id}
                      className="recon-partial-matched-row"
                      onClick={() => togglePartialExpand(pt.id)}
                      title="Click to see group breakdown"
                    >
                      <td className="recon-group-id" title={pt.id}>
                        {pt.id.slice(0, 8)}…
                      </td>
                      <td>
                        <span className="recon-cardinality-badge recon-cardinality-partial">
                          Partial
                        </span>
                      </td>
                      <td>
                        <span className="recon-voucher-chip recon-voucher-chip-partial">
                          {pt.reference || pt.description_raw.slice(0, 20)}
                        </span>
                      </td>
                      <td>—</td>
                      <td className="amount recon-diff-nonzero">{fmtAmt(pt.amount)}</td>
                      <td>—</td>
                      <td className="amount recon-diff-nonzero">
                        {grp ? `${grp.difference > 0 ? '+' : ''}${fmtAmt(grp.difference)}` : '—'}
                      </td>
                      <td>—</td>
                      <td>Partial</td>
                      <td>
                        <button className="recon-expand-btn" onClick={(e) => { e.stopPropagation(); togglePartialExpand(pt.id) }}>
                          {isExpanded ? '▲' : '▼'}
                        </button>
                      </td>
                    </tr>

                    {/* Inline group breakdown */}
                    {isExpanded && grp && (
                      <tr key={`${pt.id}-expand`} className="recon-partial-expand-row">
                        <td colSpan={10}>
                          <div className="recon-partial-breakdown">
                            <div className="recon-breakdown-title">
                              Partial match — Group {groupLabel(grp.id)} ({grp.match_cardinality})
                            </div>
                            <div className="recon-breakdown-grid">
                              <div className="recon-breakdown-col">
                                <div className="recon-breakdown-label">Bank transactions</div>
                                {(pt.bank_member_vouchers && pt.bank_member_vouchers.length > 0
                                  ? pt.bank_member_vouchers
                                  : grp.bank_member_ids.map(id => id.slice(0, 8) + '…')
                                ).map((v, i) => (
                                  <div key={i} className="recon-breakdown-item">{v}</div>
                                ))}
                                <div className="recon-breakdown-total">Total: {fmtAmt(grp.total_bank_amount)} {pt.currency}</div>
                              </div>
                              <div className="recon-breakdown-arrow">⇄</div>
                              <div className="recon-breakdown-col">
                                <div className="recon-breakdown-label">AR/AP transactions</div>
                                {(pt.ledger_member_vouchers && pt.ledger_member_vouchers.length > 0
                                  ? pt.ledger_member_vouchers
                                  : grp.ledger_member_ids.map(id => id.slice(0, 8) + '…')
                                ).map((v, i) => (
                                  <div key={i} className="recon-breakdown-item">{v}</div>
                                ))}
                                <div className="recon-breakdown-total">Total: {fmtAmt(grp.total_ledger_amount)} {pt.currency}</div>
                              </div>
                            </div>
                            <div className="recon-breakdown-diff">
                              Difference: {grp.difference > 0 ? '+' : ''}{fmtAmt(grp.difference)} {pt.currency}
                              &nbsp;→&nbsp;This remainder ({fmtAmt(pt.amount)} {pt.currency}) can still be matched
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
          </div>{/* end recon-matched-scroll */}
        </div>
      )}

      {/* ── Unmatched Bank Transactions ─────────────────────────────── */}
      {hasUnmatchedBank && (
        <div className="recon-section">
          <h3>Unmatched bank ({unmatchedBank.length})</h3>
          <table className="recon-table">
            <thead>
              <tr>
                <th className="recon-checkbox-col">
                  <input
                    type="checkbox"
                    title="Select all bank"
                    checked={unmatchedBank.length > 0 && unmatchedBank.every((t: any) => selectedBankIds.has(t.id))}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setSelectedBankIds(new Set(unmatchedBank.map((t: any) => t.id)))
                      } else {
                        setSelectedBankIds(new Set())
                      }
                    }}
                  />
                </th>
                <th>Date</th>
                <th>Description</th>
                <th className="amount">Amount</th>
                <th>Reference</th>
              </tr>
            </thead>
            <tbody>
              {unmatchedBank.map((txn: any) => (
                <tr
                  key={txn.id}
                  className={`unmatched-row${selectedBankIds.has(txn.id) ? ' recon-row-selected' : ''}`}
                  onClick={() => toggleBank(txn.id)}
                >
                  <td className="recon-checkbox-col" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedBankIds.has(txn.id)}
                      onChange={() => toggleBank(txn.id)}
                    />
                  </td>
                  <td>{txn.bank_date || txn.date}</td>
                  <td>{txn.description_raw || txn.description}</td>
                  <td className="amount">{fmtAmt(Number(txn.amount))}</td>
                  <td>{txn.reference || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Unmatched Ledger Transactions ───────────────────────────── */}
      {hasUnmatchedLedger && (
        <div className="recon-section">
          <h3>Unmatched ledger ({unmatchedLedger.length})</h3>
          <table className="recon-table">
            <thead>
              <tr>
                <th className="recon-checkbox-col">
                  <input
                    type="checkbox"
                    title="Select all AR/AP"
                    checked={unmatchedLedger.length > 0 && unmatchedLedger.every((t: any) => selectedLedgerIds.has(t.id))}
                    onChange={(e) => {
                      if (e.target.checked) {
                        setSelectedLedgerIds(new Set(unmatchedLedger.map((t: any) => t.id)))
                      } else {
                        setSelectedLedgerIds(new Set())
                      }
                    }}
                  />
                </th>
                <th>Date</th>
                <th className="amount">Amount</th>
                <th>Reference</th>
                <th>Counterparty</th>
              </tr>
            </thead>
            <tbody>
              {unmatchedLedger.map((txn: any) => (
                <tr
                  key={txn.id}
                  className={`unmatched-row${selectedLedgerIds.has(txn.id) ? ' recon-row-selected' : ''}`}
                  onClick={() => toggleLedger(txn.id)}
                >
                  <td className="recon-checkbox-col" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedLedgerIds.has(txn.id)}
                      onChange={() => toggleLedger(txn.id)}
                    />
                  </td>
                  <td>{txn.book_date || txn.date}</td>
                  <td className="amount">{fmtAmt(Number(txn.amount))}</td>
                  <td>{txn.reference || '—'}</td>
                  <td>{txn.counterparty || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Empty state ─────────────────────────────────────────────── */}
      {isEmpty && (
        <div className="recon-empty">
          <p>No reconciliation data</p>
        </div>
      )}

      {/* ── Sticky FAB ──────────────────────────────────────────────── */}
      {showFAB && (
        <div className="recon-multi-fab">
          <div className="recon-fab-body">
            <span className="recon-fab-label">
              {selectedBankIds.size} bank
              {selectedLedgerIds.size > 0 ? ` + ${selectedLedgerIds.size} AR` : ''} selected
            </span>
            {fabNotice && (
              <span className="recon-fab-notice" title={fabNotice}>
                {fabNotice}
                <button className="recon-fab-notice-close" onClick={() => setFabNotice(null)}>×</button>
              </span>
            )}
          </div>
          <div className="recon-fab-actions">
            {canMultiMatch && (
              <button
                className="recon-fab-match-btn"
                onClick={handleMultiMatch}
                disabled={fabLoading}
              >
                {fabLoading ? 'Matching…' : 'Match Selected'}
              </button>
            )}
            {canClear && (
              <button
                className="recon-fab-clear-bank-btn"
                onClick={handleClearBank}
                disabled={fabLoading}
              >
                {fabLoading ? 'Clearing…' : 'Mark as Cleared'}
              </button>
            )}
            <button
              className="recon-fab-clear-btn"
              onClick={clearSelection}
              disabled={fabLoading}
            >
              Clear
            </button>
          </div>
        </div>
      )}

      {/* ── Notice shown after FAB is dismissed (e.g. partial split success) ── */}
      {fabNotice && !showFAB && (
        <div className="recon-partial-notice">
          {fabNotice}
          <button onClick={() => setFabNotice(null)}>×</button>
        </div>
      )}
    </div>
  )
}
