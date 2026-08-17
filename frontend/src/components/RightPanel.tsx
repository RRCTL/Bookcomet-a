import { useState } from 'react'
import { reconciliationApi } from '../services/reconciliation'
import { isReconNonGroupMatchedRow } from '../utils/reconMatchedSpreadsheet'
import { mapApiReconciliationGroupsToMatched } from '../utils/reconGroupsFromApi'
import { ReconExportToolbar } from './ReconExportToolbar'
import { ReconPartialStrip } from './ReconPartialStrip'
import { ReconGlSection } from './ReconGlSection'
import { EditableSpreadsheet, type SpreadsheetRow } from './EditableSpreadsheet'
import { type ReconTransactionItem } from './ReconContainer'
import { ReconciliationTable, type MatchedGroupRow } from './ReconciliationTable'
import { FinancialReportsView } from './FinancialReportsView'

const REPORT_PANEL_SCROLL_ID = 'financial-report-panel-scroll'
import { ExtractionSummaryPanel, type ExtractionSummaryTask, type OcrDestinationPayload } from './ExtractionSummaryPanel'
import { MDRecordViewer } from './MDRecordViewer'
import type { ProcessingMode } from './ModeSelector'
import type { FinancialReportData } from '../hooks/useReportData'
import type { ChartOfAccountItem } from '../types/reconciliation'
import './RightPanel.css'

interface DupAlert {
  id: string
  level: 1 | 2 | 3 | 4
  message: string
  txnIds: { msgId: string; txnIndex: number; idNumber: string }[]
  resolved?: 'continue' | 'cancel'
}

export interface ReconPanelProps {
  sourcePoolTransactions: ReconTransactionItem[]
  bankPoolTransactions: ReconTransactionItem[]
  reconSelectedSourceTxnIds: string[]
  reconSelectedBankTxnIds: string[]
  reconMatchedSourceUids: string[]
  reconMatchedBankUids: string[]
  reconMatchResult: { matchedCount: number; timestamp: number } | null
  isProcessing: boolean
  onAllDrag: () => void
  onClearContainer: () => void
  onSelectSource: (uid: string) => void
  onSelectBank: (uid: string) => void
  onRemoveSource: (uid: string) => void
  onRemoveBank: (uid: string) => void
  onRunMatch: () => void
  onCheckDuplicates: () => void
  reconStatusText: string
  reconMatchedRows: SpreadsheetRow[]
  reconMatchedColumns: string[]
  setReconMatchedRows: React.Dispatch<React.SetStateAction<SpreadsheetRow[]>>
  reconMatchedGroups: MatchedGroupRow[]
  setReconMatchedGroups: React.Dispatch<React.SetStateAction<MatchedGroupRow[]>>
  reconUnmatchedTxns: { bank: any[]; ledger: any[] }
  setReconUnmatchedTxns: React.Dispatch<React.SetStateAction<{ bank: any[]; ledger: any[] }>>
  reconUnmatchedRows: { bank: SpreadsheetRow[]; ledger: SpreadsheetRow[] }
  setReconUnmatchedRows: React.Dispatch<React.SetStateAction<{ bank: SpreadsheetRow[]; ledger: SpreadsheetRow[] }>>
  reconPartialTxns: any[]
  setReconPartialTxns: React.Dispatch<React.SetStateAction<any[]>>
  setReconStatusText: React.Dispatch<React.SetStateAction<string>>
  duplicateAlerts: DupAlert[]
  onDuplicateResolve: (alertId: string, action: 'continue' | 'cancel') => void
  getCategoryOptionsForMode: (mode: string) => string[]
  onMatchedIdsUpdate?: (bankTxnIds: string[], ledgerTxnIds: string[], groupId: string) => void
  /** Called after a group is unmatched so App.tsx can clean up locked-UID sets and matched-ID map */
  onGroupUnmatched?: (bankTxnIds: string[], ledgerTxnIds: string[], groupId: string) => void
  /** Replace `reconMatchedGroups` from GET /reconciliation/groups (after member unmatch) */
  onReconGroupsRefresh?: () => Promise<void>
  /** Push one txn back into unmatched pools using group snapshots (after member unmatch) */
  onRestoreMemberToUnmatched?: (grp: MatchedGroupRow, txnId: string, txnType: 'bank' | 'ledger') => void
  /** Lifted from App so AI Apply and the table share the same match/unmatch logic */
  onReconMatchComplete?: (
    matchedBankIds: string[],
    matchedLedgerIds: string[],
    result: any,
  ) => void
  onReconGroupRemoved?: (groupId: string) => void
  onGlAccountCodesSynced?: (sync: { bank: Record<string, string>; ledger: Record<string, string> }) => void
  glJournalRefetchSignal?: { nonce: number; groupIds: string[] } | null
  glApplyPatchSeeds?: { nonce: number; byGroupId: Record<string, any> } | null
  onPrimaryJournalStatusByGroup?: (statusByGroupId: Record<string, string>) => void
  onGlVoucherNoByGroup?: (voucherByGroupId: Record<string, string>) => void
  glVoucherNoByGroupId?: Record<string, string>
  reconLedgerOnlyPools?: boolean
  reconScrollTargetGroupId?: string | null
  onReconScrollTargetConsumed?: () => void
}

