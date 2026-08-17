import './ReconContainer.css'
import type { SpreadsheetRow } from './EditableSpreadsheet'
import { AnimatePresence, motion } from 'framer-motion'
import { useCallback, useEffect, useRef } from 'react'

export type ReconTransactionItem = {
  uid: string
  kind: 'source' | 'bank'
  txnId: string
  voucherNo: string
  date: string
  amount: number
  amountText: string
  memo: string
  bank: string
  currency: string
  recordTitle: string
  recordMode: string
  matchable: boolean
  row: SpreadsheetRow
}

type ReconContainerProps = {
  sourceTransactions: ReconTransactionItem[]
  bankTransactions: ReconTransactionItem[]
  selectedSourceTxnIds: string[]
  selectedBankTxnIds: string[]
  matchedSourceUids?: string[]
  matchedBankUids?: string[]
  matchResult?: { matchedCount: number; timestamp: number } | null
  isProcessing?: boolean
  onAllDrag: () => void
  onClearContainer: () => void
  onSelectSource: (txnUid: string) => void
  onSelectBank: (txnUid: string) => void
  onRemoveSource: (txnUid: string) => void
  onRemoveBank: (txnUid: string) => void
  /** Legacy rule-based match (kept for fallback) */
  onMatch: () => void
  /** New AI-powered match + duplicate detection */
  onAIMatch?: () => void
  onCheckDuplicates?: () => void
  duplicateAlertCount?: number
  /** Push a message into the parent's AI chat list */
  onPushMessage?: (text: string, role?: 'user' | 'assistant') => void
  /** When RECON has no BANK tasks, internal "bank" pool is still second ledger file — avoid BANK wording. */
  reconLedgerOnlyPools?: boolean
}

type DragPayload = {
  type: 'source' | 'bank'
  uid: string
}

