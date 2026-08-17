import { useState } from 'react'
import type { MatchCardinality } from '../types/reconciliation'
import {
  buildReconCsv,
  downloadBlob,
  applyExportFilters,
  todayISO,
  type ExportFilters,
} from '../utils/reconExport'
import type { MatchedGroupRow, PartialTransaction } from './ReconciliationTable'
import './ReconciliationTable.css'

function buildDisplayGroups(
  matchedGroups: MatchedGroupRow[] | undefined,
  matchedPairs: any[] | undefined,
): MatchedGroupRow[] {
  if (matchedGroups?.length) return matchedGroups
  return (matchedPairs || []).map((m: any) => ({
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
}

export interface ReconExportToolbarProps {
  matchedGroups?: MatchedGroupRow[]
  matchedPairs?: any[]
  partialTransactions: PartialTransaction[]
  unmatchedBank: any[]
  unmatchedLedger: any[]
  glVoucherNoByGroupId?: Record<string, string>
}

export function ReconExportToolbar({
  matchedGroups,
  matchedPairs,
  partialTransactions,
  unmatchedBank,
  unmatchedLedger,
  glVoucherNoByGroupId,
}: ReconExportToolbarProps) {
  const [showExportPanel, setShowExportPanel] = useState(false)
  const [exportDateFrom, setExportDateFrom] = useState('')
  const [exportDateTo, setExportDateTo] = useState('')
  const [exportMatchType, setExportMatchType] = useState<ExportFilters['matchType']>('all')

  const displayGroups = buildDisplayGroups(matchedGroups, matchedPairs)

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
  const isEmpty = !hasMatched && !hasUnmatchedBank && !hasUnmatchedLedger
  if (isEmpty) return null

  return (
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
  )
}
