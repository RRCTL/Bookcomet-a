import { describe, expect, it } from 'vitest'
import {
  PDF_NOT_AVAILABLE_FOR_RUN,
  buildPdfPreviewSrc,
  normalizePreviewPage,
} from './pdfPreviewSrc'

describe('buildPdfPreviewSrc', () => {
  it('adds FitH when no page is known', () => {
    expect(buildPdfPreviewSrc('blob:fictional')).toBe('blob:fictional#view=FitH')
  })

  it('opens at the named page when known', () => {
    expect(buildPdfPreviewSrc('blob:fictional', 3)).toBe('blob:fictional#page=3&view=FitH')
  })

  it('ignores invalid page values', () => {
    expect(buildPdfPreviewSrc('blob:fictional', 0)).toBe('blob:fictional#view=FitH')
    expect(buildPdfPreviewSrc('blob:fictional', -1)).toBe('blob:fictional#view=FitH')
  })
})

describe('normalizePreviewPage', () => {
  it('keeps valid pages and drops invalid', () => {
    expect(normalizePreviewPage(2)).toBe(2)
    expect(normalizePreviewPage(2.9)).toBe(2)
    expect(normalizePreviewPage(0)).toBeNull()
    expect(normalizePreviewPage(null)).toBeNull()
  })
})

describe('PDF_NOT_AVAILABLE_FOR_RUN', () => {
  it('uses the exact missing-file copy', () => {
    expect(PDF_NOT_AVAILABLE_FOR_RUN).toBe('PDF not available for this run')
  })
})
