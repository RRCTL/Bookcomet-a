import {
  buildAiSummaryMarkdown,
  buildTaskHeaderMarkdown,
  renderMD,
  type MDTask,
} from './MDRecordViewer'
import './ExtractionSummaryPanel.css'

const MAX_ROWS = 50

export type OcrDestinationPayload = {
  messageId: string
  kind: 'bank' | 'arap'
}

type ExtractionMessage = MDTask['messages'][number] & {
  id: string
  bankFilename?: string
  arapFilename?: string
  fileRefs?: { id: string; name: string }[]
}

/** Same shape as active task messages; each message must have `id` for jump targets. */
export type ExtractionSummaryTask = Omit<MDTask, 'messages'> & {
  messages: ExtractionMessage[]
}

export interface ExtractionSummaryPanelProps {
  task: ExtractionSummaryTask
  onDestination?: (p: OcrDestinationPayload) => void
}

function bankDesc(t: Record<string, unknown>): string {
  return String(t.particulars ?? t.description_raw ?? '').slice(0, 40)
}

function tableFileTitle(specific?: string, fileRefs?: { id: string; name: string }[]): string {
  const a = specific?.trim()
  if (a) return a
  const n = fileRefs?.[0]?.name?.trim()
  if (n) return n
  return '—'
}

function isArapExtractionMode(pm: string): boolean {
  return pm === 'AP' || pm === 'AR'
}

function messageIsArapOcrSnapshot(m: ExtractionMessage): boolean {
  if (String(m.contentType || '').trim() !== 'ocr_snapshot') return false
  const hasAr = (m.arapTransactions?.length ?? 0) > 0
  const hasSheetOnly =
    (m.spreadsheetData?.length ?? 0) > 0 && !(m.bankTransactions && m.bankTransactions.length > 0)
  return hasAr || hasSheetOnly
}

