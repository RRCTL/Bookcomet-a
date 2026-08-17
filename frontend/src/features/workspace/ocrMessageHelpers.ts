import type { OcrResult } from '../../services/api'
import type { Message } from './types'

/** Matches assistant OCR completion lines (same phrase as `ocrContent` builders in App). */
const OCR_SUMMARY_MARKER = 'OCR \u8b58\u5225\u5b8c\u6210'

export function isOcrSummaryMessage(message: Message): boolean {
  return message.role === 'assistant' && message.content.includes(OCR_SUMMARY_MARKER)
}

export function extractFullOcrText(ocrResult?: OcrResult): string {
  if (!ocrResult) return ''
  const raw = (ocrResult as { raw_ocr?: unknown }).raw_ocr || ocrResult
  if (typeof (raw as { text?: unknown })?.text === 'string' && String((raw as { text: string }).text).trim()) {
    return (raw as { text: string }).text
  }
  const pages = Array.isArray((ocrResult as unknown as { pages?: unknown }).pages)
    ? (ocrResult as unknown as { pages: Array<{ text?: string; page?: number }> }).pages
    : []
  if (pages.length > 0) {
    return pages
      .map((page: { text?: string; page?: number }, idx: number) => {
        const pageText = typeof page?.text === 'string' ? page.text : ''
        if (!pageText.trim()) return ''
        return `=== Page ${page.page || idx + 1} ===\n${pageText}`
      })
      .filter(Boolean)
      .join('\n\n')
  }
  return ''
}

export function looksLikeHtmlTable(text: string): boolean {
  return /<table[\s>]|<tr[\s>]|<td[\s>]/i.test(text)
}
