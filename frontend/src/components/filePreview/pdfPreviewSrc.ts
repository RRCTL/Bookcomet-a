/** Exact copy for missing / out-of-run file preview. */
export const PDF_NOT_AVAILABLE_FOR_RUN = 'PDF not available for this run'

/**
 * Build a PDF iframe src that opens the in-app blob preview (not a download).
 * When `page` is known and >= 1, jump to that page via the PDF Open Parameters fragment.
 */
export function buildPdfPreviewSrc(previewUrl: string, page?: number | null): string {
  const parts: string[] = ['view=FitH']
  if (page != null && Number.isFinite(page) && page >= 1) {
    parts.unshift(`page=${Math.floor(page)}`)
  }
  return `${previewUrl}#${parts.join('&')}`
}

export function normalizePreviewPage(page?: number | null): number | null {
  if (page == null || !Number.isFinite(page) || page < 1) return null
  return Math.floor(page)
}
