import { useState, useCallback } from 'react'
import type { PartialTransaction } from './ReconciliationTable'
import './ReconciliationTable.css'

function fmtAmt(n: number): string {
  return n.toLocaleString('en-HK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export interface ReconPartialStripProps {
  partialTransactions: PartialTransaction[]
}

export function ReconPartialStrip({ partialTransactions }: ReconPartialStripProps) {
  const [expandedPartialIds, setExpandedPartialIds] = useState<Set<string>>(new Set())

  const togglePartialExpand = useCallback((id: string) => {
    setExpandedPartialIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  if (partialTransactions.length === 0) return null

  return (
    <div className="recon-partial-strip recon-section">
      <h3 className="recon-partial-strip-title">Partial matches ({partialTransactions.length})</h3>
      <div className="recon-partial-strip-list">
        {partialTransactions.map(pt => {
          const isExpanded = expandedPartialIds.has(pt.id)
          const grp = pt.group
          return (
            <div key={pt.id} className="recon-partial-strip-card">
              <button
                type="button"
                className="recon-partial-strip-summary"
                onClick={() => togglePartialExpand(pt.id)}
              >
                <span className="recon-cardinality-badge recon-cardinality-partial">Partial</span>
                <span className="recon-partial-strip-v">{pt.reference || pt.description_raw.slice(0, 24)}</span>
                <span className="recon-partial-strip-amt">{fmtAmt(pt.amount)} {pt.currency}</span>
                <span className="recon-expand-btn" aria-hidden>{isExpanded ? '▲' : '▼'}</span>
              </button>
              {isExpanded && grp && (
                <div className="recon-partial-breakdown recon-partial-strip-detail">
                  <div className="recon-breakdown-title">
                    Partial match — Group {grp.id.slice(0, 8)}… ({grp.match_cardinality})
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
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