export function ExtractionSummaryPanel({ task, onDestination }: ExtractionSummaryPanelProps) {
  const headerMd = buildTaskHeaderMarkdown(task)
  const aiMd = buildAiSummaryMarkdown(task)

  const suppressNonSnapshotArapTables =
    isArapExtractionMode(task.processingMode) && task.messages.some(messageIsArapOcrSnapshot)

  const showArApTxnBlock = (m: ExtractionMessage): boolean => {
    if (!m.arapTransactions || m.arapTransactions.length === 0) return false
    if (suppressNonSnapshotArapTables && String(m.contentType || '').trim() !== 'ocr_snapshot') {
      return false
    }
    return true
  }

  const showArApSpreadsheetOnlyBlock = (m: ExtractionMessage): boolean => {
    if (!m.spreadsheetData || m.spreadsheetData.length === 0) return false
    if (m.bankTransactions && m.bankTransactions.length > 0) return false
    if (m.arapTransactions && m.arapTransactions.length > 0) return false
    if (suppressNonSnapshotArapTables && String(m.contentType || '').trim() !== 'ocr_snapshot') {
      return false
    }
    return true
  }

  let hasTxn = false
  for (const m of task.messages) {
    if (m.bankTransactions && m.bankTransactions.length > 0) {
      hasTxn = true
      break
    }
    if (showArApTxnBlock(m) || showArApSpreadsheetOnlyBlock(m)) {
      hasTxn = true
      break
    }
  }

  return (
    <div className="extraction-summary-panel md-record-viewer">
      <div
        className="extraction-summary-md"
        dangerouslySetInnerHTML={{ __html: renderMD(headerMd) }}
      />

      {hasTxn && <h2 className="extraction-summary-h2">Transactions</h2>}

      {task.messages.map((m) => (
        <div key={m.id} className="extraction-summary-msg">
          {m.bankTransactions && m.bankTransactions.length > 0 && (
            <details className="extraction-details">
              <summary className="extraction-details-summary">
                <span className="extraction-chevron" aria-hidden>▶</span>
                <span className="extraction-filename" title={tableFileTitle(m.bankFilename, m.fileRefs)}>
                  {tableFileTitle(m.bankFilename, m.fileRefs)}
                </span>
                <span className="extraction-kind-meta">Bank · {m.bankTransactions.length} rows</span>
                <button
                  type="button"
                  className="extraction-summary-dest-btn"
                  title="Scroll to the main bank table for this file"
                  onClick={(e) => {
                    e.preventDefault()
                    e.stopPropagation()
                    onDestination?.({ messageId: m.id, kind: 'bank' })
                  }}
                >
                  Destination
                </button>
              </summary>
              <div className="extraction-details-body">
                <div className="md-table-wrapper">
                  <table className="md-table extraction-summary-table">
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>Description</th>
                        <th>Deposit</th>
                        <th>Withdrawal</th>
                        <th>Balance</th>
                        <th>Account Code</th>
                      </tr>
                    </thead>
                    <tbody>
                      {m.bankTransactions.slice(0, MAX_ROWS).map((t, i) => (
                        <tr key={`${m.id}-b-${i}`}>
                          <td>{String((t as Record<string, unknown>).bank_date ?? '')}</td>
                          <td>{bankDesc(t as Record<string, unknown>)}</td>
                          <td>{String((t as Record<string, unknown>).deposit ?? '')}</td>
                          <td>{String((t as Record<string, unknown>).withdrawal ?? '')}</td>
                          <td>{String((t as Record<string, unknown>).balance ?? '')}</td>
                          <td>{String((t as Record<string, unknown>).account_code ?? '')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {m.bankTransactions.length > MAX_ROWS && (
                  <p className="extraction-summary-more">*... {m.bankTransactions.length - MAX_ROWS} more rows*</p>
                )}
              </div>
            </details>
          )}

          {showArApTxnBlock(m) && (
            <details className="extraction-details">
              <summary className="extraction-details-summary">
                <span className="extraction-chevron" aria-hidden>▶</span>
                <span className="extraction-filename" title={tableFileTitle(m.arapFilename, m.fileRefs)}>
                  {tableFileTitle(m.arapFilename, m.fileRefs)}
                </span>
                <span className="extraction-kind-meta">{task.processingMode} · {m.arapTransactions.length} rows</span>
                <button
                  type="button"
                  className="extraction-summary-dest-btn"
                  title="Scroll to the main AR/AP table for this file"
                  onClick={(e) => {
                    e.preventDefault()
                    e.stopPropagation()
                    onDestination?.({ messageId: m.id, kind: 'arap' })
                  }}
                >
                  Destination
                </button>
              </summary>
              <div className="extraction-details-body">
                <div className="md-table-wrapper">
                  <table className="md-table extraction-summary-table">
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>Type</th>
                        <th>Amount</th>
                        <th>Currency</th>
                        <th>Payer</th>
                        <th>Payee</th>
                        <th>Account Code</th>
                      </tr>
                    </thead>
                    <tbody>
                      {m.arapTransactions.slice(0, MAX_ROWS).map((t, i) => (
                        <tr key={`${m.id}-a-${i}`}>
                          <td>{String((t as Record<string, unknown>).date ?? '')}</td>
                          <td>{String((t as Record<string, unknown>).transaction_type ?? '')}</td>
                          <td>{String((t as Record<string, unknown>).amount ?? '')}</td>
                          <td>{String((t as Record<string, unknown>).currency ?? '')}</td>
                          <td>{String((t as Record<string, unknown>).payer ?? '').slice(0, 20)}</td>
                          <td>{String((t as Record<string, unknown>).payee ?? '').slice(0, 20)}</td>
                          <td>{String((t as Record<string, unknown>).account_code ?? '')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {m.arapTransactions.length > MAX_ROWS && (
                  <p className="extraction-summary-more">*... {m.arapTransactions.length - MAX_ROWS} more rows*</p>
                )}
              </div>
            </details>
          )}

          {showArApSpreadsheetOnlyBlock(m) && (
            <details className="extraction-details">
              <summary className="extraction-details-summary">
                <span className="extraction-chevron" aria-hidden>▶</span>
                <span className="extraction-filename" title={tableFileTitle(m.arapFilename, m.fileRefs)}>
                  {tableFileTitle(m.arapFilename, m.fileRefs)}
                </span>
                <span className="extraction-kind-meta">
                  {task.processingMode} · {m.spreadsheetData.length} rows (table)
                </span>
                <button
                  type="button"
                  className="extraction-summary-dest-btn"
                  title="Scroll to the main spreadsheet for this message"
                  onClick={(e) => {
                    e.preventDefault()
                    e.stopPropagation()
                    onDestination?.({ messageId: m.id, kind: 'arap' })
                  }}
                >
                  Destination
                </button>
              </summary>
              <div className="extraction-details-body">
                <div className="md-table-wrapper">
                  <table className="md-table extraction-summary-table">
                    <thead>
                      <tr>
                        <th>Voucher</th>
                        <th>Date</th>
                        <th>Type</th>
                        <th>Amount</th>
                        <th>Currency</th>
                        <th>Payer</th>
                        <th>Payee</th>
                        <th>Category</th>
                        <th>Memo</th>
                      </tr>
                    </thead>
                    <tbody>
                      {m.spreadsheetData.slice(0, MAX_ROWS).map((row, i) => {
                        const r = row as Record<string, unknown>
                        return (
                          <tr key={`${m.id}-s-${i}`}>
                            <td>{String(r.voucher_no ?? '')}</td>
                            <td>{String(r.date ?? '')}</td>
                            <td>{String(r.transaction_type ?? '')}</td>
                            <td>{String(r.amount ?? '')}</td>
                            <td>{String(r.currency ?? '')}</td>
                            <td>{String(r.payer ?? '').slice(0, 20)}</td>
                            <td>{String(r.payee ?? '').slice(0, 20)}</td>
                            <td>{String(r.category ?? '').slice(0, 24)}</td>
                            <td>{String(r.memo ?? '').slice(0, 40)}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
                {m.spreadsheetData.length > MAX_ROWS && (
                  <p className="extraction-summary-more">*... {m.spreadsheetData.length - MAX_ROWS} more rows*</p>
                )}
              </div>
            </details>
          )}
        </div>
      ))}

      {aiMd && (
        <div
          className="extraction-summary-md"
            dangerouslySetInnerHTML={{ __html: renderMD(aiMd) }}
        />
      )}

      {!hasTxn && !aiMd.trim() && (
        <p className="extraction-summary-empty">*No records yet. Upload files or start a conversation.*</p>
      )}
    </div>
  )
}
