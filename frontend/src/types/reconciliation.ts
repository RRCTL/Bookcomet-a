export type TransactionStatus = 'unreconciled' | 'reconciled' | 'pending' | 'exception' | 'matched'

export type ReconState = Record<string, { status: 'matched' | 'unmatched'; matched_id: string }>

export type MatchDecision = 'auto' | 'manual' | 'pending'

export type MatchType = 'rule1' | 'rule2' | 'rule3' | 'manual' | 'one_many' | 'many_one' | 'many_many'

export type MatchCardinality =
  | '1:1'
  | '1:N'
  | 'N:1'
  | 'N:N'
  | 'N:0'
  | 'GL:1'
  | `0:${number}`

export interface BankTransaction {
  id: string
  account_id: string
  bank_date: string
  amount: number
  currency: string
  description_raw: string
  description_norm: string
  account_category?: string
  reference?: string
  import_batch_id: string
  status: TransactionStatus
  created_at?: string
  updated_at?: string
}

export interface LedgerTransaction {
  id: string
  company_id: string
  module?: string
  doc_type: string
  doc_id: string
  book_date: string
  amount: number
  currency: string
  counterparty: string
  account_category?: string
  reference?: string
  import_batch_id?: string
  /** Explicit module side for RECON / GL ("Dr" | "Cr"). */
  dr_cr?: 'Dr' | 'Cr' | null
  status: TransactionStatus
  created_at?: string
  updated_at?: string
}

export interface ReconciliationMatch {
  id: string
  bank_txn_id: string | null
  ledger_txn_id: string | null
  group_id?: string | null
  match_type: MatchType
  confidence_score: number
  matched_by: string
  matched_at: string
  status: string
  bank_txn?: BankTransaction
  ledger_txn?: LedgerTransaction
}

export interface ReconciliationGroup {
  id: string
  match_cardinality: MatchCardinality
  total_bank_amount: number
  total_ledger_amount: number
  difference: number
  partial_remainder_txn_id?: string | null
  bank_txns?: BankTransaction[]
  ledger_txns?: LedgerTransaction[]
  created_by?: string
  created_at?: string
}

export interface MultiMatchRequest {
  bank_txn_ids: string[]
  ledger_txn_ids: string[]
}

export interface ClearBankRequest {
  bank_txn_ids: string[]
}

export interface GlOnlyMatchRequest {
  bank_txn_ids: string[]
}

export interface LedgerPendingMatchRequest {
  ledger_txn_ids: string[]
}

export interface ClearBankResponse {
  group_id: string
  match_cardinality: MatchCardinality
  total_bank_amount: number
  total_ledger_amount: number
  difference: number
  match_rows_created: number
  gl_only?: boolean
  gl_offset_code?: string
}

export interface MultiMatchResponse {
  group_id: string
  match_cardinality: MatchCardinality
  total_bank_amount: number
  total_ledger_amount: number
  difference: number
  partial_remainder_txn_id?: string | null
  match_rows_created: number
}

export interface GroupUnmatchMemberRequest {
  group_id: string
  txn_id: string
  txn_type: 'bank' | 'ledger'
  reason: string
}

export interface GroupUnmatchMemberResponse {
  group_id: string
  group_dissolved: boolean
  remaining_members: number
}

export interface AutoMatchResult {
  bank_txn_id: string
  ledger_txn_id: string
  score: number
  match_type: MatchType
  decision: MatchDecision
}

export interface AutoMatchResponse {
  total_matches: number
  auto_matches: number
  manual_review: number
  matches: AutoMatchResult[]
}

export interface ManualMatchRequest {
  bank_txn_id: string
  ledger_txn_id: string
  user_id?: string
}

export interface UnmatchRequest {
  match_id: string
  reason: string
  user_id?: string
}

export interface ReconciliationStats {
  total_bank_txns: number
  total_ledger_txns: number
  matched: number
  unmatched_bank: number
  unmatched_ledger: number
  exceptions: number
  match_rate: number
}

export interface ChartOfAccountItem {
  id?: string
  code: string
  name_en: string
  name_zh: string
  category_type: string
  allowed_modes: string[]
  is_default?: boolean
  opening_balance?: number
  opening_balance_dr_cr?: 'Dr' | 'Cr'
}
