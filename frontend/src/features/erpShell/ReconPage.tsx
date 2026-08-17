import { useMemo, useState } from 'react'

import { useAuth } from '../../contexts/AuthContext'

import { ReconExportToolbar } from '../../components/ReconExportToolbar'

import { ReconGlSection } from '../../components/ReconGlSection'

import { useReconWorkspace } from '../recon/useReconWorkspace'
import type { ReconGlChoice } from '../recon/reconTypes'

import { ReconAiPanel } from './ReconAiPanel'

import { ReconDualGrid } from './ReconDualGrid'



function fmtMoney(n: number): string {

  return n.toLocaleString('en-HK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

}



export function ReconPage() {

  const { activeCompany } = useAuth()

  const ws = useReconWorkspace(activeCompany?.id ?? null)
  const [glActionRequest, setGlActionRequest] = useState<{
    groupId: string
    action: 'approve' | 'edit' | 'unpost'
  } | null>(null)

  const handleGlChoice = (groupId: string, choice: ReconGlChoice) => {
    if (choice === 'skip') return
    if (choice === 'cancel') {
      void ws.handleCancelMatchedGroup(groupId)
      return
    }
    ws.setReconScrollTargetGroupId(groupId)
    setGlActionRequest({ groupId, action: choice })
  }

  const currencyWarning = useMemo(() => {
    const norm = (c: string | undefined) => (c ?? '').trim().toUpperCase()
    const currencies = new Set<string>()
    ws.selectedBankIds.forEach(id => {
      const c = norm(ws.bankRowById.get(id)?.currency)
      if (c) currencies.add(c)
    })
    ws.selectedLedgerIds.forEach(id => {
      const c = norm(ws.ledgerRowById.get(id)?.currency)
      if (c) currencies.add(c)
    })
    if (currencies.size > 1) return 'Cross-currency matching is not supported in v1.'
    return null
  }, [ws.bankRowById, ws.ledgerRowById, ws.selectedBankIds, ws.selectedLedgerIds])

  const hasSelection = ws.selectedBankIds.length > 0 || ws.selectedLedgerIds.length > 0
  const showActionBar =
    !ws.needsSelection && ws.bankRows.length > 0 && ws.ledgerRows.length > 0
  const busy = ws.loading || ws.aiThinking || ws.needsSelection

  const clearSelection = () => {

    ws.setSelectedBankIds([])

    ws.setSelectedLedgerIds([])

  }



  return (

    <div className="erp-recon">

      <div className="erp-recon-filters">

        <label>

          From

          <input

            type="date"

            value={ws.filters.dateFrom}

            onChange={e => ws.setFilters(f => ({ ...f, dateFrom: e.target.value }))}

          />

        </label>

        <label>

          To

          <input

            type="date"

            value={ws.filters.dateTo}

            onChange={e => ws.setFilters(f => ({ ...f, dateTo: e.target.value }))}

          />

        </label>

        <label>

          Bank account

          <select

            value={ws.filters.bankAccount}

            onChange={e => ws.setFilters(f => ({ ...f, bankAccount: e.target.value }))}

          >

            <option value="">All</option>

            {ws.bankAccounts.map(a => (

              <option key={a} value={a}>

                {a}

              </option>

            ))}

          </select>

        </label>

        <label>

          Ledger

          <select

            value={ws.filters.ledgerType}

            onChange={e =>

              ws.setFilters(f => ({ ...f, ledgerType: e.target.value as 'all' | 'AR' | 'AP' }))

            }

          >

            <option value="all">All</option>

            <option value="AR">AR</option>

            <option value="AP">AP</option>

          </select>

        </label>

        <button

          type="button"

          className="erp-btn"

          disabled={ws.loading || ws.needsSelection}

          onClick={() => void ws.loadTransactions()}

        >

          Refresh

        </button>

        {ws.statusText && <span className="erp-recon-status">{ws.statusText}</span>}

      </div>



      {ws.needsSelection ? (

        <div className="erp-note erp-recon-empty">

          Loading Books transactions into Reconciliation…

        </div>

      ) : (

      <div className="erp-recon-main">

        <div className="erp-recon-grids-col">

          {showActionBar && (
            <div className="erp-recon-selection-bar">
              <span className="erp-recon-selection-label">
                {hasSelection
                  ? `${ws.selectedBankIds.length} bank${
                      ws.selectedLedgerIds.length > 0 ? ` + ${ws.selectedLedgerIds.length} ledger` : ''
                    } selected`
                  : 'Select rows in either grid to match'}
              </span>
              <div className="erp-recon-selection-actions">
                <button
                  type="button"
                  className="erp-btn primary"
                  disabled={busy || !hasSelection}
                  onClick={() => void ws.handleMatch()}
                >
                  Match
                </button>
                <button
                  type="button"
                  className="erp-btn"
                  disabled={busy || !ws.canAiMatch}
                  title={ws.canAiMatch ? 'Run AI matching on selected rows' : ws.aiMatchHint ?? undefined}
                  onClick={() => void ws.handleAIMatch()}
                >
                  {ws.aiThinking ? 'AI Matching…' : 'AI Match'}
                </button>
                <button type="button" className="erp-btn" disabled={busy} onClick={clearSelection}>
                  Clear
                </button>
              </div>
              {currencyWarning && hasSelection && !ws.canAiMatch && (
                <div className="erp-recon-warn">{currencyWarning}</div>
              )}
              {!ws.canAiMatch && ws.aiMatchHint && (
                <div className="erp-recon-warn">{ws.aiMatchHint}</div>
              )}
            </div>
          )}

          <div className="erp-recon-grids">

            <ReconDualGrid

              title="Bank Transactions"

              rows={ws.bankRows}

              selectedIds={ws.selectedBankIds}

              matchedIds={ws.matchedTxnIds}

              onToggle={ws.toggleBankSelection}

              onToggleAll={ws.toggleBankSelectionAll}

            />

            <ReconDualGrid

              title="Ledger Entries"

              rows={ws.ledgerRows}

              selectedIds={ws.selectedLedgerIds}

              matchedIds={ws.matchedTxnIds}

              onToggle={ws.toggleLedgerSelection}

              onToggleAll={ws.toggleLedgerSelectionAll}

            />

          </div>

        </div>

        <ReconAiPanel

          messages={ws.aiMessages}

          thinking={ws.aiThinking}

          matchedGroups={ws.reconMatchedGroups}

          glStatusByGroupId={ws.glStatusByGroupId}

          glVoucherNoByGroupId={ws.glVoucherNoByGroupId}

          onSend={text => void ws.sendAiChat(text)}

          onApplyActions={id => void ws.handleApplyReconAiActions(id)}

          onResultPageChange={ws.handleResultPageChange}

          onGlChoice={handleGlChoice}

        />

      </div>

      )}



      <div className="erp-recon-footer">

        <div className="erp-recon-kpi">

          <span className="label">Matched groups</span>

          <span className="value">

            {ws.matchedCount} / {ws.totalEligible || '-'}

          </span>

        </div>

        <div className="erp-recon-kpi">

          <span className="label">Unreconciled pool</span>

          <span className="value">

            {ws.reconUnmatchedTxns.bank.length} bank / {ws.reconUnmatchedTxns.ledger.length} ledger

          </span>

        </div>

        <div className="erp-recon-kpi">

          <span className="label">Variance (bank - ledger)</span>

          <span className="value">{fmtMoney(ws.variance)}</span>

        </div>

      </div>



      <div className="erp-recon-toolbar">

        <ReconExportToolbar

          matchedGroups={ws.reconMatchedGroups}

          partialTransactions={ws.reconPartialTxns as any}

          unmatchedBank={ws.reconUnmatchedTxns.bank}

          unmatchedLedger={ws.reconUnmatchedTxns.ledger}

          glVoucherNoByGroupId={ws.glVoucherNoByGroupId}

        />

      </div>



      <ReconGlSection

        matchedGroups={ws.reconMatchedGroups}

        coaList={ws.coaList}

        glJournalRefetchSignal={ws.glJournalRefetchSignal}

        glApplyPatchSeeds={ws.glApplyPatchSeeds}

        onPrimaryJournalStatusByGroup={ws.setGlStatusByGroupId}

        onGlVoucherNoByGroup={v => ws.setGlVoucherNoByGroupId(prev => ({ ...prev, ...v }))}

        scrollTargetGroupId={ws.reconScrollTargetGroupId}

        onScrollTargetConsumed={() => ws.setReconScrollTargetGroupId(null)}

        actionRequest={glActionRequest}

        onActionRequestConsumed={() => setGlActionRequest(null)}

        collapseTables

      />

    </div>

  )

}