export interface RightPanelProps extends ReconPanelProps {
  mode: ProcessingMode
  financialReportData: FinancialReportData | null
  coaList: ChartOfAccountItem[]
  mdContent: string
  /** When set (OCR modes with active task), shows interactive extraction summary with Destination buttons. */
  extractionTask?: ExtractionSummaryTask | null
  onOcrDestination?: (p: OcrDestinationPayload) => void
}

export function RightPanel(props: RightPanelProps) {
  const { mode } = props

  if (mode === 'RECON') {
    return <ReconRightPanel {...props} />
  }

  if (mode === 'REPORT') {
    return (
      <div className="right-panel-container">
        <div className="right-panel-header">
          <span className="right-panel-title">Financial statements</span>
          <span className="right-panel-mode-badge" style={{ background: '#fff7ed', color: '#c2410c' }}>RPT</span>
        </div>
        <div id={REPORT_PANEL_SCROLL_ID} className="right-panel-content">
          {props.financialReportData ? (
            <FinancialReportsView data={props.financialReportData} />
          ) : (
            <div className="right-panel-empty">
              <p>No report generated yet.</p>
              <p className="right-panel-hint">Use the chat to generate financial reports.</p>
            </div>
          )}
        </div>
      </div>
    )
  }

  const modeBadgeStyle = {
    AR:        { background: '#dcfce7', color: '#15803d' },
    AP:        { background: '#fef9c3', color: '#a16207' },
    BANK:      { background: '#f3e8ff', color: '#7e22ce' },
    OTHER: { background: '#d1fae5', color: '#065f46' },
  }[mode] ?? { background: '#f3f4f6', color: '#374151' }

  return (
    <div className="right-panel-container">
      <div className="right-panel-header">
        <span className="right-panel-title">Extraction</span>
        <span className="right-panel-mode-badge" style={modeBadgeStyle}>{mode}</span>
      </div>
      <div className="right-panel-content">
        {props.extractionTask ? (
          <ExtractionSummaryPanel task={props.extractionTask} onDestination={props.onOcrDestination} />
        ) : (
          <MDRecordViewer content={props.mdContent} />
        )}
      </div>
    </div>
  )
}

