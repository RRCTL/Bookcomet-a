import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { ReconGlJournalEditModal } from '../../components/ReconGlJournalEditModal'
import { useAuth } from '../../contexts/AuthContext'
import {
  reconciliationApi,
  type GlJournalListItem,
  type GlJournalPayload,
} from '../../services/reconciliation'
import type { ChartOfAccountItem } from '../../types/reconciliation'

type DisplayMode = 'preview' | 'manage'

const MONTHS = ['Jan.', 'Feb.', 'Mar.', 'Apr.', 'May', 'Jun.', 'Jul.', 'Aug.', 'Sep.', 'Oct.', 'Nov.', 'Dec.']

function fmtMoney(n: number): string {
  return n.toLocaleString('en-HK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtBookDate(iso: string | null | undefined): { year: string; day: string } {
  if (!iso) return { year: '', day: '' }
  const d = new Date(iso.slice(0, 10) + 'T12:00:00')
  if (Number.isNaN(d.getTime())) return { year: '', day: iso.slice(0, 10) }
  return {
    year: String(d.getFullYear()),
    day: `${MONTHS[d.getMonth()]} ${d.getDate()}`,
  }
}

function accountLabel(code: string, coaList: ChartOfAccountItem[]): string {
  const c = (code || '').trim()
  if (!c) return '—'
  const a = coaList.find(x => x.code === c)
  return (a?.name_en || a?.name_zh || c).trim() || c
}

function postRef(status: string, code: string): string {
  if ((status || '').toLowerCase() !== 'posted') return '—'
  return (code || '').trim() || '—'
}

function canPost(j: GlJournalListItem): boolean {
  if ((j.status || '').toLowerCase() !== 'draft') return false
  if (!j.balanced) return false
  const src = (j.source || '').toLowerCase()
  if (src === 'manual') return true
  if (j.reconciliation_group_id) return true
  return false
}

/** Journal Manage may edit only draft + unreconciled (not RECON group / matched). */
function canEditJournal(j: GlJournalListItem): boolean {
  if ((j.status || '').toLowerCase() !== 'draft') return false
  if ((j.reconciliation_group_id || '').trim()) return false
  const recon = (j.recon_status || 'unreconciled').toLowerCase()
  return recon === 'unreconciled'
}

function editBlockedReason(j: GlJournalListItem): string | null {
  if ((j.status || '').toLowerCase() === 'posted') {
    return 'Unpost the journal before editing.'
  }
  if ((j.reconciliation_group_id || '').trim() || (j.recon_status || '').toLowerCase() !== 'unreconciled') {
    return 'Cancel the RECON match before editing (reconciled transactions cannot be edited).'
  }
  return null
}

function csvCell(v: string): string {
  return v.includes(',') || v.includes('"') || v.includes('\n') ? `"${v.replace(/"/g, '""')}"` : v
}

function downloadCsv(filename: string, content: string) {
  const blob = new Blob(['\uFEFF' + content], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
  URL.revokeObjectURL(a.href)
}

function journalsToCsv(journals: GlJournalListItem[], coaList: ChartOfAccountItem[]): string {
  const headers = [
    'voucher_no',
    'journal_date',
    'currency',
    'gl_status',
    'recon_status',
    'module',
    'source',
    'narration',
    'line_no',
    'account_code',
    'account_name',
    'debit',
    'credit',
    'memo',
    'bank_txn_id',
    'ledger_txn_id',
  ]
  const rows: string[] = [headers.join(',')]
  const sorted = [...journals].sort((a, b) => {
    const da = (a.journal_date || '').slice(0, 10)
    const db = (b.journal_date || '').slice(0, 10)
    if (da !== db) return da.localeCompare(db)
    return (a.voucher_no || '').localeCompare(b.voucher_no || '')
  })
  for (const j of sorted) {
    const lines = j.lines?.length
      ? j.lines
      : [
          {
            id: '',
            line_no: 1,
            account_code: '',
            debit: 0,
            credit: 0,
            memo: null,
            bank_txn_id: null,
            ledger_txn_id: null,
          },
        ]
    for (const ln of lines) {
      rows.push(
        [
          csvCell(j.voucher_no || ''),
          csvCell((j.journal_date || '').slice(0, 10)),
          csvCell(j.currency || ''),
          csvCell(j.status || ''),
          csvCell(j.recon_status || ''),
          csvCell(j.module || ''),
          csvCell(j.source || ''),
          csvCell(j.narration || ''),
          csvCell(String(ln.line_no ?? '')),
          csvCell(ln.account_code || ''),
          csvCell(accountLabel(ln.account_code || '', coaList)),
          csvCell(String(ln.debit ?? 0)),
          csvCell(String(ln.credit ?? 0)),
          csvCell(ln.memo || ''),
          csvCell(ln.bank_txn_id || ''),
          csvCell(ln.ledger_txn_id || ''),
        ].join(','),
      )
    }
  }
  return rows.join('\n')
}

function badgeClass(kind: 'gl' | 'recon', value: string): string {
  const v = value.toLowerCase()
  if (kind === 'gl') {
    if (v === 'posted') return 'erp-j-badge posted'
    if (v === 'voided') return 'erp-j-badge voided'
    return 'erp-j-badge draft'
  }
  if (v === 'matched') return 'erp-j-badge matched'
  if (v === 'partial') return 'erp-j-badge partial'
  return 'erp-j-badge open'
}

type ManualLine = { account_code: string; debit: string; credit: string; memo: string }

const emptyManualLine = (): ManualLine => ({
  account_code: '',
  debit: '',
  credit: '',
  memo: '',
})

function JournalBookView({
  journals,
  coaList,
  companyName,
}: {
  journals: GlJournalListItem[]
  coaList: ChartOfAccountItem[]
  companyName: string
}) {
  const sorted = useMemo(() => {
    return [...journals].sort((a, b) => {
      const da = (a.journal_date || '').slice(0, 10)
      const db = (b.journal_date || '').slice(0, 10)
      if (da !== db) return da.localeCompare(db)
      return (a.voucher_no || '').localeCompare(b.voucher_no || '')
    })
  }, [journals])

  const showYearById = useMemo(() => {
    const map = new Map<string, boolean>()
    let lastYear = ''
    for (const j of sorted) {
      const { year } = fmtBookDate(j.journal_date)
      const show = !!year && year !== lastYear
      map.set(j.id, show)
      if (year) lastYear = year
    }
    return map
  }, [sorted])

  return (
    <div className="erp-journal-book">
      <div className="erp-journal-book-meta">
        <div>
          <div className="erp-journal-book-title">General Journal</div>
          <div className="erp-journal-book-sub">{companyName || 'Company'}</div>
        </div>
        <div className="erp-journal-book-sub">Page 1</div>
      </div>
      <table className="erp-journal-book-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Particulars</th>
            <th>Post Ref</th>
            <th>Debit</th>
            <th>Credit</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((j, ji) => {
            const gl = (j.status || 'draft').toLowerCase()
            const recon = (j.recon_status || 'unreconciled').toLowerCase()
            const { year, day } = fmtBookDate(j.journal_date)
            const showYear = showYearById.get(j.id) === true

            const debitLines = (j.lines || []).filter(ln => (Number(ln.debit) || 0) > 0.0005)
            const creditLines = (j.lines || []).filter(ln => (Number(ln.credit) || 0) > 0.0005)
            const narration = (j.narration || '').trim() || `To record ${j.voucher_no}`
            const lineRows: Array<{ key: string; kind: 'debit' | 'credit'; ln: (typeof debitLines)[0] }> = [
              ...debitLines.map(ln => ({ key: `${j.id}-d-${ln.id}`, kind: 'debit' as const, ln })),
              ...creditLines.map(ln => ({ key: `${j.id}-c-${ln.id}`, kind: 'credit' as const, ln })),
            ]

            return (
              <Fragment key={j.id}>
                <tr className="entry-meta">
                  <td colSpan={5}>
                    <code>{j.voucher_no}</code>{' '}
                    <span className={badgeClass('gl', gl)}>{gl}</span>
                    <span className={badgeClass('recon', recon)}>{recon}</span>
                    {(j.module || '').trim() ? (
                      <span className="erp-j-badge">{(j.module || '').toLowerCase()}</span>
                    ) : null}{' '}
                    <span className="erp-journal-book-ccy">{j.currency}</span>
                  </td>
                </tr>
                {lineRows.map((row, ri) => (
                  <tr key={row.key}>
                    <td className="dt">
                      {ri === 0 ? (
                        <>
                          {showYear ? <span className="yr">{year}</span> : null}
                          {day}
                        </>
                      ) : null}
                    </td>
                    <td className={row.kind === 'credit' ? 'credit-name' : undefined}>
                      {accountLabel(row.ln.account_code, coaList)}
                    </td>
                    <td className="pr">{postRef(gl, row.ln.account_code)}</td>
                    <td className="am">
                      {row.kind === 'debit' ? fmtMoney(Number(row.ln.debit) || 0) : ''}
                    </td>
                    <td className="am">
                      {row.kind === 'credit' ? fmtMoney(Number(row.ln.credit) || 0) : ''}
                    </td>
                  </tr>
                ))}
                <tr className="narr">
                  <td />
                  <td className="narr-text" colSpan={4}>
                    {narration}
                  </td>
                </tr>
                {ji < sorted.length - 1 ? (
                  <tr className="spacer">
                    <td colSpan={5} />
                  </tr>
                ) : null}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export function JournalPage() {
  const { activeCompany } = useAuth()
  const [mode, setMode] = useState<DisplayMode>('preview')
  const [journals, setJournals] = useState<GlJournalListItem[]>([])
  const [coaList, setCoaList] = useState<ChartOfAccountItem[]>([])
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState('')
  const [currencyFilter, setCurrencyFilter] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [editJournal, setEditJournal] = useState<GlJournalPayload | null>(null)
  const [showManual, setShowManual] = useState(false)
  const [manualDate, setManualDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [manualCurrency, setManualCurrency] = useState('HKD')
  const [manualNarration, setManualNarration] = useState('')
  const [manualLines, setManualLines] = useState<ManualLine[]>([emptyManualLine(), emptyManualLine()])
  const [manualErr, setManualErr] = useState<string | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [listRes, coaRes] = await Promise.all([
        reconciliationApi.glList({
          status: statusFilter || undefined,
          currency: currencyFilter || undefined,
          date_from: dateFrom || undefined,
          date_to: dateTo || undefined,
          limit: 500,
        }),
        reconciliationApi.getChartOfAccounts(),
      ])
      setJournals(listRes.journals || [])
      setCoaList(coaRes.accounts || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load journals')
    } finally {
      setLoading(false)
    }
  }, [statusFilter, currencyFilter, dateFrom, dateTo])

  useEffect(() => {
    void reload()
  }, [reload])

  const coaOptionLabel = useCallback((a: ChartOfAccountItem) => {
    return `${a.code} — ${a.name_en || a.name_zh || ''}`
  }, [])

  const currencies = useMemo(() => {
    const set = new Set<string>()
    for (const j of journals) {
      const c = (j.currency || '').trim().toUpperCase()
      if (c) set.add(c)
    }
    return [...set].sort()
  }, [journals])

  const companyName = activeCompany?.name || 'Company'

  const onPost = async (j: GlJournalListItem) => {
    setBusyId(j.id)
    setError(null)
    try {
      await reconciliationApi.glPostJournal(j.id)
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Post failed')
    } finally {
      setBusyId(null)
    }
  }

  const onUnpost = async (j: GlJournalListItem) => {
    setBusyId(j.id)
    setError(null)
    try {
      await reconciliationApi.glUnpostToDraft(j.id)
      await reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unpost failed')
    } finally {
      setBusyId(null)
    }
  }

  const submitManual = async () => {
    setManualErr(null)
    const lines = manualLines
      .map(ln => ({
        account_code: ln.account_code.trim(),
        debit: Number(ln.debit) || 0,
        credit: Number(ln.credit) || 0,
        memo: ln.memo.trim() || null,
      }))
      .filter(ln => ln.account_code && (ln.debit > 0 || ln.credit > 0))
    try {
      await reconciliationApi.glCreateManual({
        journal_date: manualDate,
        currency: manualCurrency.trim().toUpperCase() || 'HKD',
        narration: manualNarration.trim() || null,
        lines,
      })
      setShowManual(false)
      setManualLines([emptyManualLine(), emptyManualLine()])
      setManualNarration('')
      await reload()
    } catch (err) {
      setManualErr(err instanceof Error ? err.message : 'Create failed')
    }
  }

  return (
    <div className="erp-journal">
      <div className="erp-journal-toolbar">
        <div className="erp-journal-seg" role="group" aria-label="Display mode">
          <button
            type="button"
            className={`erp-btn${mode === 'preview' ? ' on' : ''}`}
            onClick={() => setMode('preview')}
          >
            Preview
          </button>
          <button
            type="button"
            className={`erp-btn${mode === 'manage' ? ' on' : ''}`}
            onClick={() => setMode('manage')}
          >
            Manage
          </button>
        </div>
        <label>
          Status
          <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
            <option value="">All</option>
            <option value="draft">Draft</option>
            <option value="posted">Posted</option>
          </select>
        </label>
        <label>
          Currency
          <select value={currencyFilter} onChange={e => setCurrencyFilter(e.target.value)}>
            <option value="">All</option>
            {currencies.map(c => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
            {!currencies.includes('HKD') && <option value="HKD">HKD</option>}
            {!currencies.includes('USD') && <option value="USD">USD</option>}
          </select>
        </label>
        <label>
          From
          <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
        </label>
        <label>
          To
          <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} />
        </label>
        <button type="button" className="erp-btn" onClick={() => void reload()} disabled={loading}>
          Refresh
        </button>
        <button
          type="button"
          className="erp-btn"
          disabled={loading || journals.length === 0}
          onClick={() => {
            const stamp = new Date().toISOString().slice(0, 10)
            downloadCsv(`journals_${stamp}.csv`, journalsToCsv(journals, coaList))
          }}
        >
          Export CSV
        </button>
        <button type="button" className="erp-btn primary" onClick={() => setShowManual(true)}>
          New manual journal
        </button>
      </div>

      {error && <div className="erp-journal-err">{error}</div>}
      {loading && <div className="erp-note">Loading journals…</div>}

      {!loading && journals.length === 0 && (
        <div className="erp-note">No journals yet. Approve AP/AR/BANK rows or create a manual journal.</div>
      )}

      {!loading && journals.length > 0 && mode === 'preview' && (
        <JournalBookView
          journals={journals}
          coaList={coaList}
          companyName={companyName}
        />
      )}

      {!loading && journals.length > 0 && mode === 'manage' && (
        <div className="erp-journal-table-wrap">
          <table className="erp-journal-table">
            <thead>
              <tr>
                <th>Voucher</th>
                <th>Date</th>
                <th>CCY</th>
                <th>Module</th>
                <th>Recon</th>
                <th>GL</th>
                <th>Debit</th>
                <th>Credit</th>
                <th>Narration</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {journals.map(j => {
                const gl = (j.status || 'draft').toLowerCase()
                const recon = (j.recon_status || 'unreconciled').toLowerCase()
                const draft = gl === 'draft'
                return (
                  <tr key={j.id}>
                    <td>
                      <code>{j.voucher_no}</code>
                    </td>
                    <td>{(j.journal_date || '').slice(0, 10)}</td>
                    <td>{j.currency}</td>
                    <td>{j.module || '—'}</td>
                    <td>
                      <span className={badgeClass('recon', recon)}>{recon}</span>
                    </td>
                    <td>
                      <span className={badgeClass('gl', gl)}>{gl}</span>
                    </td>
                    <td className="num">{fmtMoney(j.total_debit)}</td>
                    <td className="num">{fmtMoney(j.total_credit)}</td>
                    <td className="narr">{j.narration || '—'}</td>
                    <td className="actions">
                      {draft && canEditJournal(j) && (
                        <button
                          type="button"
                          className="erp-btn sm"
                          disabled={busyId === j.id}
                          onClick={() => setEditJournal(j)}
                        >
                          Edit
                        </button>
                      )}
                      {draft && !canEditJournal(j) && (
                        <button
                          type="button"
                          className="erp-btn sm"
                          disabled
                          title={editBlockedReason(j) || 'Cannot edit'}
                        >
                          Edit
                        </button>
                      )}
                      {canPost(j) && (
                        <button
                          type="button"
                          className="erp-btn sm primary"
                          disabled={busyId === j.id}
                          onClick={() => void onPost(j)}
                        >
                          Post
                        </button>
                      )}
                      {gl === 'posted' && (
                        <button
                          type="button"
                          className="erp-btn sm"
                          disabled={busyId === j.id}
                          onClick={() => void onUnpost(j)}
                        >
                          Unpost
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <ReconGlJournalEditModal
        open={!!editJournal}
        groupId={editJournal?.reconciliation_group_id || editJournal?.id || ''}
        journal={editJournal}
        coaList={coaList}
        coaOptionLabel={coaOptionLabel}
        busy={busyId === editJournal?.id}
        onClose={() => setEditJournal(null)}
        onSaved={async (_next: GlJournalPayload) => {
          setEditJournal(null)
          await reload()
        }}
      />

      {showManual && (
        <div className="erp-journal-modal-backdrop" role="presentation" onClick={() => setShowManual(false)}>
          <div
            className="erp-journal-modal"
            role="dialog"
            aria-label="New manual journal"
            onClick={e => e.stopPropagation()}
          >
            <h3>New manual journal</h3>
            <div className="erp-journal-manual-fields">
              <label>
                Date
                <input type="date" value={manualDate} onChange={e => setManualDate(e.target.value)} />
              </label>
              <label>
                Currency
                <input
                  value={manualCurrency}
                  onChange={e => setManualCurrency(e.target.value.toUpperCase())}
                  maxLength={8}
                />
              </label>
              <label className="wide">
                Narration
                <input value={manualNarration} onChange={e => setManualNarration(e.target.value)} />
              </label>
            </div>
            <table className="erp-journal-table compact">
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Debit</th>
                  <th>Credit</th>
                  <th>Memo</th>
                </tr>
              </thead>
              <tbody>
                {manualLines.map((ln, i) => (
                  <tr key={i}>
                    <td>
                      <select
                        value={ln.account_code}
                        onChange={e => {
                          const next = [...manualLines]
                          next[i] = { ...ln, account_code: e.target.value }
                          setManualLines(next)
                        }}
                      >
                        <option value="">Select…</option>
                        {coaList.map(a => (
                          <option key={a.code} value={a.code}>
                            {a.code} — {a.name_en || a.name_zh}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        value={ln.debit}
                        onChange={e => {
                          const next = [...manualLines]
                          next[i] = { ...ln, debit: e.target.value, credit: e.target.value ? '' : ln.credit }
                          setManualLines(next)
                        }}
                      />
                    </td>
                    <td>
                      <input
                        value={ln.credit}
                        onChange={e => {
                          const next = [...manualLines]
                          next[i] = { ...ln, credit: e.target.value, debit: e.target.value ? '' : ln.debit }
                          setManualLines(next)
                        }}
                      />
                    </td>
                    <td>
                      <input
                        value={ln.memo}
                        onChange={e => {
                          const next = [...manualLines]
                          next[i] = { ...ln, memo: e.target.value }
                          setManualLines(next)
                        }}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="erp-journal-manual-actions">
              <button
                type="button"
                className="erp-btn"
                onClick={() => setManualLines(prev => [...prev, emptyManualLine()])}
              >
                Add line
              </button>
              <button type="button" className="erp-btn" onClick={() => setShowManual(false)}>
                Cancel
              </button>
              <button type="button" className="erp-btn primary" onClick={() => void submitManual()}>
                Create draft
              </button>
            </div>
            {manualErr && <div className="erp-journal-err">{manualErr}</div>}
          </div>
        </div>
      )}
    </div>
  )
}
