import type { ARAPTransaction } from '../../components/ARAPReview'
import type { ProcessingMode } from '../../components/ModeSelector'
import type { Message } from './types'

/** If every row is AR or every row is AP, return that mode; else null (mixed or invalid / empty 類型). */
export function inferHomogeneousArapMode(txns: ARAPTransaction[] | undefined): 'AR' | 'AP' | null {
  if (!txns?.length) return null
  const types = new Set<string>()
  for (const t of txns) {
    const v = String(t.transaction_type ?? '').trim().toUpperCase()
    if (v !== 'AR' && v !== 'AP') return null
    types.add(v)
  }
  if (types.size !== 1) return null
  return types.has('AR') ? 'AR' : 'AP'
}

/**
 * Prefer ocr_snapshot (authoritative saved table), else latest message with homogeneous AR/AP.
 * Avoids stale earlier assistant payloads overwriting the folder after refresh.
 */
export function inferCanonicalHomogeneousArapFromMessages(messages: Message[]): 'AR' | 'AP' | null {
  let lastSnapshot: 'AR' | 'AP' | null = null
  for (const m of messages) {
    if (m.contentType !== 'ocr_snapshot') continue
    const s = inferHomogeneousArapMode(m.arapTransactions)
    if (s) lastSnapshot = s
  }
  if (lastSnapshot) return lastSnapshot
  for (let i = messages.length - 1; i >= 0; i--) {
    const s = inferHomogeneousArapMode(messages[i].arapTransactions)
    if (s) return s
  }
  return null
}

/** Align task folder with homogeneous OCR type when server metadata is stale (e.g. after refresh). */
export function processingModeReconciledWithArapSnapshot(
  processingMode: ProcessingMode,
  messages: Message[],
): ProcessingMode {
  const canonical = inferCanonicalHomogeneousArapFromMessages(messages)
  if (canonical && (processingMode === 'AR' || processingMode === 'AP') && canonical !== processingMode) {
    return canonical
  }
  return processingMode
}
