import { useEffect, useMemo, useRef, useState } from 'react'
import { reconciliationApi, type GlJournalPayload } from '../services/reconciliation'
import type { ChartOfAccountItem } from '../types/reconciliation'
import './ReconGlSection.css'

function fmt(n: number) {
  return (Math.round(n * 100) / 100).toLocaleString('en-HK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

const NEW_PREFIX = 'new:'

function newLineId(): string {
  return `${NEW_PREFIX}${crypto.randomUUID()}`
}

type ModalLineState = {
  id: string
  account_code: string
  /** Source txn reference / voucher evidence (read-only display). */
  reference: string
  debit: number
  credit: number
}

function linesToModalState(lines: GlJournalPayload['lines']): ModalLineState[] {
  return lines.map(ln => ({
    id: ln.id,
    account_code: ln.account_code,
    reference: (ln.memo || '').trim(),
    debit: Number(ln.debit) || 0,
    credit: Number(ln.credit) || 0,
  }))
}

function lineUnchanged(orig: GlJournalPayload['lines'][0], st: ModalLineState): boolean {
  return (
    orig.account_code === st.account_code &&
    Math.round((Number(orig.debit) || 0) * 100) === Math.round(st.debit * 100) &&
    Math.round((Number(orig.credit) || 0) * 100) === Math.round(st.credit * 100)
  )
}

function defaultAccountForRow(coaList: ChartOfAccountItem[], rows: ModalLineState[], index: number): string {
  if (index > 0 && rows[index - 1]?.account_code) return rows[index - 1].account_code
  return coaList[0]?.code ?? ''
}

function padToMinRows(rows: ModalLineState[], coaList: ChartOfAccountItem[]): ModalLineState[] {
  const next = [...rows]
  while (next.length < 2) {
    const i = next.length
    next.push({
      id: newLineId(),
      account_code: defaultAccountForRow(coaList, next, i),
      reference: '',
      debit: 0,
      credit: 0,
    })
  }
  return next
}

type AleSide = { dr: number; cr: number }

/** Display-only roll-up by CoA category (informational, v1). */
function computeAleRollup(
  lineStates: ModalLineState[],
  coaByCode: Map<string, ChartOfAccountItem>,
): { assets: AleSide; liabilities: AleSide; equity: AleSide } {
  const assets: AleSide = { dr: 0, cr: 0 }
  const liabilities: AleSide = { dr: 0, cr: 0 }
  const equity: AleSide = { dr: 0, cr: 0 }
  const add = (side: AleSide, dr: number, cr: number) => {
    side.dr += dr
    side.cr += cr
  }
  for (const st of lineStates) {
    const dr = st.debit
    const cr = st.credit
    const cat = (coaByCode.get(st.account_code)?.category_type || '').toLowerCase()
    if (cat === 'asset' || cat === 'bank' || cat === 'cash') {
      add(assets, dr, cr)
    } else if (cat === 'liability' || cat === 'liabilities') {
      add(liabilities, dr, cr)
    } else if (cat === 'equity' || cat === 'revenue' || cat === 'other_income' || cat === 'expense' || cat === 'expenses') {
      add(equity, dr, cr)
    }
  }
  return { assets, liabilities, equity }
}

function accountIconKind(categoryType: string | undefined): 'bank' | 'ap' | 'neutral' {
  const c = (categoryType || '').toLowerCase()
  if (c === 'asset' || c === 'bank' || c === 'cash') return 'bank'
  if (c === 'liability' || c === 'liabilities') return 'ap'
  return 'neutral'
}

function AccountGlyph({ kind }: { kind: 'bank' | 'ap' | 'neutral' }) {
  if (kind === 'bank') {
    return (
      <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
        <path d="M2 10h20M4 10v9M20 10v9M8 14h2M14 14h2M6 19h12M3 19h18v2H3v-2zM12 3 2 10h20L12 3z" />
      </svg>
    )
  }
  if (kind === 'ap') {
    return (
      <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 3H6a2 2 0 00-2 2v14a2 2 0 002 2h12a2 2 0 002-2V8l-6-5z" />
        <path d="M14 3v5h5M10 13h4M10 17h4" />
      </svg>
    )
  }
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12h8" />
    </svg>
  )
}

export interface ReconGlJournalEditModalProps {
  open: boolean
  groupId: string
  journal: GlJournalPayload | null
  coaList: ChartOfAccountItem[]
  coaOptionLabel: (c: ChartOfAccountItem) => string
  busy: boolean
  onClose: () => void
  onSaved: (next: GlJournalPayload) => void
  /** After save: server pushes line account_code to linked txn account_category — use to refresh OCR/task rows. */
  onAccountCodesSynced?: (sync: { bank: Record<string, string>; ledger: Record<string, string> }) => void
}

export function ReconGlJournalEditModal({
  open,
  groupId,
  journal,
  coaList,
  coaOptionLabel,
  busy,
  onClose,
  onSaved,
  onAccountCodesSynced,
}: ReconGlJournalEditModalProps) {
  const [localErr, setLocalErr] = useState<string | null>(null)
  const [dateStr, setDateStr] = useState('')
  const [balCode, setBalCode] = useState('')
  const [lineStates, setLineStates] = useState<ModalLineState[]>([])
  const [saving, setSaving] = useState(false)
  const initialLinesRef = useRef<GlJournalPayload['lines'] | null>(null)

  const cur = journal?.currency || 'HKD'

  useEffect(() => {
    if (!open || !journal) return
    setLocalErr(null)
    setDateStr((journal.journal_date || '').slice(0, 10))
    setBalCode(journal.balancing_account_code || '')
    initialLinesRef.current = journal.lines.map(l => ({ ...l }))
    let rows = linesToModalState(journal.lines)
    // Manual journals may pad empty drafts; RECON group drafts must keep server lines
    // (padding zeros hid missing bank amounts for GL-only / inter-bank matches).
    if (rows.length === 0 && !journal.reconciliation_group_id) {
      rows = padToMinRows([], coaList)
    }
    setLineStates(rows)
  }, [open, journal, coaList])

  const totals = useMemo(() => {
    let td = 0
    let tc = 0
    for (const st of lineStates) {
      td += st.debit
      tc += st.credit
    }
    const balanced = Math.round((td - tc) * 100) === 0
    const hasMovement = td > 0.005 || tc > 0.005
    return { totalDebit: td, totalCredit: tc, balanced, hasMovement }
  }, [lineStates])

  const coaByCode = useMemo(() => new Map(coaList.map(c => [c.code, c])), [coaList])
  const ale = useMemo(() => computeAleRollup(lineStates, coaByCode), [lineStates, coaByCode])

  const canSubmit =
    coaList.length > 0 &&
    lineStates.length >= 2 &&
    totals.balanced &&
    totals.hasMovement

  if (!open || !journal || journal.status !== 'draft') return null

  const accountOptionsFor = (code: string) =>
    coaList.some(c => c.code === code)
      ? coaList
      : [...coaList, { code, name_en: '', name_zh: '', category_type: '-', allowed_modes: [] as string[] }]

  const removeRow = (idx: number) => {
    setLineStates(prev => {
      const next = prev.filter((_, i) => i !== idx)
      return padToMinRows(next, coaList)
    })
  }

  const addRow = () => {
    setLineStates(prev => {
      const last = prev[prev.length - 1]
      const acct = last?.account_code || coaList[0]?.code || ''
      return [...prev, { id: newLineId(), account_code: acct, reference: '', debit: 0, credit: 0 }]
    })
  }

  const setDebit = (idx: number, value: number) => {
    setLineStates(prev =>
      prev.map((r, i) => (i === idx ? { ...r, debit: value, credit: 0 } : r)),
    )
  }

  const setCredit = (idx: number, value: number) => {
    setLineStates(prev =>
      prev.map((r, i) => (i === idx ? { ...r, credit: value, debit: 0 } : r)),
    )
  }

  const handleOk = async () => {
    setLocalErr(null)
    setSaving(true)
    try {
      const origDate = (journal.journal_date || '').slice(0, 10)
      const origBal = journal.balancing_account_code || ''

      const initialLines = initialLinesRef.current || journal.lines
      const deleted_line_ids = initialLines
        .map(l => l.id)
        .filter(id => !lineStates.some(s => s.id === id))

      const linePatches: Array<{
        id?: string
        account_code: string
        debit: number
        credit: number
      }> = []

      for (const st of lineStates) {
        if (st.id.startsWith(NEW_PREFIX)) {
          linePatches.push({
            account_code: st.account_code,
            debit: st.debit,
            credit: st.credit,
          })
          continue
        }
        const orig = initialLines.find(l => l.id === st.id)
        if (!orig) {
          linePatches.push({
            id: st.id,
            account_code: st.account_code,
            debit: st.debit,
            credit: st.credit,
          })
          continue
        }
        if (!lineUnchanged(orig, st)) {
          linePatches.push({
            id: st.id,
            account_code: st.account_code,
            debit: st.debit,
            credit: st.credit,
          })
        }
      }

      const body: {
        journal_date?: string
        balancing_account_code?: string | null
        deleted_line_ids?: string[]
        lines?: Array<{ id?: string; account_code: string; debit: number; credit: number }>
      } = {}

      if (dateStr && dateStr !== origDate) {
        body.journal_date = new Date(dateStr + 'T12:00:00').toISOString()
      }
      if (balCode !== origBal) {
        body.balancing_account_code = balCode || null
      }
      if (deleted_line_ids.length) body.deleted_line_ids = deleted_line_ids
      if (linePatches.length) body.lines = linePatches

      let curJ = journal
      if (Object.keys(body).length > 0) {
        curJ = await reconciliationApi.glPatchJournal(journal.id, body)
      }

      try {
        const { sync, journal: jAfterSync } = await reconciliationApi.glSyncJournalLinesToTransactions(curJ.id)
        curJ = jAfterSync
        if (
          onAccountCodesSynced &&
          (Object.keys(sync.bank).length > 0 || Object.keys(sync.ledger).length > 0)
        ) {
          onAccountCodesSynced(sync)
        }
      } catch {
        /* posted / missing journal — draft modal should not hit this */
      }

      onSaved(curJ)
      onClose()
    } catch (e: unknown) {
      setLocalErr(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="recon-gl-modal-overlay" role="presentation" onMouseDown={e => e.target === e.currentTarget && !saving && !busy && onClose()}>
      <div
        className="recon-gl-modal recon-gl-modal--journal-entry"
        role="dialog"
        aria-labelledby="recon-gl-modal-title"
        onMouseDown={e => e.stopPropagation()}
      >
        <div className="recon-gl-modal-header">
          <div>
            <h3 id="recon-gl-modal-title">Journal entry</h3>
            <span className="recon-gl-modal-title-sub">GL voucher</span>
          </div>
          <button type="button" className="recon-gl-modal-close" onClick={onClose} disabled={saving || busy} aria-label="Close">
            ×
          </button>
        </div>
        <p className="recon-gl-modal-meta">
          Group <code>{groupId.slice(0, 8)}…</code>
          {journal.voucher_no ? ` · ${journal.voucher_no}` : null}
        </p>
        {journal.narration ? (
          <p className="recon-gl-modal-desc">{journal.narration}</p>
        ) : (
          <p className="recon-gl-modal-desc recon-gl-modal-desc-muted">No description</p>
        )}

        {localErr && <div className="recon-gl-error" style={{ marginBottom: 8 }}>{localErr}</div>}

        <label className="recon-gl-modal-field recon-gl-modal-field--date">
          <span>Journal date</span>
          <div className="recon-gl-modal-date-row">
            <input type="date" value={dateStr} onChange={e => setDateStr(e.target.value)} disabled={saving || busy} />
          </div>
        </label>

        <div className="recon-gl-modal-grid-wrap">
          <div className="recon-gl-modal-journal-scroll">
            <table className="recon-gl-table recon-gl-modal-lines recon-gl-modal-lines--simple">
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Reference</th>
                  <th className="recon-gl-amt">Debit</th>
                  <th className="recon-gl-amt">Credit</th>
                  <th className="recon-gl-modal-col-remove" aria-label="Remove row" />
                </tr>
              </thead>
              <tbody>
                {lineStates.map((st, idx) => {
                  const cat = coaByCode.get(st.account_code)?.category_type
                  const iconKind = accountIconKind(cat)
                  return (
                    <tr key={st.id}>
                      <td>
                        <div className="recon-gl-modal-account-cell">
                          <span className={`recon-gl-acct-icon recon-gl-acct-icon--${iconKind}`} aria-hidden title={cat || 'Account'}>
                            {iconKind === 'bank' ? '◆' : iconKind === 'ap' ? '▪' : '○'}
                          </span>
                          <select
                            value={st.account_code}
                            onChange={e => {
                              const v = e.target.value
                              setLineStates(prev => prev.map((r, i) => (i === idx ? { ...r, account_code: v } : r)))
                            }}
                            disabled={saving || busy || !coaList.length}
                            className="recon-gl-account-select"
                          >
                            {accountOptionsFor(st.account_code).map(c => (
                              <option key={c.code} value={c.code}>
                                {coaOptionLabel(c)}
                              </option>
                            ))}
                          </select>
                        </div>
                      </td>
                      <td className="recon-gl-modal-ref" title={st.reference || undefined}>
                        {st.reference || '—'}
                      </td>
                      <td className="recon-gl-amt">
                        <input
                          type="number"
                          step="0.01"
                          min="0"
                          className="recon-gl-modal-amt-input"
                          value={st.debit || ''}
                          placeholder="0"
                          onChange={e => {
                            const n = parseFloat(e.target.value) || 0
                            setDebit(idx, n)
                          }}
                          disabled={saving || busy}
                        />
                      </td>
                      <td className="recon-gl-amt">
                        <input
                          type="number"
                          step="0.01"
                          min="0"
                          className="recon-gl-modal-amt-input"
                          value={st.credit || ''}
                          placeholder="0"
                          onChange={e => {
                            const n = parseFloat(e.target.value) || 0
                            setCredit(idx, n)
                          }}
                          disabled={saving || busy}
                        />
                      </td>
                      <td className="recon-gl-modal-col-remove">
                        <button
                          type="button"
                          className="recon-gl-modal-icon-btn recon-gl-modal-icon-btn--active"
                          title="Remove line"
                          aria-label="Remove line"
                          disabled={saving || busy}
                          onClick={() => removeRow(idx)}
                        >
                          <svg className="recon-gl-modal-icon-trash" width="16" height="16" viewBox="0 0 24 24" aria-hidden fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                            <path d="M3 6h18M8 4v2M16 4v2M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14zM10 11v6M14 11v6" />
                          </svg>
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <div className="recon-gl-modal-add-row">
            <button type="button" className="recon-gl-modal-add-line recon-gl-modal-add-line--active" disabled={saving || busy || !coaList.length} onClick={addRow}>
              + Add row
            </button>
          </div>
          {!coaList.length ? <p className="recon-gl-modal-hint">Add chart of accounts to edit lines.</p> : null}
        </div>

        <label className="recon-gl-modal-field">
          Balancing account (replace suspense)
          <select
            value={balCode}
            onChange={e => setBalCode(e.target.value)}
            disabled={saving || busy}
            className="recon-gl-modal-select-wide"
          >
            <option value="">—</option>
            {coaList.map(c => (
              <option key={c.code} value={c.code}>
                {coaOptionLabel(c)}
              </option>
            ))}
          </select>
        </label>

        <div className={`recon-gl-modal-balance ${totals.balanced ? 'is-balanced' : ''}`}>
          <div className="recon-gl-modal-balance-left">
            <div className="recon-gl-modal-base-cur">
              Base ({cur}){' '}
              <span className="recon-gl-modal-info-icon" title="Posting currency for this voucher.">
                i
              </span>
            </div>
            <div className="recon-gl-modal-balance-status">
              {!totals.hasMovement && lineStates.length >= 2
                ? 'Enter amounts'
                : totals.balanced
                  ? 'Balanced'
                  : `Not balanced (Δ ${fmt(totals.totalDebit - totals.totalCredit)} ${cur})`}
            </div>
            {lineStates.length < 2 ? <div className="recon-gl-modal-balance-status">At least 2 lines required</div> : null}
            <span className="recon-gl-modal-totals">
              Dr {cur} {fmt(totals.totalDebit)} · Cr {cur} {fmt(totals.totalCredit)}
            </span>
          </div>
          <div className="recon-gl-modal-balance-right" aria-label="Category roll-up">
            <span>
              Assets · Dr {cur} {fmt(ale.assets.dr)} · Cr {cur} {fmt(ale.assets.cr)}
            </span>
            <span>
              Liabilities · Dr {cur} {fmt(ale.liabilities.dr)} · Cr {cur} {fmt(ale.liabilities.cr)}
            </span>
            <span>
              Equity · Dr {cur} {fmt(ale.equity.dr)} · Cr {cur} {fmt(ale.equity.cr)}
            </span>
            <span className="recon-gl-modal-ale-hint">By CoA category (display only)</span>
          </div>
        </div>

        <div className="recon-gl-modal-footer">
          <button type="button" className="recon-gl-modal-btn-cancel" onClick={onClose} disabled={saving || busy}>
            Cancel
          </button>
          <button
            type="button"
            className="recon-gl-modal-btn-ok primary"
            onClick={() => void handleOk()}
            disabled={saving || busy || !canSubmit}
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
