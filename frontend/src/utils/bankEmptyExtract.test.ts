import { describe, expect, it } from 'vitest'
import {
  EMPTY_EXTRACT_BODY,
  EMPTY_EXTRACT_MARK_ACTION,
  EMPTY_EXTRACT_MARK_CONFIRM,
  EMPTY_EXTRACT_RETRY_ACTION,
  EMPTY_EXTRACT_TITLE,
  REVIEWED_NO_ROWS_LABEL,
  collectEmptyExtractPagesFromRunFiles,
  collectLivePageStatusesForFile,
  emptyExtractBannerText,
  formatLiveOutputPageStatuses,
  hasPendingEmptyExtractPages,
} from './bankEmptyExtract'

describe('bankEmptyExtract copy', () => {
  it('uses exact extract-miss wording (never implies blank PDF page)', () => {
    expect(EMPTY_EXTRACT_TITLE).toBe('Extraction returned no rows — retry this page')
    expect(EMPTY_EXTRACT_BODY).toBe(
      'This page had a lot of statement text, but the extract came back empty. The page is not treated as blank.',
    )
    expect(EMPTY_EXTRACT_RETRY_ACTION).toBe('Retry this page')
    expect(EMPTY_EXTRACT_MARK_ACTION).toBe('Mark as reviewed with no rows')
    expect(EMPTY_EXTRACT_MARK_CONFIRM).toBe(
      'Only use this if you have checked the PDF and this page really has no transactions to extract.',
    )
    expect(EMPTY_EXTRACT_TITLE.toLowerCase()).not.toContain('blank page')
    expect(EMPTY_EXTRACT_TITLE.toLowerCase()).not.toContain('no transactions on this page')
  })
})

describe('collectEmptyExtractPagesFromRunFiles', () => {
  it('collects pending amber pages and skips acknowledged', () => {
    const pages = collectEmptyExtractPagesFromRunFiles([
      {
        task_file_id: 'tf-fictional-1',
        original_filename: 'Fictional-Statement.pdf',
        result_summary_json: {
          pages: [
            { page: 1, status: 'success' },
            {
              page: 2,
              status: 'needs_retry',
              empty_extract: true,
              empty_extract_auto_retried: true,
              empty_extract_title: EMPTY_EXTRACT_TITLE,
              empty_extract_body: EMPTY_EXTRACT_BODY,
            },
            {
              page: 3,
              status: 'needs_retry',
              empty_extract: true,
              empty_extract_acknowledged: true,
            },
          ],
        },
      },
    ])
    expect(pages).toHaveLength(1)
    expect(pages[0]?.page).toBe(2)
    expect(pages[0]?.autoRetried).toBe(true)
    expect(pages[0]?.title).toBe(EMPTY_EXTRACT_TITLE)
    expect(hasPendingEmptyExtractPages([
      {
        task_file_id: 'tf-fictional-1',
        result_summary_json: { pages: [{ page: 2, empty_extract: true }] },
      },
    ])).toBe(true)
    expect(emptyExtractBannerText(2)).toContain(EMPTY_EXTRACT_TITLE)
  })

  it('formats mixed per-page Live output statuses', () => {
    const file = {
      task_file_id: 'tf-fictional-1',
      original_filename: 'Fictional-Statement.pdf',
      file_status: 'warning',
      result_summary_json: {
        pages: [
          {
            page: 1,
            status: 'reviewed_no_rows',
            empty_extract_acknowledged: true,
          },
          {
            page: 2,
            status: 'needs_retry',
            empty_extract: true,
          },
        ],
      },
    }
    const statuses = collectLivePageStatusesForFile(file)
    expect(statuses.map(s => s.label)).toEqual([
      `Page 1: ${REVIEWED_NO_ROWS_LABEL}`,
      'Page 2: needs retry',
    ])
    expect(formatLiveOutputPageStatuses(file)).toBe(
      `Page 1: ${REVIEWED_NO_ROWS_LABEL} · Page 2: needs retry`,
    )
  })
})
