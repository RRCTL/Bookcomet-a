export interface MDTaskMessage {
  role: string
  content: string
  contentType?: string
  bankTransactions?: Array<Record<string, unknown>>
  arapTransactions?: Array<Record<string, unknown>>
  spreadsheetData?: Array<Record<string, unknown>>
  progressPercent?: number
  progressLabel?: string
  progressMeta?: {
    fileIndex?: number
    totalFiles?: number
    processingFiles?: number
    pageCurrent?: number
    pageTotal?: number
  }
  /** Derived from chat upload bubbles — avoids passing raw File blobs into MD builders. */
  uploadedFileCount?: number
}

export interface MDTask {
  title: string
  processingMode: string
  createdAt: string
  fileCount: number
  /** When server `file_count` lags, prefer queue/upload-derived counts for the header. */
  displayFileCount?: number
  messages: MDTaskMessage[]
}

function msgHasTxnPayload(m: MDTaskMessage): boolean {
  return Boolean(
    (m.bankTransactions && m.bankTransactions.length > 0) ||
      (m.arapTransactions && m.arapTransactions.length > 0) ||
      (m.spreadsheetData && m.spreadsheetData.length > 0),
  )
}

function formatProgressBullet(m: MDTaskMessage): string | null {
  const body = (m.content || '').replace(/\n/g, ' ').trim()
  const pct = m.progressPercent
  const label = (m.progressLabel || '').trim()
  const isProgressish =
    typeof pct === 'number' ||
    /處理進度|處理中|Processing|Progress/.test(body)
  if (!isProgressish) return null
  const pctPart = typeof pct === 'number' ? ` (${pct}%)` : ''
  const meta = m.progressMeta
  const batchHint =
    meta?.fileIndex != null && meta?.totalFiles != null
      ? ` · file ${meta.fileIndex}/${meta.totalFiles}`
      : ''
  const labelPart = label ? `${label}${pctPart}${batchHint}` : `Processing${pctPart}${batchHint}`
  if (!body.length) return `- ${labelPart}`
  return `- ${labelPart} — ${body.slice(0, 160)}${body.length > 160 ? '…' : ''}`
}

/** Task title line + meta for the right panel (no transaction tables). */
export function buildTaskHeaderMarkdown(task: MDTask): string {
  const lines: string[] = []
  const d = new Date(task.createdAt)
  const dateStr = d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
  const fileDisplay = task.displayFileCount ?? task.fileCount
  lines.push(`# ${task.title}`)
  lines.push(`**Mode:** ${task.processingMode}  |  **Date:** ${dateStr}  |  **Files:** ${fileDisplay}`)
  lines.push('')
  lines.push('---')
  lines.push('')
  return lines.join('\n')
}

/** Chronological chat / upload / OCR progress lines for the extraction panel (immediate feedback). */
export function buildAiSummaryMarkdown(task: MDTask): string {
  if (!task.messages.length) return ''
  const bullets: string[] = []
  for (const m of task.messages.slice(-120)) {
    if (msgHasTxnPayload(m)) continue
    if (m.role === 'user') {
      const c = (m.content || '').trim()
      const uploadN = m.uploadedFileCount ?? 0
      if (!c && uploadN > 0) {
        bullets.push(`- **You:** (${uploadN} file${uploadN === 1 ? '' : 's'} attached)`)
        continue
      }
      if (!c) continue
      const ex = c.slice(0, 220).replace(/\n/g, ' ')
      bullets.push(`- **You:** ${ex}${c.length > 220 ? '…' : ''}`)
      continue
    }
    if (m.role !== 'assistant') continue
    const rawContent = (m.content || '').trim()
    if (rawContent === '__QUEUE_NOTICE__') continue
    const pb = formatProgressBullet(m)
    if (pb) {
      bullets.push(pb)
      continue
    }
    const c = m.content || ''
    if (c.startsWith('已加入') || c.startsWith('已上傳') || c.startsWith('已追加') || c.startsWith('Queued') || c.startsWith('Uploaded') || c.startsWith('Added')) {
      const ex = c.slice(0, 220).replace(/\n/g, ' ')
      bullets.push(`- ${ex}${c.length > 220 ? '…' : ''}`)
      continue
    }
    if (c.length > 10) {
      const ex = c.slice(0, 220).replace(/\n/g, ' ')
      bullets.push(`- ${ex}${c.length > 220 ? '…' : ''}`)
    }
  }
  if (!bullets.length) return ''
  return ['## Chat & processing', '', ...bullets, ''].join('\n')
}

