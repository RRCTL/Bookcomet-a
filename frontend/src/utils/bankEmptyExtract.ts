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

export type EmptyExtractPageFlag = {
  page: number
  taskFileId: string
  filename: string
  autoRetried: boolean
  title: string
  body: string
}

type RunFileLike = {
  task_file_id: string
  original_filename?: string | null
  file_status?: string | null
  gate_result?: string | null
  result_summary_json?: Record<string, unknown> | null
}

function pageHasPendingEmptyExtract(page: Record<string, unknown>): boolean {
  if (page.empty_extract_acknowledged === true) return false
  if (page.empty_extract === true) return true
  return page.status === 'needs_retry'
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
      const pageNum = Number(page.page)
      if (!Number.isFinite(pageNum) || pageNum < 1) continue
      out.push({
        page: pageNum,
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