function ReconRightPanel(props: RightPanelProps) {
  const {
    sourcePoolTransactions, bankPoolTransactions,
    reconSelectedSourceTxnIds, reconSelectedBankTxnIds,
    reconMatchedGroups, setReconMatchedGroups,
    reconMatchedRows, reconMatchedColumns, setReconMatchedRows,
    reconUnmatchedTxns, setReconUnmatchedTxns,
    reconUnmatchedRows, setReconUnmatchedRows,
    reconPartialTxns, setReconPartialTxns,
    setReconStatusText,
    duplicateAlerts, onDuplicateResolve,
    getCategoryOptionsForMode,
    onSelectSource, onSelectBank,
    onMatchedIdsUpdate,
    onGroupUnmatched,
    onReconGroupsRefresh,
    onRestoreMemberToUnmatched,
    onReconMatchComplete,
    onReconGroupRemoved,
    onGlAccountCodesSynced,
    glJournalRefetchSignal,
    glApplyPatchSeeds,
    onPrimaryJournalStatusByGroup,
    onGlVoucherNoByGroup,
    reconScrollTargetGroupId,
    onReconScrollTargetConsumed,
    coaList,
  } = props

  const [sourceOpen, setSourceOpen] = useState(true)
  const [bankOpen,   setBankOpen]   = useState(true)

  const selectedSourceSet = new Set(reconSelectedSourceTxnIds)
  const selectedBankSet = new Set(reconSelectedBankTxnIds)
  // Also hide transactions that have already been matched (locked) so they can't be re-dragged
  const matchedUidsSet = new Set([...props.reconMatchedSourceUids, ...props.reconMatchedBankUids])
  const pendingSource = sourcePoolTransactions.filter(r =>
    !selectedSourceSet.has(r.uid) && !matchedUidsSet.has(r.uid)
  )
  const pendingBank = bankPoolTransactions.filter(r =>
    !selectedBankSet.has(r.uid) && !matchedUidsSet.has(r.uid)
  )

  const activeDupAlerts = duplicateAlerts.filter(a => !a.resolved)

  const handleMatchComplete = onReconMatchComplete ?? (() => {})
  const handleGroupRemoved = onReconGroupRemoved ?? (() => {})

  const renderChip = (row: ReconTransactionItem) => (
    <div
      key={row.uid}
      className={`recon-chip ${row.kind === 'source' ? 'recon-chip-source' : 'recon-chip-bank'} ${row.matchable ? '' : 'recon-chip-disabled'}`}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData('application/json', JSON.stringify({ type: row.kind, uid: row.uid }))
        e.dataTransfer.effectAllowed = 'move'
      }}
      onDoubleClick={() => {
        if (row.kind === 'source') onSelectSource(row.uid)
        if (row.kind === 'bank') onSelectBank(row.uid)
      }}
      title="Drag to match bar or double-click to select"
    >
      <span className="recon-chip-title">
        {row.voucherNo || '(No voucher)'} · {row.amountText}
        <span className={`recon-match-badge ${row.matchable ? 'is-matchable' : 'is-missing-id'}`}>
          {row.matchable ? 'Matchable' : 'No ID'}
        </span>
      </span>
      <span className="recon-chip-meta">
        {row.date || '-'} · {row.recordMode} · {row.recordTitle}
      </span>
    </div>
  )

  return (
    <div className="right-panel-container recon-right-panel">
      <div className="right-panel-header">
        <span className="right-panel-title">Reconciliation</span>
        <span className="right-panel-mode-badge" style={{ background: '#dbeafe', color: '#1d4ed8' }}>RECON</span>
      </div>

      {/* Duplicate alerts banner */}
      {activeDupAlerts.length > 0 && (
        <div className="recon-dup-banner">
          {activeDupAlerts.map(alert => (
            <div key={alert.id} className={`recon-dup-banner-item recon-dup-banner-L${alert.level}`}>
              <div className="recon-dup-banner-header">
                <span>{alert.level === 2 || alert.level === 4 ? '⚠ Confirmed duplicate' : '⚠ Possible duplicate'}</span>
                <span className="recon-dup-banner-tag">L{alert.level}</span>
              </div>
              <div className="recon-dup-banner-msg">{alert.message}</div>
              <div className="recon-dup-banner-actions">
                <button onClick={() => onDuplicateResolve(alert.id, 'continue')}>Keep</button>
                <button onClick={() => onDuplicateResolve(alert.id, 'cancel')}>Mark duplicate</button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="right-panel-content">
      {/* Collapsible record frames */}
      <div className="recon-records-split">

        {/* Source Records accordion */}
        <div className="recon-records-frame">
          <button
            className="recon-records-frame-title recon-records-toggle"
            onClick={() => setSourceOpen(v => !v)}
          >
            <span className={`recon-chevron${sourceOpen ? ' open' : ''}`}>›</span>
            Source Records
            <span className="recon-records-count">{pendingSource.length}</span>
          </button>
          {sourceOpen && (
            <div className="recon-records-scroll">
              {pendingSource.length === 0
                ? <div className="recon-empty">All dragged to match bar</div>
                : pendingSource.map(renderChip)
              }
            </div>
          )}
        </div>

        {/* Bank Records accordion */}
        <div className="recon-records-frame">
          <button
            className="recon-records-frame-title recon-records-toggle"
            onClick={() => setBankOpen(v => !v)}
          >
            <span className={`recon-chevron${bankOpen ? ' open' : ''}`}>›</span>
            Bank Records
            <span className="recon-records-count">{pendingBank.length}</span>
          </button>
          {bankOpen && (
            <div className="recon-records-scroll">
              {pendingBank.length === 0
                ? <div className="recon-empty">All dragged to match bar</div>
                : pendingBank.map(renderChip)
              }
            </div>
          )}
        </div>

      </div>

      {/* Matched results + unmatched tables scroll area */}
      {(reconMatchedRows.length > 0 || reconMatchedGroups.length > 0 || reconUnmatchedRows.bank.length > 0 || reconUnmatchedRows.ledger.length > 0) && (
        <div className="recon-results-area">
          <ReconExportToolbar
            matchedGroups={reconMatchedGroups}
            partialTransactions={reconPartialTxns}
            unmatchedBank={reconUnmatchedTxns.bank}
            unmatchedLedger={reconUnmatchedTxns.ledger}
          />
          {reconMatchedRows.length > 0 && (
            <div className="recon-result-block">
              <div className="recon-section-header">Matched Results</div>
              <EditableSpreadsheet
                data={reconMatchedRows}
                columnsOverride={reconMatchedColumns}
                headersOverride={reconMatchedColumns}
                enableRowExpand
                categoryOptions={getCategoryOptionsForMode('RECON')}
                onDataChange={(newRows) => {
                  const removed = reconMatchedRows.filter(
                    pr => !newRows.some(nr => nr.id === pr.id),
                  )
                  if (removed.length === 0) {
                    setReconMatchedRows(newRows)
                    return
                  }
                  if (removed.every(r => isReconNonGroupMatchedRow(r))) {
                    setReconMatchedRows(newRows)
                    return
                  }

                  const snapshotBefore = reconMatchedRows
                  setReconMatchedRows(newRows)

                  void (async () => {
                    if (removed.length > 1) {
                      const prevRowsByGroup = new Map<string, number>()
                      snapshotBefore.forEach((r: any) => {
                        const gid = String(r['Group ID'] || '')
                        if (gid) prevRowsByGroup.set(gid, (prevRowsByGroup.get(gid) ?? 0) + 1)
                      })
                      const newRowsByGroup = new Map<string, number>()
                      newRows.forEach((r: any) => {
                        const gid = String(r['Group ID'] || '')
                        if (gid) newRowsByGroup.set(gid, (newRowsByGroup.get(gid) ?? 0) + 1)
                      })
                      const touched = new Set<string>()
                      prevRowsByGroup.forEach((count, gid) => {
                        if ((newRowsByGroup.get(gid) ?? 0) < count) touched.add(gid)
                      })
                      for (const gid of touched) handleGroupRemoved(gid)
                      reconciliationApi.getPartialTransactions()
                        .then(res => setReconPartialTxns(res.partial_transactions))
                        .catch(() => {})
                      return
                    }

                    const r0 = removed[0] as any
                    const gid = String(r0._recon_group_id || r0['Group ID'] || '')
                    const txnId = r0._recon_txn_id as string | undefined
                    const txnType = r0._recon_txn_type as 'bank' | 'ledger' | undefined
                    const isLegacy = Boolean(r0._recon_is_legacy)
                    const grp = reconMatchedGroups.find(g => g.id === gid)

                    const rowsInGroupBefore = snapshotBefore.filter(
                      (r: any) => String(r._recon_group_id || r['Group ID'] || '') === gid,
                    ).length
                    const rowsInGroupAfter = newRows.filter(
                      (r: any) => String(r._recon_group_id || r['Group ID'] || '') === gid,
                    ).length

                    const canMemberUnmatch =
                      Boolean(grp && !isLegacy && txnId && txnType) &&
                      rowsInGroupAfter > 0 &&
                      rowsInGroupAfter < rowsInGroupBefore

                    if (canMemberUnmatch && grp && txnId && txnType) {
                      try {
                        const res = await reconciliationApi.groupUnmatchMember({
                          group_id: gid,
                          txn_id: String(txnId),
                          txn_type: txnType,
                          reason: 'user_removed_from_sheet',
                        })
                        if (res.group_dissolved) {
                          handleGroupRemoved(gid)
                        } else {
                          onGroupUnmatched?.(
                            txnType === 'bank' ? [String(txnId)] : [],
                            txnType === 'ledger' ? [String(txnId)] : [],
                            gid,
                          )
                          onRestoreMemberToUnmatched?.(grp, String(txnId), txnType)
                          if (onReconGroupsRefresh) await onReconGroupsRefresh()
                          else {
                            const { groups } = await reconciliationApi.fetchGroups()
                            setReconMatchedGroups(
                              groups.length ? mapApiReconciliationGroupsToMatched(groups) : [],
                            )
                          }
                        }
                        const p = await reconciliationApi.getPartialTransactions()
                        setReconPartialTxns(p.partial_transactions)
                      } catch (e) {
                        setReconMatchedRows(snapshotBefore)
                        setReconStatusText(
                          `Failed to remove match member: ${e instanceof Error ? e.message : String(e)}`,
                        )
                      }
                      return
                    }

                    if (isLegacy && gid) {
                      try {
                        await reconciliationApi.unmatch({
                          match_id: gid,
                          reason: 'user_removed_from_sheet',
                        })
                        handleGroupRemoved(gid)
                        reconciliationApi.getPartialTransactions()
                          .then(res => setReconPartialTxns(res.partial_transactions))
                          .catch(() => {})
                      } catch (e) {
                        setReconMatchedRows(snapshotBefore)
                        setReconStatusText(
                          `Failed to unmatch: ${e instanceof Error ? e.message : String(e)}`,
                        )
                      }
                      return
                    }

                    const prevRowsByGroup = new Map<string, number>()
                    snapshotBefore.forEach((r: any) => {
                      const g = String(r['Group ID'] || '')
                      if (g) prevRowsByGroup.set(g, (prevRowsByGroup.get(g) ?? 0) + 1)
                    })
                    const newRowsByGroup = new Map<string, number>()
                    newRows.forEach((r: any) => {
                      const g = String(r['Group ID'] || '')
                      if (g) newRowsByGroup.set(g, (newRowsByGroup.get(g) ?? 0) + 1)
                    })
                    const touchedGroupIds = new Set<string>()
                    prevRowsByGroup.forEach((count, g) => {
                      if ((newRowsByGroup.get(g) ?? 0) < count) touchedGroupIds.add(g)
                    })
                    if (touchedGroupIds.size === 0) return
                    for (const g of touchedGroupIds) handleGroupRemoved(g)
                    reconciliationApi.getPartialTransactions()
                      .then(res => setReconPartialTxns(res.partial_transactions))
                      .catch(() => {})
                  })()
                }}
              />
            </div>
          )}

          <ReconPartialStrip partialTransactions={reconPartialTxns} />

          {(reconMatchedGroups.length > 0 || reconUnmatchedRows.bank.length > 0 || reconUnmatchedRows.ledger.length > 0) && (
            <div className="recon-result-block">
              <ReconciliationTable
                embeddedInReconRightPanel
                matchedGroups={reconMatchedGroups}
                unmatchedBank={reconUnmatchedTxns.bank}
                unmatchedLedger={reconUnmatchedTxns.ledger}
                partialTransactions={reconPartialTxns}
                onMatchComplete={handleMatchComplete}
                onGroupRemoved={(gid) => {
                  void reconciliationApi.glDeleteDraftByGroup(gid)
                  handleGroupRemoved(gid)
                }}
                onGroupUnmatched={onGroupUnmatched}
                onMatchedGroupsRefresh={onReconGroupsRefresh}
                onRestoreMemberToUnmatched={onRestoreMemberToUnmatched}
              />
              {reconMatchedGroups.length > 0 && (
                <ReconGlSection
                  matchedGroups={reconMatchedGroups}
                  coaList={coaList}
                  onGlAccountCodesSynced={onGlAccountCodesSynced}
                  glJournalRefetchSignal={glJournalRefetchSignal}
                  glApplyPatchSeeds={glApplyPatchSeeds}
                  onPrimaryJournalStatusByGroup={onPrimaryJournalStatusByGroup}
                  onGlVoucherNoByGroup={onGlVoucherNoByGroup}
                  scrollTargetGroupId={reconScrollTargetGroupId}
                  onScrollTargetConsumed={onReconScrollTargetConsumed}
                />
              )}
              {(reconUnmatchedRows.bank.length > 0 || reconUnmatchedRows.ledger.length > 0) && (
                <>
                  <div className="recon-section-header" style={{ marginTop: 12 }}>Unmatched - Bank</div>
                  {reconUnmatchedRows.bank.length > 0 ? (
                    <EditableSpreadsheet
                      data={reconUnmatchedRows.bank}
                      enableRowExpand
                      categoryOptions={getCategoryOptionsForMode('BANK')}
                      onDataChange={(d) => setReconUnmatchedRows(prev => ({ ...prev, bank: d }))}
                    />
                  ) : <div className="recon-empty">No unmatched bank transactions</div>}
                  <div className="recon-section-header">Unmatched - AR/AP</div>
                  {reconUnmatchedRows.ledger.length > 0 ? (
                    <EditableSpreadsheet
                      data={reconUnmatchedRows.ledger}
                      enableRowExpand
                      categoryOptions={getCategoryOptionsForMode('RECON')}
                      onDataChange={(d) => setReconUnmatchedRows(prev => ({ ...prev, ledger: d }))}
                    />
                  ) : <div className="recon-empty">No unmatched AR/AP transactions</div>}
                </>
              )}
            </div>
          )}
        </div>
      )}
      </div>
    </div>
  )
}
