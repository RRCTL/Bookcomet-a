import { useState } from 'react'
import type { MatchedGroupRow } from '../../components/ReconciliationTable'
import type { ReconAiMessage, ReconGlChoice } from '../recon/reconTypes'

function fmtMoney(n: number): string {
  return n.toLocaleString('en-HK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtProcessedAt(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('en-HK', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

type Props = {
  messages: ReconAiMessage[]
  thinking: boolean
  matchedGroups: MatchedGroupRow[]
  glStatusByGroupId: Record<string, string>
  glVoucherNoByGroupId: Record<string, string>
  onSend: (text: string) => void
  onApplyActions: (messageId: string) => void
  onResultPageChange: (messageId: string, pageIndex: number) => void
  onGlChoice: (groupId: string, choice: ReconGlChoice) => void
}

export function ReconAiPanel({
  messages,
  thinking,
  matchedGroups,
  glStatusByGroupId,
  glVoucherNoByGroupId,
  onSend,
  onApplyActions,
  onResultPageChange,
  onGlChoice,
}: Props) {
  const [input, setInput] = useState('')
  const groupById = new Map(matchedGroups.map(g => [g.id, g]))

  const submit = () => {
    const t = input.trim()
    if (!t || thinking) return
    setInput('')
    onSend(t)
  }

  return (
    <aside className="erp-ai-panel">
      <div className="erp-ai-panel-head">AI Assistant</div>
      <div className="erp-ai-stream">
        {messages.map(msg => {
          const review = msg.resultReview
          const total = review?.groupIds.length ?? 0
          const page = review ? Math.max(0, Math.min(review.pageIndex, Math.max(0, total - 1))) : 0
          const groupId = review?.groupIds[page]
          const grp = groupId ? groupById.get(groupId) : undefined
          const status = groupId ? (glStatusByGroupId[groupId] || '').toLowerCase() : ''
          const voucher = groupId ? glVoucherNoByGroupId[groupId] || '' : ''
          const cur = (grp?.currency || 'HKD').trim() || 'HKD'
          const isDraft = status === 'draft' || (!status && Boolean(grp))
          const isPosted = status === 'posted'
          const isGlOnly = (grp?.match_cardinality || '') === 'GL:1'
          const whenLabel = fmtProcessedAt(grp?.created_at || review?.processedAt)

          return (
            <div key={msg.id} className={`erp-ai-bubble ${msg.role}`}>
              <div className="erp-ai-bubble-body">{msg.content}</div>

              {review && total > 0 && (
                <div className="erp-ai-mc">
                  <div className="erp-ai-mc-summary">
                    <div className="erp-ai-mc-title">
                      {voucher || `Group ${groupId?.slice(0, 8) ?? ''}…`}
                      {isGlOnly ? (
                        <span className="erp-ai-mc-badge">GL only</span>
                      ) : null}
                      {status ? (
                        <span className={`erp-ai-mc-badge${isPosted ? ' posted' : ''}`}>
                          {isPosted ? 'Posted' : status === 'draft' ? 'Draft' : status}
                        </span>
                      ) : null}
                    </div>
                    {whenLabel ? <div className="erp-ai-mc-when">Matched: {whenLabel}</div> : null}
                    {grp ? (
                      <ul className="erp-ai-mc-kv">
                        <li>
                          <span>Bank</span>
                          <b>
                            {cur} {fmtMoney(grp.bank_total)}
                          </b>
                        </li>
                        <li>
                          <span>Ledger</span>
                          <b>
                            {cur} {fmtMoney(grp.ledger_total)}
                          </b>
                        </li>
                        <li>
                          <span>Variance</span>
                          <b>
                            {cur} {fmtMoney(grp.difference)}
                          </b>
                        </li>
                        <li>
                          <span>Members</span>
                          <b>
                            {(grp.bank_txn_ids?.length ?? 0)} bank / {(grp.ledger_txn_ids?.length ?? 0)} ledger
                          </b>
                        </li>
                      </ul>
                    ) : (
                      <p className="erp-ai-mc-missing">Group no longer in matched list.</p>
                    )}
                  </div>

                  <div className="erp-ai-mc-choices" role="group" aria-label="Reconciliation actions">
                    {grp && isDraft && (
                      <button
                        type="button"
                        className="erp-ai-mc-choice primary"
                        onClick={() => onGlChoice(grp.id, 'approve')}
                      >
                        Approve
                      </button>
                    )}
                    {grp && !isGlOnly && (
                      <button
                        type="button"
                        className="erp-ai-mc-choice"
                        onClick={() => onGlChoice(grp.id, 'edit')}
                      >
                        Edit draft
                      </button>
                    )}
                    {grp && isPosted && (
                      <button
                        type="button"
                        className="erp-ai-mc-choice"
                        onClick={() => onGlChoice(grp.id, 'unpost')}
                      >
                        Cancel approval
                      </button>
                    )}
                    {grp && !isPosted && (
                      <button
                        type="button"
                        className="erp-ai-mc-choice danger"
                        onClick={() => onGlChoice(grp.id, 'cancel')}
                      >
                        Cancel match
                      </button>
                    )}
                    {total > 1 && page < total - 1 && (
                      <button
                        type="button"
                        className="erp-ai-mc-choice"
                        onClick={() => {
                          onGlChoice(groupId!, 'skip')
                          onResultPageChange(msg.id, page + 1)
                        }}
                      >
                        Skip to next group
                      </button>
                    )}
                  </div>

                  {total > 1 && (
                    <div className="erp-ai-mc-pager">
                      <button
                        type="button"
                        className="erp-btn sm"
                        disabled={page <= 0}
                        onClick={() => onResultPageChange(msg.id, page - 1)}
                      >
                        Prev
                      </button>
                      <span>
                        {page + 1} / {total}
                      </span>
                      <button
                        type="button"
                        className="erp-btn sm"
                        disabled={page >= total - 1}
                        onClick={() => onResultPageChange(msg.id, page + 1)}
                      >
                        Next
                      </button>
                    </div>
                  )}
                </div>
              )}

              {msg.reconActionsPending && msg.reconActions && msg.reconActions.length > 0 && (
                <div className="erp-ai-bubble-actions">
                  <button type="button" className="erp-btn primary sm" onClick={() => onApplyActions(msg.id)}>
                    Apply
                  </button>
                  <details className="erp-ai-actions-detail">
                    <summary>Review actions</summary>
                    <pre>{JSON.stringify(msg.reconActions, null, 2)}</pre>
                  </details>
                </div>
              )}
            </div>
          )
        })}
        {thinking && (
          <div className="erp-ai-bubble assistant">
            <div className="erp-ai-bubble-body">Working…</div>
          </div>
        )}
      </div>
      <div className="erp-ai-composer">
        <textarea
          value={input}
          rows={2}
          placeholder="Ask about matches, duplicates, or GL drafts…"
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
        />
        <button type="button" className="erp-btn primary" disabled={thinking || !input.trim()} onClick={submit}>
          Send
        </button>
      </div>
    </aside>
  )
}