/** Build a Markdown string from the active task's data */
export function buildTaskMD(task: MDTask): string {
  const lines: string[] = []
  lines.push(buildTaskHeaderMarkdown(task))

  let hasTxns = false
  for (const m of task.messages) {
    if (m.bankTransactions && m.bankTransactions.length > 0) {
      if (!hasTxns) { lines.push('## Transactions'); hasTxns = true }
      lines.push(`### Bank Transactions (${m.bankTransactions.length} rows)`)
      lines.push('| Date | Description | Deposit | Withdrawal | Balance | Account Code |')
      lines.push('|------|-------------|---------|------------|---------|--------------|')
      for (const t of m.bankTransactions.slice(0, 50)) {
        lines.push(
          `| ${t.bank_date ?? ''} | ${String(t.particulars ?? t.description_raw ?? '').slice(0, 40)} | ${t.deposit ?? ''} | ${t.withdrawal ?? ''} | ${t.balance ?? ''} | ${t.account_code ?? ''} |`
        )
      }
      if (m.bankTransactions.length > 50) lines.push(`*... ${m.bankTransactions.length - 50} more rows*`)
      lines.push('')
    }

    if (m.arapTransactions && m.arapTransactions.length > 0) {
      if (!hasTxns) { lines.push('## Transactions'); hasTxns = true }
      lines.push(`### ${task.processingMode} Transactions (${m.arapTransactions.length} rows)`)
      lines.push('| Date | Type | Amount | Currency | Payer | Payee | Account Code |')
      lines.push('|------|------|--------|----------|-------|-------|--------------|')
      for (const t of m.arapTransactions.slice(0, 50)) {
        lines.push(
          `| ${t.date ?? ''} | ${t.transaction_type ?? ''} | ${t.amount ?? ''} | ${t.currency ?? ''} | ${String(t.payer ?? '').slice(0, 20)} | ${String(t.payee ?? '').slice(0, 20)} | ${t.account_code ?? ''} |`
        )
      }
      if (m.arapTransactions.length > 50) lines.push(`*... ${m.arapTransactions.length - 50} more rows*`)
      lines.push('')
    }
  }

  const aiMd = buildAiSummaryMarkdown(task)
  if (aiMd) lines.push(aiMd)

  if (!hasTxns && !aiMd.trim()) {
    lines.push('*No records yet. Upload files or start a conversation.*')
  }

  return lines.join('\n')
}

/** Lightweight markdown-to-HTML renderer (no dependencies) */
export function renderMD(md: string): string {
  const lines = md.split('\n')
  const html: string[] = []
  let inTable = false
  let isFirstTableRow = false

  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

  const inline = (s: string) =>
    esc(s)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g, '<em>$1</em>')
      .replace(/`(.+?)`/g, '<code>$1</code>')

  const closeTable = () => {
    html.push('</tbody></table></div>')
    inTable = false
    isFirstTableRow = false
  }

  for (const raw of lines) {
    const line = raw

    // Match any pipe-starting line (covers "| text |" and "|---|---|" separators)
    if (line.startsWith('|')) {
      const cells = line.split('|').slice(1, -1)
      // Skip separator rows (|---|---|)
      if (cells.every(c => /^[-: ]+$/.test(c))) continue

      if (!inTable) {
        // Open wrapper + table + thead with first row as <th>
        html.push('<div class="md-table-wrapper"><table class="md-table">')
        html.push('<thead><tr>' + cells.map(c => `<th>${inline(c.trim())}</th>`).join('') + '</tr></thead>')
        html.push('<tbody>')
        inTable = true
        isFirstTableRow = true
        continue
      }

      html.push('<tr>' + cells.map(c => `<td>${inline(c.trim())}</td>`).join('') + '</tr>')
      continue
    }

    if (inTable) closeTable()

    if (line.startsWith('# '))   { html.push(`<h1>${inline(line.slice(2))}</h1>`); continue }
    if (line.startsWith('## '))  { html.push(`<h2>${inline(line.slice(3))}</h2>`); continue }
    if (line.startsWith('### ')) { html.push(`<h3>${inline(line.slice(4))}</h3>`); continue }
    if (line === '---')          { html.push('<hr />'); continue }
    if (line.startsWith('- '))   { html.push(`<li>${inline(line.slice(2))}</li>`); continue }
    if (line === '')             { html.push('<br />'); continue }
    html.push(`<p>${inline(line)}</p>`)
  }
  if (inTable) closeTable()
  return html.join('')
}

interface Props {
  content: string
}

export function MDRecordViewer({ content }: Props) {
  if (!content) {
    return (
      <div className="md-empty">
        <p>No record yet.</p>
        <p className="md-empty-hint">Upload files or start a conversation to generate a record.</p>
      </div>
    )
  }

  return (
    <div
      className="md-record-viewer"
      dangerouslySetInnerHTML={{ __html: renderMD(content) }}
    />
  )
}
