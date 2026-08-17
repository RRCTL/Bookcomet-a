import type { SpreadsheetRow } from '../../components/EditableSpreadsheet'
import type { BankTransaction } from '../../components/BankStatementReview'
import type { ARAPTransaction } from '../../components/ARAPReview'
import type { ProcessingMode } from '../../components/ModeSelector'
import type { ReportSetupCardData } from '../../components/ReportSetupCard'
import type { ReconTransactionItem } from '../../components/ReconContainer'
import type { FinancialReportData } from '../../hooks/useReportData'
import type { GlDraftConflict } from '../../utils/mergeGlJournalsForReport'
import type { OcrResult } from '../../services/api'
import type { OtherRow } from '../../types/other'
import type { ApVlmTablePreset } from './apComposerOptions'

export type QueuedFile = {
  id: string
  file: File
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled'
  result?: any
  previewUrl?: string
  addedToSpreadsheet?: boolean
  processingMode?: ProcessingMode
  taskFileId?: string
  ocrJobId?: string
  bankJobId?: string
  /** Company/workspace where this file's OCR job was started. */
  companyId?: string
  /** User-initiated retries after OCR failure; drives follow-up copy in processFile catch. */
  ocrRetryCount?: number
  /** All files from one attach/drop/picker action share this id (one table per batch). */
  uploadBatchId?: string
}

export type TaskStatus = 'idle' | 'queued' | 'processing' | 'completed' | 'failed'

export type Message = {
  id: string
  role: 'user' | 'assistant'
  content: string
  progressPercent?: number
  progressLabel?: string
  progressMeta?: {
    fileIndex: number
    totalFiles: number
    processingFiles: number
    pageCurrent?: number
    pageTotal?: number
    /** 1-based page -> verified | needs_review from dual-model bank OCR */
    pageVerification?: Record<string, string>
  }
  progressJob?: {
    kind: 'ocr' | 'bank'
    jobId: string
    taskId: string
    fileId?: string
  }
  ocrResult?: OcrResult
  fullOcrText?: string
  spreadsheetData?: SpreadsheetRow[]
  spreadsheetColumns?: string[]
  spreadsheetHeaders?: string[]
  spreadsheetReadOnly?: boolean
  uploadedFiles?: QueuedFile[]
  reconciliationResults?: any
  reconRecords?: ChatTask[]
  reconUnmatched?: {
    bank: SpreadsheetRow[]
    ledger: SpreadsheetRow[]
  }
  reconContainer?: boolean
  isReconResult?: boolean
  bankTransactions?: BankTransaction[]
  bankFilename?: string
  isCashTable?: boolean
  arapTransactions?: ARAPTransaction[]
  arapFilename?: string
  /** AP composer table style when this OCR message was produced (`ap_table` unlocks dedicated AP columns in ARAPReview). */
  apVlmTablePreset?: ApVlmTablePreset
  reportSetupCard?: ReportSetupCardData
  reportGlDraftConflict?: {
    conflicts: GlDraftConflict[]
    baseOpts: {
      dateFrom: string
      dateTo: string
      suspenseCode: string
      arControlCode: string
      apControlCode: string
      bankCode: string
    }
    accumulatedPicks: Record<string, string>
  }
  financialReportData?: FinancialReportData
  dupAlertType?: 'warn' | 'cancel'
  dupFileNames?: string
  dupConfirmPending?: boolean
  dupConfirmId?: string
  csvHint?: boolean
  fileRefs?: { id: string; name: string }[]
  gateCard?: {
    gateResult: string
    gateMessage: string
    fileName: string
    documentSubtype: string
    ocrText: string
    sourceTaskId: string
    processingMode: string
    originalFile: File
  }
  otherRecords?: OtherRow[]
  /** Legacy; 15-min stage nudges removed — kept for old persisted tasks */
  stagePrompt?: {
    fromStage: 'OCR' | 'RECON'
    completedCount: number
    failedCount: number
    dismissed?: boolean
  }
  saveRulePending?: boolean
  saveRuleProposal?: { type: string; vendor?: string; field: string; value: string } | null
  ruleSaved?: boolean
  ruleSavedMessage?: string
  /** Assistant error bubble for a failed processFile; removed when user retries that file. */
  ocrErrorForFileId?: string
  isTyping?: boolean
  typingFullContent?: string
  reconActionsPending?: boolean
  reconActions?: {
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
  }[]
  reconRedirect?: {
    gl_display?: string | null
    reason_zh: string
    reason_en: string
  }
  redirectTasks?: {
    task_id: string
    title?: string
    mode?: string
    reason?: string
    fields?: string[]
  }[]
  recon_chat?: boolean
  contentType?: string
  /** Client incremental batch table (`ocr-batch-*`); cleared when server ocr_snapshot is saved. */
  ocrUploadBatchId?: string
}

export type ChatTask = {
  id: string
  title: string
  createdAt: string
  status: TaskStatus
  processingMode: ProcessingMode
  messages: Message[]
  fileQueue: QueuedFile[]
  fileCount: number
  pageCount: number
  hasSpreadsheet: boolean
  spreadsheetData?: SpreadsheetRow[]
  bankBatchIds?: string[]
  ledgerBatchIds?: string[]
  dupWarning?: string
  titleGenerated?: boolean
}

export type ReconPools = {
  sourceAll: ReconTransactionItem[]
  bankAll: ReconTransactionItem[]
  sourcePending: ReconTransactionItem[]
  bankPending: ReconTransactionItem[]
  selectedSource: ReconTransactionItem[]
  selectedBank: ReconTransactionItem[]
}

export type DuplicateAlert = {
  id: string
  level: 1 | 2 | 3 | 4
  taskId: string
  message: string
  txnIds: { msgId: string; txnIndex: number; idNumber: string }[]
  resolved?: 'continue' | 'cancel'
}
