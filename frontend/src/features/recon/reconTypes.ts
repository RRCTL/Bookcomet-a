import type { GlJournalLinePayload, GlJournalPayload } from '../../services/reconciliation'

export type ReconRawTxn = Record<string, unknown>

export type ReconGridRow = {
  id: string
  date: string
  amount: number
  currency: string
  description: string
  reference: string
  status: string
  docType?: string
  module?: string
  accountId?: string
  recordMode?: string
  /** Ledger stored side, or bank derived from amount sign. */
  drCr?: 'Dr' | 'Cr'
  /** Bank GL / CoA offset code (Books account_code → account_category). */
  accountCategory?: string
}

export type ReconFilters = {
  dateFrom: string
  dateTo: string
  bankAccount: string
  ledgerType: 'all' | 'AR' | 'AP'
}

export type ReconAiAction = {
  op: string
  bank_txn_ids?: string[]
  ledger_txn_ids?: string[]
  group_id?: string | null
  journal_id?: string | null
  voucher_no?: string | null
  gl_lines?: Array<{
    line_id?: string | null
    account_code?: string | null
    memo?: string | null
    debit?: number | null
    credit?: number | null
  }>
  deleted_line_ids?: string[]
}

/** Paginated GL/match review card embedded in an AI assistant message. */
export type ReconResultReview = {
  groupIds: string[]
  pageIndex: number
  /** ISO time when Match / AI Match last produced or updated this review. */
  processedAt?: string
}

export type ReconGlChoice = 'approve' | 'edit' | 'unpost' | 'skip' | 'cancel'

export type ReconAiMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  reconActions?: ReconAiAction[]
  reconActionsPending?: boolean
  isTyping?: boolean
  typingFullContent?: string
  /** Cursor-style MC review of matched groups (one page = one group). */
  resultReview?: ReconResultReview
}

/** Stable id so match/AI-match upserts one review bubble instead of stacking many. */
export const RECON_RESULT_REVIEW_MSG_ID = 'recon-result-review'

export type ReconGlMeta = {
  journal_id: string
  voucher_no: string
  status: string
  lines: GlJournalLinePayload[]
}

export type ReconGlPatchSeeds = {
  nonce: number
  byGroupId: Record<string, GlJournalPayload>
} | null

export type ReconGlRefetchSignal = {
  nonce: number
  groupIds: string[]
} | null
