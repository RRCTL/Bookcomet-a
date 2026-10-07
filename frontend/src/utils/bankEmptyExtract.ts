/**
 * Dense-OCR empty-extract miss (workflow BANK multi-page).
 * This is an extract failure on a busy statement page — NOT "the PDF page is blank".
 */

export const EMPTY_EXTRACT_TITLE = 'Extraction returned no rows — retry this page'
export const EMPTY_EXTRACT_BODY =
  'This page had a lot of statement text, but the extract came back empty. The page is not treated as blank.'
export const EMPTY_EXTRACT_RETRY_ACTION = 'Retry this page'
export const EMPTY_EXTRACT_MARK_ACTION = 'Mark as reviewed with no rows'
export const EMPTY_EXTRACT_MARK_CONFIRM =
  'Only use this if you have checked the PDF and this page really has no transactions to extract.'
export const REVIEWED_NO_ROWS_LABEL = 'Reviewed — no rows'
export const REVIEWED_NO_ROWS_GATE = 'REVIEWED_NO_ROWS'
export const VIEW_PDF_ACTION = 'View PDF'

export type EmptyExtractPageFlag = {
  page: number
  taskFileId: string
  filename: string
  autoRetried: boolean
  title: string
  body: string
}

export type LivePageStatus = {
  page: number
  label: string
}

type RunFileLike = {
  task_file_id: string
  original_filename?: string | null
  file_status?: string | null
  gate_result?: string | null
  error_text?: string | null
  page_count?: number | null
  result_summary_json?: Record<string, unknown> | null
}

function pageNum(page: Record<string, unknown>): number | null {
  const n = Number(page.page)
  if (!Number.isFinite(n) || n < 1) return null
  return Math.floor(n)
}

function pageHasPendingEmptyExtract(page: Record<string, unknown>): boolean {
  if (page.empty_extract_acknowledged === true) return false
  if (page.empty_extract === true) return true
  return page.status === 'needs_retry'
}

function pageIsReviewedNoRows(page: Record<string, unknown>): boolean {
  if (page.empty_extract_acknowledged === true) return true
  return page.status === 'reviewed_no_rows'
}

export function collectEmptyExtractPagesFromRunFiles(
  files: RunFileLike[] | null | undefined,
): EmptyExtractPageFlag[] {
  if (!files?.length) return []
  const out: EmptyExtractPageFlag[] = []
  for (const f of files) {
    const summary = f.result_summary_json
    if (!summary || typeof summary !== 'object') continue
    const pages = summary.pages
    if (!Array.isArray(pages)) continue
    for (const raw of pages) {
      if (!raw || typeof raw !== 'object') continue
      const page = raw as Record<string, unknown>
      if (!pageHasPendingEmptyExtract(page)) continue
      const num = pageNum(page)
      if (num == null) continue
      out.push({
        page: num,
        taskFileId: f.task_file_id,
        filename: f.original_filename || f.task_file_id,
        autoRetried: page.empty_extract_auto_retried === true,
        title:
          typeof page.empty_extract_title === 'string' && page.empty_extract_title.trim()
            ? page.empty_extract_title
            : EMPTY_EXTRACT_TITLE,
        body:
          typeof page.empty_extract_body === 'string' && page.empty_extract_body.trim()
            ? page.empty_extract_body
            : EMPTY_EXTRACT_BODY,
      })
    }
  }
  return out.sort((a, b) => a.page - b.page || a.filename.localeCompare(b.filename))
}

export function hasPendingEmptyExtractPages(
  files: RunFileLike[] | null | undefined,
): boolean {
  return collectEmptyExtractPagesFromRunFiles(files).length > 0
}

export function emptyExtractBannerText(count: number): string {
  if (count <= 0) return ''
  if (count === 1) return EMPTY_EXTRACT_TITLE
  return `${count} pages: ${EMPTY_EXTRACT_TITLE}`
}

/** Per-page Live output fragments for one run file (mixed statuses stay visible). */
export function collectLivePageStatusesForFile(file: RunFileLike): LivePageStatus[] {
  const summary = file.result_summary_json
  const pages =
    summary && typeof summary === 'object' && Array.isArray(summary.pages)
      ? (summary.pages as unknown[])
      : []
  const out: LivePageStatus[] = []
  const seen = new Set<number>()
  const dictPages = pages.filter((p): p is Record<string, unknown> => !!p && typeof p === 'object')
  for (const page of [...dictPages].sort((a, b) => (pageNum(a) ?? 0) - (pageNum(b) ?? 0))) {
    const n = pageNum(page)
    if (n == null || seen.has(n)) continue
    seen.add(n)
    if (pageIsReviewedNoRows(page)) {
      out.push({ page: n, label: `Page ${n}: ${REVIEWED_NO_ROWS_LABEL}` })
    } else if (pageHasPendingEmptyExtract(page)) {
      out.push({ page: n, label: `Page ${n}: needs retry` })
    } else if (String(page.status ?? '').toLowerCase() === 'error') {
      out.push({ page: n, label: `Page ${n}: error` })
    } else if (['running', 'processing', 'pending'].includes(String(page.status ?? '').toLowerCase())) {
      out.push({ page: n, label: `Page ${n}: Processing` })
    }
  }
  const pageCount = Number(file.page_count ?? summary?.total_pages ?? 0)
  if (file.file_status === 'running' && Number.isFinite(pageCount) && pageCount > 0) {
    for (let n = 1; n <= pageCount; n += 1) {
      if (!seen.has(n)) {
        out.push({ page: n, label: `Page ${n}: Processing` })
        seen.add(n)
      }
    }
  }
  // Prefer server-authored live_page_statuses when present and pages are empty.
  if (
    out.length === 0 &&
    summary &&
    typeof summary.live_page_statuses === 'string' &&
    summary.live_page_statuses.trim()
  ) {
    return summary.live_page_statuses
      .split(' · ')
      .map(s => s.trim())
      .filter(Boolean)
      .map((label, i) => ({ page: i + 1, label }))
  }
  return out
}

export function formatLiveOutputPageStatuses(file: RunFileLike): string {
  if (typeof file.error_text === 'string' && file.error_text.includes('Page ')) {
    return file.error_text
  }
  const summary = file.result_summary_json
  if (
    summary &&
    typeof summary.live_page_statuses === 'string' &&
    summary.live_page_statuses.trim()
  ) {
    return summary.live_page_statuses.trim()
  }
  return collectLivePageStatusesForFile(file)
    .map(s => s.label)
    .join(' · ')
}

export function fileLooksClearOfEmptyExtract(file: RunFileLike): boolean {
  return collectEmptyExtractPagesFromRunFiles([file]).length === 0
}
