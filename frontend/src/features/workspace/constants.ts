import type { Message } from './types'

export const RECON_SHEET_LEGACY_EXTRA = [
  'Bank Mode 憑證號',
  'AR/AP Mode 憑證號',
  'Bank Total',
  'AR/AP Total',
  'Difference',
  'bank_txn_id',
  'ledger_txn_id',
  '信心度',
  'AI 配對原因',
] as const

export const MAX_CONCURRENT_TASKS = 3

/** Max simultaneous OCR requests per task (AR/AP multi-file parallel processing) */
export const MAX_CONCURRENT_OCR_FILES = 4

/** Max OCR file pipelines across all non-BANK tasks; keep in line with backend DB_HEAVY_WORK_CONCURRENCY. */
export const MAX_GLOBAL_CONCURRENT_OCR_FILES = 8

/** Hydrate AR/AP sidebar rows without starving browser connections to same host. */
export const MAX_ARAP_MESSAGES_PREFETCH_CONCURRENT = 5

/** Assistant placeholder while background AI job runs (must match thinking `Message.content`). */
export const AI_CHAT_THINKING_PLACEHOLDER = 'Thinking…'

export const seedMessages: Message[] = [
  {
    id: 'm1',
    role: 'assistant',
    content: 'Welcome. Upload a cheque image or PDF (single or multi-page) for OCR.',
  },
]

export const reconSeedMessages: Message[] = [
  {
    id: 'recon-welcome',
    role: 'assistant',
    content: 'Reconciliation mode (RECON).\n\nDrag Source and Bank transactions into the match area, then use AI Match or Match. Results appear here.',
    isReconResult: true,
  },
]
