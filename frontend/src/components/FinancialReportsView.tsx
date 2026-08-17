import { useState, type ReactNode } from 'react'
import type {
  FinancialReportData,
  TrialBalanceRow,
  IncomeRow,
  BalanceRow,
  TxnRecord,
} from '../hooks/useReportData'
import './FinancialReportsView.css'

type Props = {
  data: FinancialReportData
}

type Tab = 'trial' | 'income' | 'balance'

// ─── Number formatters ────────────────────────────────────────────────────────
const fmt = (n: number) =>
  n.toLocaleString('en-HK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

const fmtSigned = (n: number) => {
  if (n === 0) return '—'
  return (n < 0 ? '-' : '') + fmt(Math.abs(n))
}

/** BS / subtotals: parentheses for negatives; zero as 0.00 */
const fmtSignedAmount = (n: number) => {
  if (Math.abs(n) < 0.005) return fmt(0)
  return n < 0 ? `(${fmt(Math.abs(n))})` : fmt(n)
}

// ─── Accountant single-column layout (print / PDF friendly) ─────────────────
function AcctTable({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <table className={`frv-acct-table ${className}`.trim()}>
      <tbody>{children}</tbody>
    </table>
  )
}

function AcctLineRow({
  code,
  nameEn,
  nameZh: _nameZh,
  amount,
  noteRef,
  suspense,
  signed,
  children,
}: {
  code?: string
  nameEn: string
  nameZh?: string
  amount: number
  noteRef?: string
  suspense?: boolean
  signed?: boolean
  children?: ReactNode
}) {
  const amt = signed ? fmtSigned(amount) : fmtSignedAmount(amount)
  return (
    <tr className={suspense ? 'frv-acct-tr-suspense' : undefined}>
      <td className="frv-acct-td-label">
        {code ? <span className="frv-acct-code">{code}</span> : null}
        <span className="frv-acct-name-en">
          {nameEn}
          {noteRef ? <span className="frv-acct-note-ref"> (Note {noteRef})</span> : null}
        </span>
        {children}
      </td>
      <td className="frv-acct-td-amt">{amt}</td>
    </tr>
  )
}

function AcctSubtotalRow({ labelEn, labelZh: _labelZh, amount }: { labelEn: string; labelZh?: string; amount: number }) {
  return (
    <tr className="frv-acct-tr-subtotal">
      <td className="frv-acct-td-label">
        <strong className="frv-acct-name-en">{labelEn}</strong>
      </td>
      <td className="frv-acct-td-amt">{fmtSignedAmount(amount)}</td>
    </tr>
  )
}

function AcctDoubleRuleRow({ labelEn, labelZh: _labelZh, amount }: { labelEn: string; labelZh?: string; amount: number }) {
  return (
    <tr className="frv-acct-tr-double">
      <td className="frv-acct-td-label">
        <strong className="frv-acct-name-en">{labelEn}</strong>
      </td>
      <td className="frv-acct-td-amt">
        <strong>{fmtSignedAmount(amount)}</strong>
      </td>
    </tr>
  )
}

function AcctNilRow({ labelEn, labelZh: _labelZh }: { labelEn: string; labelZh?: string }) {
  return (
    <tr className="frv-acct-tr-nil">
      <td className="frv-acct-td-label">
        <span className="frv-acct-nil">{labelEn}</span>
      </td>
      <td className="frv-acct-td-amt">—</td>
    </tr>
  )
}

// ─── Expandable transaction list ──────────────────────────────────────────────
function TxnExpand({
  txns,
  unmatchedCount,
  isSuspense,
}: {
  txns: TxnRecord[]
  unmatchedCount: number
  isSuspense?: boolean
}) {
  const [open, setOpen] = useState(false)
  if (txns.length === 0) return null

  const toggleLabel = isSuspense
    ? `${txns.length} suspense transaction${txns.length === 1 ? '' : 's'}`
    : `${txns.length} line${txns.length === 1 ? '' : 's'}${unmatchedCount > 0 ? ` (${unmatchedCount} uncoded)` : ''}`

  const getTxnReasonBadge = (t: TxnRecord) => {
    if (t.isNoCode && t.isReconUnmatched) return <span className="frv-txn-badge frv-txn-badge-both">Uncoded + unmatched</span>
    if (t.isReconUnmatched) return <span className="frv-txn-badge frv-txn-badge-recon">RECON unmatched</span>
    if (t.isNoCode) return <span className="frv-txn-badge">Uncoded</span>
    return null
  }

  return (
    <div className="frv-txn-expand">
      <button
        className={`frv-txn-toggle ${unmatchedCount > 0 || isSuspense ? 'has-unmatched' : ''}`}
        onClick={() => setOpen(v => !v)}
      >
        {open ? '▴' : '▾'} {toggleLabel}
      </button>
      {open && (
        <div className="frv-txn-list">
          {txns.map((t, i) => (
            <div key={`${t.id}-${i}`} className={`frv-txn-row ${t.isUnmatched ? 'unmatched' : ''}`}>
              <span className="frv-txn-date">{t.date || '—'}</span>
              <span className="frv-txn-desc">{t.description || t.id}</span>
              <span className="frv-txn-amt">{fmt(t.amount)}</span>
              {t.gl?.status === 'draft' && (
                <span className="frv-txn-badge frv-txn-badge-draft">DRAFT</span>
              )}
              {t.gl?.status === 'posted' && (
                <span className="frv-txn-badge frv-txn-badge-posted">POSTED</span>
              )}
              {getTxnReasonBadge(t)}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Suspense warning strip ───────────────────────────────────────────────────
function SuspenseWarning({ summary }: { summary: FinancialReportData['summary'] }) {
  const { suspenseNoCodeCount, suspenseReconUnmatchedCount, suspenseBothCount } = summary
  const total = suspenseNoCodeCount + suspenseReconUnmatchedCount + suspenseBothCount
  if (total === 0) return null

  return (
    <div className="frv-suspense-warning">
      <div className="frv-suspense-warning-title">
        Suspense holds uncoded or unmatched items — assign codes and re-reconcile, then regenerate the report.
      </div>
      <ul className="frv-suspense-warning-list">
        {suspenseNoCodeCount > 0 && (
          <li>Missing account code: {suspenseNoCodeCount}</li>
        )}
        {suspenseReconUnmatchedCount > 0 && (
          <li>RECON unmatched (code set): {suspenseReconUnmatchedCount}</li>
        )}
        {suspenseBothCount > 0 && (
          <li>Both uncoded and unmatched: {suspenseBothCount}</li>
        )}
      </ul>
    </div>
  )
}

// ─── Trial Balance tab ────────────────────────────────────────────────────────
function TrialBalanceTab({ rows, summary }: { rows: TrialBalanceRow[]; summary: FinancialReportData['summary'] }) {
  if (rows.length === 0) {
    return (
      <div className="frv-empty">
        <p className="frv-empty-lead">No transactions in this period.</p>
        <p className="frv-empty-sub">Confirm the date range and that the chart of accounts is deployed.</p>
      </div>
    )
  }
  return (
    <div className="frv-table-wrap">
      <table className="frv-table">
        <thead>
          <tr>
            <th>Code</th>
            <th>Account</th>
            <th>Type</th>
            <th className="num-col">Open Dr</th>
            <th className="num-col">Open Cr</th>
            <th className="num-col">Mov Dr</th>
            <th className="num-col">Mov Cr</th>
            <th className="num-col">Close Dr</th>
            <th className="num-col">Close Cr</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.code} className={row.isSuspense ? 'frv-suspense-row' : ''}>
              <td className="code-col">
                {row.code}
                {row.isSuspense && <span className="frv-suspense-badge">Suspense</span>}
              </td>
              <td className="name-col">
                <div>{row.name_en || row.name_zh}</div>
              </td>
              <td>
                <span className={`frv-cat-badge cat-${row.category_type.toLowerCase()}`}>
                  {row.category_type}
                </span>
              </td>
              <td className="num-col">{row.openDr > 0 ? fmt(row.openDr) : '—'}</td>
              <td className="num-col">{row.openCr > 0 ? fmt(row.openCr) : '—'}</td>
              <td className="num-col">{row.movDr > 0 ? fmt(row.movDr) : '—'}</td>
              <td className="num-col">{row.movCr > 0 ? fmt(row.movCr) : '—'}</td>
              <td className="num-col">{row.closeDr > 0 ? fmt(row.closeDr) : '—'}</td>
              <td className="num-col">{row.closeCr > 0 ? fmt(row.closeCr) : '—'}</td>
              <td>
                <TxnExpand
                  txns={row.transactions}
                  unmatchedCount={row.unmatchedCount}
                  isSuspense={row.isSuspense}
                />
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="frv-total-row">
            <td colSpan={5}>Totals</td>
            <td className="num-col total">{fmt(summary.totalMovDr)}</td>
            <td className="num-col total">{fmt(summary.totalMovCr)}</td>
            <td colSpan={3}>
              {summary.balanced
                ? <span className="frv-balanced">Balanced</span>
                : <span className="frv-unbalanced">Out of balance — diff {fmt(Math.abs(summary.totalMovDr - summary.totalMovCr))}</span>
              }
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  )
}

// ─── Income Statement tab (accountant single column) ──────────────────────────
function IncomeStatementTab({
  rows,
  summary,
  periodLabel,
}: {
  rows: IncomeRow[]
  summary: FinancialReportData['summary']
  periodLabel: string
}) {
  const incomeRows = rows.filter(r => r.category_type === 'Income')
  const expenseRows = rows.filter(r => r.category_type === 'Expense')

  if (rows.length === 0) {
    return (
      <div className="frv-empty">
        <p className="frv-empty-lead">
          No income or expense accounts were used in this period.
        </p>
        <p className="frv-empty-sub">
          Assign income or expense account codes from the chart of accounts.
        </p>
      </div>
    )
  }

  return (
    <div className="frv-acct-doc frv-acct-pl">
      <header className="frv-acct-doc-head">
        <h2 className="frv-acct-doc-title">Statement of profit or loss</h2>
        <p className="frv-acct-doc-meta">
          For the period: <strong>{periodLabel}</strong>
        </p>
      </header>

      <details className="frv-acct-disclosure frv-no-print">
        <summary>P&amp;L scope</summary>
        <p>
          Period activity on income &amp; expense accounts only; Dr = Cr on the trial balance.
        </p>
      </details>

      <section className="frv-acct-section">
        <h3 className="frv-acct-h3">Income</h3>
        <AcctTable>
          {incomeRows.length === 0 ? (
            <AcctNilRow labelEn="No income lines" labelZh="無收入科目" />
          ) : (
            incomeRows.map(row => (
              <AcctLineRow
                key={row.code}
                code={row.code}
                nameEn={row.name_en}
                nameZh={row.name_zh}
                amount={row.amount}
              >
                <TxnExpand txns={row.transactions} unmatchedCount={row.unmatchedCount} />
              </AcctLineRow>
            ))
          )}
          <AcctSubtotalRow labelEn="Total income" labelZh="收入合計" amount={summary.totalIncome} />
        </AcctTable>
      </section>

      <section className="frv-acct-section">
        <h3 className="frv-acct-h3">Expenses</h3>
        <AcctTable>
          {expenseRows.length === 0 ? (
            <AcctNilRow labelEn="No expense lines" labelZh="無費用科目" />
          ) : (
            expenseRows.map(row => (
              <AcctLineRow
                key={row.code}
                code={row.code}
                nameEn={row.name_en}
                nameZh={row.name_zh}
                amount={row.amount}
              >
                <TxnExpand txns={row.transactions} unmatchedCount={row.unmatchedCount} />
              </AcctLineRow>
            ))
          )}
          <AcctSubtotalRow labelEn="Total expenses" labelZh="費用合計" amount={summary.totalExpense} />
        </AcctTable>
      </section>

      <AcctTable className="frv-acct-pl-bottom">
        <tr className={`frv-acct-tr-pl-result ${summary.netIncome >= 0 ? 'profit' : 'loss'}`}>
          <td className="frv-acct-td-label">
            <strong className="frv-acct-name-en">
              {summary.netIncome >= 0 ? 'Profit for the period' : 'Loss for the period'}
            </strong>
          </td>
          <td className="frv-acct-td-amt">
            <strong>{fmtSigned(summary.netIncome)}</strong>
          </td>
        </tr>
      </AcctTable>
    </div>
  )
}

// ─── Balance Sheet tab (accountant single column) ─────────────────────────────
function BalanceSheetTab({
  rows,
  summary,
  periodLabel,
  asAtDate,
}: {
  rows: BalanceRow[]
  summary: FinancialReportData['summary']
  periodLabel: string
  asAtDate: string
}) {
  const suspenseRows = rows.filter(r => r.isSuspense)
  const assetRows = rows.filter(r => r.category_type === 'Asset' && !r.isSuspense)
  const liabilityRows = rows.filter(r => r.category_type === 'Liability')
  const equityRows = rows.filter(r => r.category_type === 'Equity')

  const totalAssetsFull = summary.totalAsset

  const totalPresentationRight =
    summary.totalLiability + summary.totalEquity + summary.netIncome
  const balanceSheetOk = summary.balanceSheetBalanced
  const netAssets = totalAssetsFull - summary.totalLiability
  const totalEquitySection = summary.totalEquity + summary.netIncome

  if (rows.length === 0) {
    return (
      <div className="frv-empty">
        <p className="frv-empty-lead">No balance sheet lines in this period.</p>
        <p className="frv-empty-sub">Confirm the date range and CoA classifications for assets, liabilities, and equity.</p>
      </div>
    )
  }

  return (
    <div className="frv-acct-doc frv-acct-bs">
      <header className="frv-acct-doc-head">
        <h2 className="frv-acct-doc-title">Statement of financial position</h2>
        <p className="frv-acct-doc-meta">
          As at: <strong>{asAtDate}</strong>
        </p>
        <p className="frv-acct-doc-meta frv-acct-doc-period">
          Period: <strong>{periodLabel}</strong>
        </p>
      </header>

      <details className="frv-acct-disclosure frv-no-print">
        <summary>Balance sheet tie</summary>
        <p>
          Line amounts are <strong>signed nets</strong> from the same closing balances as the trial balance: assets =
          Dr−Cr (credit balance shows in parentheses); liabilities &amp; equity = Cr−Dr (debit balance in
          parentheses). Totals: Assets = Liabilities + Equity (CoA) + current P&amp;L when the trial balance ties.
        </p>
      </details>

      <div className={`frv-bs-at-a-glance ${balanceSheetOk ? 'balanced' : 'unbalanced'}`}>
        <div className="frv-bs-glance-title">Summary</div>
        <table className="frv-bs-glance-table">
          <tbody>
            <tr>
              <td>Total assets</td>
              <td className="frv-bs-glance-amt">{fmtSignedAmount(totalAssetsFull)}</td>
            </tr>
            <tr className="frv-bs-glance-split">
              <td colSpan={2}>Right side</td>
            </tr>
            <tr>
              <td>Liabilities</td>
              <td className="frv-bs-glance-amt">{fmtSignedAmount(summary.totalLiability)}</td>
            </tr>
            <tr>
              <td>Equity (CoA)</td>
              <td className="frv-bs-glance-amt">{fmtSignedAmount(summary.totalEquity)}</td>
            </tr>
            <tr>
              <td>P&amp;L</td>
              <td className="frv-bs-glance-amt">{fmtSigned(summary.netIncome)}</td>
            </tr>
            <tr className="frv-bs-glance-total">
              <td><strong>Total</strong></td>
              <td className="frv-bs-glance-amt"><strong>{fmtSignedAmount(totalPresentationRight)}</strong></td>
            </tr>
          </tbody>
        </table>
        <div className="frv-bs-glance-row2">
          <span className={`frv-bs-glance-status ${balanceSheetOk ? 'ok' : 'bad'}`}>
            {balanceSheetOk ? 'Balanced' : `Diff ${fmt(Math.abs(totalAssetsFull - totalPresentationRight))}`}
          </span>
          <button
            type="button"
            className="frv-bs-jump-equity frv-no-print"
            onClick={() =>
              document.getElementById('frv-bs-equity-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
            }
          >
            To equity
          </button>
        </div>
      </div>

      <section className="frv-acct-section">
        <h3 className="frv-acct-h3">Assets</h3>
        <AcctTable>
          {assetRows.map(row => (
            <AcctLineRow
              key={row.code}
              code={row.code}
              nameEn={row.name_en}
              nameZh={row.name_zh}
              amount={row.amount}
            >
              <TxnExpand txns={row.transactions} unmatchedCount={row.unmatchedCount} />
            </AcctLineRow>
          ))}
          {suspenseRows.map(row => (
            <AcctLineRow
              key={row.code}
              code={row.code}
              nameEn={row.name_en}
              nameZh={row.name_zh}
              amount={row.amount}
              noteRef="1"
              suspense
            >
              <TxnExpand txns={row.transactions} unmatchedCount={row.unmatchedCount} isSuspense />
            </AcctLineRow>
          ))}
          <AcctDoubleRuleRow
            labelEn="Total assets"
            labelZh="資產總計（含暫記）"
            amount={summary.totalAsset}
          />
        </AcctTable>
      </section>

      <section className="frv-acct-section">
        <h3 className="frv-acct-h3">Liabilities</h3>
        <AcctTable>
          {liabilityRows.length === 0 ? (
            <AcctNilRow labelEn="— Nil —" labelZh="— 無 —" />
          ) : (
            liabilityRows.map(row => (
              <AcctLineRow
                key={row.code}
                code={row.code}
                nameEn={row.name_en}
                nameZh={row.name_zh}
                amount={row.amount}
              >
                <TxnExpand txns={row.transactions} unmatchedCount={row.unmatchedCount} />
              </AcctLineRow>
            ))
          )}
          <AcctSubtotalRow
            labelEn="Total liabilities"
            labelZh="負債合計"
            amount={summary.totalLiability}
          />
        </AcctTable>
      </section>

      <AcctTable className="frv-acct-bs-net-assets">
        <tr className="frv-acct-tr-net-assets">
          <td className="frv-acct-td-label">
            <strong className="frv-acct-name-en">Net assets</strong>
          </td>
          <td className="frv-acct-td-amt">
            <strong>{fmtSignedAmount(netAssets)}</strong>
          </td>
        </tr>
      </AcctTable>

      <section id="frv-bs-equity-section" className="frv-acct-section frv-bs-equity-anchor">
        <h3 className="frv-acct-h3">Equity</h3>
        <AcctTable>
          {equityRows.length === 0 ? (
            <AcctNilRow labelEn="No equity lines" labelZh="無權益科目" />
          ) : (
            equityRows.map(row => (
              <AcctLineRow
                key={row.code}
                code={row.code}
                nameEn={row.name_en}
                nameZh={row.name_zh}
                amount={row.amount}
              >
                <TxnExpand txns={row.transactions} unmatchedCount={row.unmatchedCount} />
              </AcctLineRow>
            ))
          )}
          <AcctLineRow
            nameEn="Profit / (loss) for the period"
            nameZh="本期淨利／（淨虧）"
            amount={summary.netIncome}
            signed
          />
          <AcctDoubleRuleRow
            labelEn="Total equity"
            labelZh="權益合計"
            amount={totalEquitySection}
          />
        </AcctTable>
      </section>

      <p className="frv-acct-footnote frv-acct-footnote-compact">
        <strong>Note 1:</strong> Suspense = uncoded / unmatched — clear before finals.
      </p>

      <div className={`frv-bs-check frv-acct-bs-check ${balanceSheetOk ? 'balanced' : 'unbalanced'}`}>
        {balanceSheetOk
          ? 'Balance sheet ties'
          : `Out of balance — diff ${fmt(Math.abs(totalAssetsFull - totalPresentationRight))}`
        }
      </div>
    </div>
  )
}

// ─── CSV export ───────────────────────────────────────────────────────────────
function buildCsv(data: FinancialReportData): string {
  const lines: string[] = []
  const q = (v: string | number) => `"${String(v).replace(/"/g, '""')}"`

  const opFallback = data.balanceRows
    .filter(r => r.category_type === 'Asset' && !r.isSuspense)
    .reduce((s, r) => s + r.amount, 0)
  const suspFallback = data.balanceRows.filter(r => r.isSuspense).reduce((s, r) => s + r.amount, 0)
  const s = data.summary
  const opAssets = typeof s.totalOperatingAssets === 'number' ? s.totalOperatingAssets : opFallback
  const suspBal = typeof s.totalSuspenseBalance === 'number' ? s.totalSuspenseBalance : suspFallback
  const assetsTotal = typeof s.totalAsset === 'number' ? s.totalAsset : opAssets + suspBal

  lines.push('Financial Statements Export')
  lines.push(`Generated,${data.generatedAt}`)
  lines.push(`Period,${data.dateFrom} ~ ${data.dateTo}`)
  lines.push('')

  lines.push('=== TRIAL BALANCE ===')
  lines.push([q('Code'), q('Name EN'), q('Name ZH'), q('Category'), q('Suspense'), q('Open Dr'), q('Open Cr'), q('Mov Dr'), q('Mov Cr'), q('Close Dr'), q('Close Cr')].join(','))
  for (const r of data.trialBalanceRows) {
    lines.push([q(r.code), q(r.name_en), q(r.name_zh), q(r.category_type), r.isSuspense ? 'Y' : '', r.openDr, r.openCr, r.movDr, r.movCr, r.closeDr, r.closeCr].join(','))
  }
  lines.push(['', '', '', 'TOTALS', '', '', '', data.summary.totalMovDr, data.summary.totalMovCr, '', ''].join(','))
  lines.push('')

  const { suspenseNoCodeCount, suspenseReconUnmatchedCount, suspenseBothCount } = data.summary
  if (suspenseNoCodeCount + suspenseReconUnmatchedCount + suspenseBothCount > 0) {
    lines.push('=== SUSPENSE BREAKDOWN ===')
    lines.push(`Missing account code,${suspenseNoCodeCount}`)
    lines.push(`RECON unmatched (code set),${suspenseReconUnmatchedCount}`)
    lines.push(`Both,${suspenseBothCount}`)
    lines.push('')
  }

  lines.push('=== INCOME STATEMENT ===')
  lines.push([q('Code'), q('Name EN'), q('Name ZH'), q('Type'), q('Amount')].join(','))
  for (const r of data.incomeRows) {
    lines.push([q(r.code), q(r.name_en), q(r.name_zh), q(r.category_type), r.amount].join(','))
  }
  lines.push(['', '', '', q('Total Income'), data.summary.totalIncome].join(','))
  lines.push(['', '', '', q('Total Expense'), data.summary.totalExpense].join(','))
  lines.push(['', '', '', q('Net Income'), data.summary.netIncome].join(','))
  lines.push('')

  lines.push('=== BALANCE SHEET ===')
  lines.push([q('Code'), q('Name EN'), q('Name ZH'), q('Type'), q('Suspense'), q('Amount_signed_net')].join(','))
  for (const r of data.balanceRows) {
    lines.push([q(r.code), q(r.name_en), q(r.name_zh), q(r.category_type), r.isSuspense ? 'Y' : '', r.amount].join(','))
  }
  lines.push(['', '', '', q('Total operating assets (excl. suspense)'), '', opAssets].join(','))
  lines.push(['', '', '', q('Suspense (balance sheet)'), '', suspBal].join(','))
  lines.push(['', '', '', q('Total assets (incl. suspense)'), '', assetsTotal].join(','))
  lines.push(['', '', '', q('Total Liabilities'), '', data.summary.totalLiability].join(','))
  lines.push(['', '', '', q('Total Equity'), '', data.summary.totalEquity].join(','))
  lines.push(['', '', '', q('Net income (current period, in equation)'), '', data.summary.netIncome].join(','))
  lines.push(['', '', '', q('Net assets (assets − liabilities, signed)'), '', assetsTotal - data.summary.totalLiability].join(','))
  lines.push(['', '', '', q('Total equity (CoA + NI, signed)'), '', data.summary.totalEquity + data.summary.netIncome].join(','))
  lines.push(['', '', '', q('Balance sheet balanced (Assets = Liab + Equity + NI)'), '', data.summary.balanceSheetBalanced ? 'Y' : 'N'].join(','))

  return lines.join('\n')
}

// ─── Main component ───────────────────────────────────────────────────────────
export function FinancialReportsView({ data }: Props) {
  const [activeTab, setActiveTab] = useState<Tab>('trial')

  const handleExportCsv = () => {
    const csv = buildCsv(data)
    const blob = new Blob(['\uFEFF' + csv, ''], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const from = data.dateFrom || 'all'
    const to = data.dateTo || 'all'
    a.download = `financial-statements-${from}-${to}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handlePrint = () => {
    window.print()
  }

  const tabLabels: Record<Tab, string> = {
    trial: 'Trial balance',
    income: 'P&L',
    balance: 'Balance sheet',
  }

  const dateLabel = data.dateFrom && data.dateTo
    ? `${data.dateFrom} ~ ${data.dateTo}`
    : data.dateFrom || data.dateTo || 'All periods'

  return (
    <div className="frv-root">
      {/* Header */}
      <div className="frv-header">
        <div className="frv-header-left">
          <div className="frv-title">Financial statements</div>
          <div className="frv-subtitle">{dateLabel}</div>
        </div>
        <div className="frv-header-actions">
          <button className="frv-btn frv-btn-csv" onClick={handleExportCsv}>Export CSV</button>
          <button className="frv-btn frv-btn-print" onClick={handlePrint}>Print PDF</button>
        </div>
      </div>

      {/* Tabs */}
      <div className="frv-tabs">
        {(Object.keys(tabLabels) as Tab[]).map(tab => (
          <button
            key={tab}
            className={`frv-tab ${activeTab === tab ? 'active' : ''}`}
            onClick={() => setActiveTab(tab)}
          >
            {tabLabels[tab]}
          </button>
        ))}
      </div>

      {data.glProvenance && (data.glProvenance.source === 'gl' || data.glProvenance.source === 'gl+ocr') && (
        <div className={`frv-gl-strip frv-no-print${data.glProvenance.includesDraftJournals ? ' has-draft' : ''}`}>
          <strong>TB source:</strong>{' '}
          {data.glProvenance.source === 'gl'
            ? 'RECON GL only (HKD)'
            : 'RECON GL + OCR supplement (legacy snapshot)'}
          {data.glProvenance.includesDraftJournals && (
            <span className="frv-gl-draft-flag"> · includes DRAFT vouchers</span>
          )}
          <span className="frv-gl-strip-vouchers">
            {' '}
            ({data.glProvenance.activeVoucherNos.slice(0, 8).join(', ')}
            {data.glProvenance.activeVoucherNos.length > 8 ? '…' : ''})
          </span>
        </div>
      )}

      {data.glProvenance && data.glProvenance.supersededDrafts.length > 0 && (
        <details className="frv-gl-superseded frv-no-print">
          <summary>
            Superseded vouchers (struck through for transparency)
          </summary>
          <ul className="frv-gl-superseded-list">
            {data.glProvenance.supersededDrafts.map(d => (
              <li key={d.journalId} className="frv-gl-superseded-item">
                <span className="frv-gl-superseded-strike">{d.voucherNo}</span>
                <span className="frv-gl-superseded-by">
                  → Superseded by: <strong>{d.supersededByVoucherNo}</strong>
                </span>
                <ul className="frv-gl-superseded-lines">
                  {d.lines.map((ln, i) => (
                    <li key={i}>
                      {ln.account_code} Dr {ln.debit > 0 ? fmt(ln.debit) : '—'} Cr{' '}
                      {ln.credit > 0 ? fmt(ln.credit) : '—'}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </details>
      )}

      {/* Suspense warning strip — shown on all tabs when suspense items exist */}
      <SuspenseWarning summary={data.summary} />

      {/* Tab content */}
      <div className="frv-tab-content">
        {activeTab === 'trial' && (
          <TrialBalanceTab rows={data.trialBalanceRows} summary={data.summary} />
        )}
        {activeTab === 'income' && (
          <IncomeStatementTab rows={data.incomeRows} summary={data.summary} periodLabel={dateLabel} />
        )}
        {activeTab === 'balance' && (
          <BalanceSheetTab
            rows={data.balanceRows}
            summary={data.summary}
            periodLabel={dateLabel}
            asAtDate={data.dateTo || data.dateFrom || dateLabel}
          />
        )}
      </div>

      <div className="frv-footer">
        Generated: {new Date(data.generatedAt).toLocaleString('en-HK', { hour12: false })}
      </div>
    </div>
  )
}
