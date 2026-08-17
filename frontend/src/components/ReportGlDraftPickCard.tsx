import type { GlDraftConflict } from '../utils/mergeGlJournalsForReport'
import './ReportGlDraftPickCard.css'

type BaseOpts = {
  dateFrom: string
  dateTo: string
  suspenseCode: string
  arControlCode: string
  apControlCode: string
  bankCode: string
}

type Props = {
  conflicts: GlDraftConflict[]
  baseOpts: BaseOpts
  onPick: (groupKey: string, journalId: string) => void
}

export function ReportGlDraftPickCard({ conflicts, baseOpts, onPick }: Props) {
  if (conflicts.length === 0) return null
  return (
    <div className="report-gl-draft-pick">
      <div className="report-gl-draft-pick-title">
        Multiple draft vouchers for the same match — pick which one to use for the trial balance.
      </div>
      <ul className="report-gl-draft-pick-list">
        {conflicts.map(c => (
          <li key={c.groupKey} className="report-gl-draft-pick-group">
            <div className="report-gl-draft-pick-group-key">Group: <code>{c.groupKey}</code></div>
            <div className="report-gl-draft-pick-options">
              {c.options.map((j, idx) => (
                <button
                  key={j.id}
                  type="button"
                  className="report-gl-draft-pick-btn"
                  onClick={() => onPick(c.groupKey, j.id)}
                  title={`${baseOpts.dateFrom}–${baseOpts.dateTo}`}
                >
                  <span className="report-gl-draft-pick-label">Voucher {idx + 1}</span>
                  <span className="report-gl-draft-pick-vno">{j.voucher_no}</span>
                  <span className="report-gl-draft-pick-meta">
                    {j.journal_date?.slice(0, 10) ?? '—'} · Dr {j.total_debit?.toLocaleString('en-HK', { minimumFractionDigits: 2 }) ?? '0'}
                  </span>
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