export function ReconContainer({
  sourceTransactions,
  bankTransactions,
  selectedSourceTxnIds,
  selectedBankTxnIds,
  matchedSourceUids = [],
  matchedBankUids = [],
  matchResult,
  isProcessing,
  onAllDrag,
  onClearContainer,
  onSelectSource,
  onSelectBank,
  onRemoveSource,
  onRemoveBank,
  onMatch,
  onAIMatch,
  onCheckDuplicates,
  duplicateAlertCount,
  onPushMessage,
  reconLedgerOnlyPools = false,
}: ReconContainerProps) {
  const push = useCallback((text: string, role: 'user' | 'assistant' = 'assistant') => {
    onPushMessage?.(text, role)
  }, [onPushMessage])

  // Notify chat when a match completes
  const prevMatchTimestampRef = useRef<number | null>(null)
  useEffect(() => {
    if (matchResult && matchResult.timestamp !== prevMatchTimestampRef.current) {
      prevMatchTimestampRef.current = matchResult.timestamp
      if (matchResult.matchedCount > 0) {
        push(`Match complete: ${matchResult.matchedCount} record(s). See the match results below.`)
      } else {
        push('No matching transactions found.')
      }
    }
  }, [matchResult, push])

  const lockedUidsSet = new Set([...matchedSourceUids, ...matchedBankUids])

  const selectedSourceSet = new Set(selectedSourceTxnIds)
  const selectedBankSet = new Set(selectedBankTxnIds)
  const pendingSource = sourceTransactions.filter((r) => !selectedSourceSet.has(r.uid))
  const pendingBank = bankTransactions.filter((r) => !selectedBankSet.has(r.uid))
  const selectedSourceRows = sourceTransactions.filter((r) => selectedSourceSet.has(r.uid))
  const selectedBankRows = bankTransactions.filter((r) => selectedBankSet.has(r.uid))
  const selectedRows = [...selectedSourceRows, ...selectedBankRows]
  const selectedMatchableSource = selectedSourceRows.filter((r) => r.matchable)
  const selectedMatchableBank = selectedBankRows.filter((r) => r.matchable)
  // Align with App handleRunRecon: cross-side; ledger-only (≥2 or 1 pending); bank-only (≥2 or 1 clear)
  const canMatch =
    !isProcessing &&
    ((selectedMatchableSource.length >= 1 && selectedMatchableBank.length >= 1) ||
      (selectedMatchableSource.length >= 2 && selectedMatchableBank.length === 0) ||
      (selectedMatchableSource.length === 1 && selectedMatchableBank.length === 0) ||
      (selectedMatchableBank.length >= 2 && selectedMatchableSource.length === 0) ||
      (selectedMatchableBank.length === 1 && selectedMatchableSource.length === 0))

  const poolTag = (kind: 'source' | 'bank') => {
    if (reconLedgerOnlyPools) return kind === 'source' ? 'A' : 'B'
    return kind === 'source' ? 'SRC' : 'BANK'
  }

  const renderMatchBadge = (matchable: boolean) => (
    <span className={`recon-match-badge ${matchable ? 'is-matchable' : 'is-missing-id'}`}>
      {matchable ? 'Matchable' : 'No ID'}
    </span>
  )

  const handleDrop = (
    event: React.DragEvent<HTMLDivElement>,
    target: 'source' | 'bank'
  ) => {
    event.preventDefault()
    try {
      const raw = event.dataTransfer.getData('application/json')
      if (!raw) return
      const payload = JSON.parse(raw) as DragPayload
      if (payload.type !== target) return
      if (target === 'source') onSelectSource(payload.uid)
      if (target === 'bank') onSelectBank(payload.uid)
      push('Dragged 1 record into the container.')
    } catch {
      // ignore malformed drag payload
    }
  }

  const handleDropAny = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    try {
      const raw = event.dataTransfer.getData('application/json')
      if (!raw) return
      const payload = JSON.parse(raw) as DragPayload
      if (payload.type === 'source') onSelectSource(payload.uid)
      if (payload.type === 'bank') onSelectBank(payload.uid)
      push('Dragged 1 record into the container.')
    } catch {
      // ignore malformed
    }
  }

  const handleAllDrag = () => {
    const incoming = pendingSource.length + pendingBank.length
    onAllDrag()
    push(`Dragged ${incoming} records into the container.`)
  }

  const handleMatch = () => {
    push('Matching selected records...')
    onMatch()
  }

  const renderSelectedCard = (row: ReconTransactionItem) => {
    const isLocked = lockedUidsSet.has(row.uid)
    return (
      <motion.div
        key={row.uid}
        className={`recon-selected-chip${isLocked ? ' recon-selected-chip-locked' : ''}`}
        layout
        layoutId={`recon-txn-${row.uid}`}
        initial={{ opacity: 0, y: 8, scale: 0.96 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: -8, scale: 0.96 }}
        transition={{ duration: 0.2 }}
      >
        <span>
          <span className="recon-kind-tag">{poolTag(row.kind)}</span>{' '}
          {row.voucherNo || '(No voucher)'} · {row.amountText} {renderMatchBadge(row.matchable)}
          {isLocked && <span className="recon-locked-badge">MATCHED</span>}
        </span>
        {!isLocked && (
          <button
            onClick={() => {
              if (row.kind === 'source') onRemoveSource(row.uid)
              if (row.kind === 'bank') onRemoveBank(row.uid)
            }}
          >
            ×
          </button>
        )}
      </motion.div>
    )
  }

  return (
    <div className="recon-matchbar">
      {/* Drop zone — visible when chips are present, or always as a drag target */}
      <div
        className={`recon-drop-zone${selectedRows.length === 0 ? ' recon-drop-zone-empty' : ''}`}
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDropAny}
      >
        {selectedRows.length === 0 ? (
          <span className="recon-drop-placeholder">Click + or drag transactions here to match…</span>
        ) : (
          <AnimatePresence initial={false}>
            {selectedRows.map(renderSelectedCard)}
          </AnimatePresence>
        )}
      </div>

      {/* Input + actions row */}
      <div
        className="recon-composer-input"
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDropAny}
      >
        <input
          className="recon-text-input"
          type="text"
          placeholder="Type a RECON message or drag records above…"
          readOnly
        />
        <div className="recon-composer-actions">
          <button className="recon-action-btn" onClick={handleAllDrag} disabled={isProcessing}>
            All Drag
          </button>
          {onAIMatch ? (
            <button
              className="recon-action-btn recon-action-ai-match"
              onClick={() => { push('AI is analysing duplicates and matches…'); onAIMatch() }}
              disabled={isProcessing || selectedRows.length === 0}
              title="Detect duplicates and match with AI"
            >
              {isProcessing ? 'Analysing…' : 'AI Match'}
            </button>
          ) : (
            <button className="recon-action-btn recon-action-match" onClick={handleMatch} disabled={!canMatch}>
              {isProcessing ? 'Matching…' : 'Match'}
            </button>
          )}
          <button className="recon-action-btn" onClick={onClearContainer} disabled={isProcessing}>
            Clear
          </button>
        </div>
      </div>

      {/* Status bar */}
      <div className="recon-composer-bottom">
        {reconLedgerOnlyPools ? (
          <>
            Pool A: {selectedMatchableSource.length}/{selectedSourceRows.length} matchable · Pool B:{' '}
            {selectedMatchableBank.length}/{selectedBankRows.length} matchable
          </>
        ) : (
          <>
            Source: {selectedMatchableSource.length}/{selectedSourceRows.length} matchable · Bank:{' '}
            {selectedMatchableBank.length}/{selectedBankRows.length} matchable
          </>
        )}
      </div>
    </div>
  )
}
