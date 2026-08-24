import { useState, useRef, useEffect, useLayoutEffect, useCallback, useMemo, type DragEvent } from 'react'
import { flushSync } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import '../../App.css'
import {
  api,
  taskApi,
  BG_JOB_STORAGE_PREFIX,
  apiFetch,
  type BackgroundJobRecord,
  type ServerTaskMessage,
} from '../../services/api'
import {
  trackTabBackgroundJob,
  untrackTabBackgroundJob,
} from '../../services/tabBackgroundJobRegistry'
import { reconciliationApi, type GlJournalLinePayload, type GlJournalPayload } from '../../services/reconciliation'
import { useAuth } from '../../contexts/AuthContext'
import { EditableSpreadsheet, type SpreadsheetRow } from '../../components/EditableSpreadsheet'
import { BankStatementReview, type BankTransaction } from '../../components/BankStatementReview'
import { ARAPReview, type ARAPTransaction } from '../../components/ARAPReview'
import { MODE_META, DROPDOWN_MODES, type ProcessingMode } from '../../components/ModeSelector'
import { ReconciliationTable, type MatchedGroupRow } from '../../components/ReconciliationTable'
import { type ReconTransactionItem } from '../../components/ReconContainer'
import { Settings } from '../../components/Settings'
import { OnboardingWizard } from '../../components/OnboardingWizard'
import { WorkspaceWelcome } from '../../components/WorkspaceWelcome'
import { ReportSetupCard } from '../../components/ReportSetupCard'
import {
  computeReportData,
  buildReportFromGlJournalsOnly,
  type FinancialReportData,
} from '../../hooks/useReportData'
import { mergeGlJournalsForReport } from '../../utils/mergeGlJournalsForReport'
import { ReportGlDraftPickCard } from '../../components/ReportGlDraftPickCard'
import type { ReconState, ChartOfAccountItem } from '../../types/reconciliation'
import { OtherTable } from '../../components/OtherTable'
import { LeftAgentSidebar, type SidebarTask } from '../../components/LeftAgentSidebar'

import { RightPanel } from '../../components/RightPanel'
import { FilePreviewModal, useTaskFilePreview } from '../../components/filePreview'
import type { ExtractionSummaryTask, OcrDestinationPayload } from '../../components/ExtractionSummaryPanel'
import { buildTaskMD } from '../../components/MDRecordViewer'
import { DashboardPage } from '../../components/DashboardPage'
import { MobileBottomNav } from '../../components/MobileBottomNav'
import { useViewport } from '../../hooks/useViewport'
import { CompanyPickerModal } from '../../components/CompanyPickerModal'
import { BookcometLogo } from '../../components/BookcometLogo'
import { useLeftSidebarCollapse, LEFT_SIDEBAR_COLLAPSED_PX } from '../../hooks/useLeftSidebarCollapse'
import { useCompanyOnboardingWizard } from '../../hooks/useCompanyOnboardingWizard'
import { useBankChatTargetTaskId } from '../../hooks/useBankChatTargetTaskId'
import { useStageProgressionReminders } from '../../hooks/useStageProgressionReminders'
import { useResizeDrag } from '../../hooks/useResizeDrag'
import { useDebouncedOcrSnapshotSave } from '../../hooks/useDebouncedOcrSnapshotSave'
import { WorkspaceModuleGridPanel } from './WorkspaceModuleGridPanel'
import { useReconWorkspaceState } from './useReconWorkspaceState'

import {
  RECON_GROUP_COL_HEADER,
  RECON_MATCHED_SHEET_COLUMNS,
  matchedGroupsToSpreadsheetRows,
  mergeReconMatchedSheetRows,
  filterPreservedMatchedRowsCoveredByGroups,
  isReconNonGroupMatchedRow,
  filterSubsumedLedgerPendingGroups,
  mergeReconGroupsFromApiAndLocal,
  normalizeReconTxnIdList,
} from '../../utils/reconMatchedSpreadsheet'
import { mapApiReconciliationGroupsToMatched } from '../../utils/reconGroupsFromApi'
import {
  buildGlPostedBankLockKeys,
  buildGlPostedLedgerLockKeys,
  isBankRowGlPosted,
  isLedgerRowGlPosted,
} from '../../utils/glPostedOcrLock'
import { applyTablePatchesToRows } from '../../utils/applyTablePatches'
import { coaNameByCodeMap, coaOptionLabel } from '../../utils/coaDisplay'
import type { OtherRow } from '../../types/other'
import type {
  ChatTask,
  Message,
  QueuedFile,
  TaskStatus,
  ReconPools,
  DuplicateAlert,
} from './types'
import {
  applyArapMoveMessages,
  validateRowsMovable,
  arapRowIdentity,
} from './arapTableMove'
import {
  inferHomogeneousArapMode,
  inferCanonicalHomogeneousArapFromMessages,
  processingModeReconciledWithArapSnapshot,
} from './arapModeInference'
import {
  buildSpreadsheetRowsFromOcrResult,
  formatConfidenceDisplay,
  spreadsheetRowsToArapTransactions,
} from './buildSpreadsheetFromOcrResult'
import { csvSampleForMode, parseArapCsvToOcrResult } from './parseArapCsv'
import { mapServerTaskMessagesToClient, hydrateMessagesWithReconIdMap } from './messageMappers'
import { mergeTaskMessagesFromServer } from './mergeTaskMessagesFromServer'
import {
  removeLocalBatchOcrSnapshotMessages,
  resolveArapTransactionsForMessage,
  upsertLocalBatchOcrSnapshotInMessages,
} from './batchOcrSnapshot'
import {
  workspaceTasksCacheKey,
  serverTaskToFrontend,
  normalizeClientProcessingMode,
} from './taskMappers'
import { makeChatRecordId } from './makeChatRecordId'
import { buildCreateTaskBody } from './taskPayload'
import { patchTaskMetadataFireAndForget } from './taskPatch'
import { stringifyChatTasksForLocalCache, ghostChatTasksFromActiveOcrBgJobs, hydrateChatTasksFromCache } from './taskLocalCache'
import {
  extractFullOcrText,
  isOcrSummaryMessage,
  looksLikeHtmlTable,
} from './ocrMessageHelpers'
import {
  RECON_SHEET_LEGACY_EXTRA,
  MAX_CONCURRENT_TASKS,
  MAX_CONCURRENT_OCR_FILES,
  MAX_GLOBAL_CONCURRENT_OCR_FILES,
  MAX_ARAP_MESSAGES_PREFETCH_CONCURRENT,
  AI_CHAT_THINKING_PLACEHOLDER,
  seedMessages,
  reconSeedMessages,
} from './constants'
import {
  AP_COMPOSER_LS_PREFIX,
  formatApComposerNotice,
  hasFullApComposerOptions,
  isApVlmReceiptSignal,
  isApVlmTablePreset,
  type ApVlmReceiptSignal,
  type ApVlmTablePreset,
} from './apComposerOptions'
import { ComposerWorkspaceHub } from './ComposerWorkspaceHub'
import { ApModalReceiptPickList, ApModalTablePickList } from './ApComposerModalPickLists'
import { playCompletionSound } from './playCompletionSound'
import { TypewriterText } from '../chat/TypewriterText'
import { AiChatThinkingIndicator } from '../chat/AiChatThinkingIndicator'

/** In-memory composer draft bucket when no sidebar task is selected (welcome / new chat). */
const COMPOSER_DRAFT_NULL_KEY = '__composer_draft_null__'

/**
 * If the task list API reports queued/processing but we already marked completed locally,
 * keep completed when either the local file queue is fully terminal or it was stripped empty
 * (`slimChatTasksForLocalCache`) so we do not trust stale server status over a known-good local flag.
 */
function mergePreferLocalCompletedWhenQueueDone(
  existing: ChatTask | undefined,
  serverStatus: TaskStatus,
): TaskStatus {
  if (!existing || existing.status !== 'completed') return serverStatus
  if (serverStatus !== 'processing' && serverStatus !== 'queued') return serverStatus
  const fq = existing.fileQueue
  /* slimChatTasksForLocalCache strips fileQueue to [] — without this branch, list() can
     overwrite a cached `completed` with stale server `processing` and the sidebar spinner never stops. */
  if (!fq.length) return ('completed' as TaskStatus)
  const allTerminal = fq.every(
    f => f.status === 'completed' || f.status === 'failed' || f.status === 'cancelled',
  )
  return allTerminal ? ('completed' as TaskStatus) : serverStatus
}

/**
 * True when BANK task metadata still says queued/processing but local state shows uploads
 * finished (no pending/processing queued files), no running progress bubbles, and the task
 * already has bank-review surface (`hasSpreadsheet` or bank messages — works when API strips `fileQueue`).
 */
function bankStaleStatusShouldBeCompleted(t: ChatTask): boolean {
  if (t.processingMode !== 'BANK') return false
  if (t.status !== 'processing' && t.status !== 'queued') return false
  const fq = t.fileQueue
  if (fq.some(f => f.status === 'pending' || f.status === 'processing')) return false
  const fqOk =
    fq.length === 0 ||
    fq.every(f => f.status === 'completed' || f.status === 'failed' || f.status === 'cancelled')
  if (!fqOk) return false
  const hasBankSurface =
    Boolean(t.hasSpreadsheet) ||
    (t.messages ?? []).some(
      m =>
        (m.bankTransactions?.length ?? 0) > 0 ||
        m.isCashTable === true,
    )
  if (!hasBankSurface) return false
  const hasLiveProgress = (t.messages ?? []).some(
    m => typeof m.progressPercent === 'number' && m.progressPercent < 100,
  )
  return !hasLiveProgress
}

/** Concatenate BANK table messages for one OCR snapshot (multi-file tasks). */
function mergeBankMessagesForOcrSnapshot(bankMsgs: Message[]): {
  spreadsheetData: SpreadsheetRow[]
  bankTransactions: BankTransaction[]
  bankFilename?: string
  fileRefs: { id: string; name: string }[]
} {
  const ordered = bankMsgs.filter(m => m.bankTransactions && m.bankTransactions.length > 0)
  const spreadsheetData: SpreadsheetRow[] = []
  const bankTransactions: BankTransaction[] = []
  const refMap = new Map<string, { id: string; name: string }>()
  for (const m of ordered) {
    if (m.spreadsheetData?.length) spreadsheetData.push(...m.spreadsheetData)
    bankTransactions.push(...(m.bankTransactions as BankTransaction[]))
    const refs = (m as Message & { fileRefs?: { id: string; name: string }[] }).fileRefs
    if (refs) for (const r of refs) if (r?.id) refMap.set(r.id, r)
  }
  const bankFilename = ordered.length === 1 ? ordered[0].bankFilename : undefined
  return {
    spreadsheetData,
    bankTransactions,
    bankFilename,
    fileRefs: [...refMap.values()],
  }
}

function mergedBankOcrSnapshotContent(merged: { bankTransactions: BankTransaction[]; fileRefs: { id: string; name: string }[] }, singleFileFallback: string): string {
  if (merged.fileRefs.length <= 1 && merged.bankTransactions.length > 0) return singleFileFallback
  return `Updated: processed ${merged.bankTransactions.length} record(s) (${merged.fileRefs.length} file(s))\n\nEditable summary table — double-click a cell to edit:`
}

/** Per-file AR/AP completion: same row materialization as finalizeTask — progress bubble used to carry this until it was removed. */
function arapAttachmentsFromOcrCompletion(args: {
  queuedFileId: string
  fileName: string
  result: any
  processingMode: string
  ocrBackgroundJobId?: string | null
  apVlmTablePreset?: ApVlmTablePreset | null
}):
  | Pick<Message, 'spreadsheetData' | 'arapTransactions' | 'arapFilename' | 'apVlmTablePreset'>
  | undefined {
  const pm = String(args.processingMode || 'AR').toUpperCase()
  if (pm === 'BANK' || pm === 'RECON') return undefined
  const { spreadsheetData } = buildSpreadsheetRowsFromOcrResult({
    fileId: args.queuedFileId,
    fileName: args.fileName,
    result: args.result,
    processingMode: args.processingMode,
    rowIndexStart: 1,
    ocrBackgroundJobId: args.ocrBackgroundJobId ?? undefined,
  })
  if (spreadsheetData.length === 0) return undefined
  const presetSlice =
    pm === 'AP' ? { apVlmTablePreset: (args.apVlmTablePreset ?? 'default') as ApVlmTablePreset } : {}
  return {
    spreadsheetData,
    arapTransactions: spreadsheetRowsToArapTransactions(spreadsheetData, args.processingMode),
    arapFilename: args.fileName,
    ...presetSlice,
  }
}

/** Same upload gesture shares `uploadBatchId`; multi-file batches use one growing `ocr-batch-*` snapshot. */
function arapUploadBatchPeerCount(task: ChatTask, queuedFileId: string): number {
  const f = task.fileQueue.find(x => x.id === queuedFileId)
  if (!f) return 1
  const key = f.uploadBatchId ?? f.id
  return task.fileQueue.filter(x => (x.uploadBatchId ?? x.id) === key).length
}

function totalOcrProcessingNonBankTasks(taskList: ChatTask[]): number {
  return taskList
    .filter(t => t.processingMode !== 'BANK')
    .reduce((s, t) => s + t.fileQueue.filter(f => f.status === 'processing').length, 0)
}

/** Throttled uploads: when to show one-time modal (OCR cap and/or task slot cap). */
function ocrScanOverloadInfo(
  existingFileQueue: QueuedFile[],
  newFileCount: number,
  willQueueTask: boolean,
  totalOcrGlobalProcessing: number,
): { open: boolean; ocr: boolean; task: boolean } {
  const processing = existingFileQueue.filter(f => f.status === 'processing').length
  const localFree = Math.max(0, MAX_CONCURRENT_OCR_FILES - processing)
  const globalFree = Math.max(0, MAX_GLOBAL_CONCURRENT_OCR_FILES - totalOcrGlobalProcessing)
  const availableOcr = Math.min(localFree, globalFree)
  const ocr = newFileCount > availableOcr
  return { open: ocr || willQueueTask, ocr, task: willQueueTask }
}

function ocrFileStatusEn(status: QueuedFile['status']): string {
  switch (status) {
    case 'pending': return 'Pending'
    case 'processing': return 'Scanning'
    case 'completed': return 'Complete'
    case 'failed': return 'Failed'
    case 'cancelled': return 'Cancelled'
    default: return String(status)
  }
}

export default function WorkspaceApp() {
  const navigate = useNavigate()

  // ─── Auth context ──────────────────────────────────────────────────────────
  const { user, accessToken, companies, activeCompany, needsCompanyPick, switchCompany, refreshCompanies, logout } = useAuth()

  /** Workspace switch only; task list + mode reset run in useLayoutEffect( activeCompany ). */
  const handleSwitchCompany = (companyId: string) => {
    switchCompany(companyId)
  }

  const handleCreateWorkspace = useCallback(
    async (name: string) => {
      const row = await api.createCompany(name)
      await refreshCompanies()
      switchCompany(row.id)
    },
    [refreshCompanies, switchCompany],
  )

  // ─── Core state ───────────────────────────────────────────────────────────
  const [tasks, setTasks] = useState<ChatTask[]>([])
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)
  const tasksRef = useRef(tasks)
  tasksRef.current = tasks
  const activeTaskIdRef = useRef<string | null>(null)
  activeTaskIdRef.current = activeTaskId
  /** Tracks workspace switches to reset mode/task only when changing company (not first paint). */
  const lastWorkspaceIdRef = useRef<string | null>(null)

  /** Keep `activeTaskIdRef` in sync immediately — React state from `setActiveTaskId` is async, and
   * `setMessages` must target the current task in the same tick as new-task creation (e.g. await ensureTaskSaved). */
  const assignActiveTaskId = useCallback((id: string | null) => {
    activeTaskIdRef.current = id
    setActiveTaskId(id)
  }, [])

  // ─── Derived from active task ─────────────────────────────────────────────
  const activeTask = tasks.find(t => t.id === activeTaskId) ?? null

  /** AP: queue has work still running through OCR/VLM (allows typed chat while both chip axes are set). */
  const apActiveTaskOcrBusy = useMemo(() => {
    const q = activeTask?.fileQueue
    if (!q?.length) return false
    return q.some(f => f.status === 'pending' || f.status === 'processing')
  }, [activeTask])

  // ─── Shims that update the ACTIVE task (used by non-processFile code) ─────
  /** When `taskIdOverride` is set (e.g. Deploy Codes), patch that task even if user switched chats during await. */
  const setMessages = (
    updater: Message[] | ((prev: Message[]) => Message[]),
    taskIdOverride?: string | null,
  ) => {
    setTasks(prev => {
      const taskId = taskIdOverride !== undefined && taskIdOverride !== null
        ? taskIdOverride
        : activeTaskIdRef.current
      if (!taskId) return prev
      return prev.map(t => {
        if (t.id !== taskId) return t
        const newMsgs = typeof updater === 'function' ? updater(t.messages) : updater
        return { ...t, messages: newMsgs }
      })
    })
  }

  // ─── Task updater helper ──────────────────────────────────────────────────
  const updateTask = useCallback((taskId: string, updater: (t: ChatTask) => ChatTask) => {
    setTasks(prev => prev.map(t => t.id === taskId ? updater(t) : t))
  }, [])

  // ─── Deploy Codes in-progress tracking ───────────────────────────────────
  const [deployingTaskIds, setDeployingTaskIds] = useState<Set<string>>(new Set())
  const [aiThinkingTaskIds, setAiThinkingTaskIds] = useState<Set<string>>(new Set())
  const [reconAiChatThinking, setReconAiChatThinking] = useState(false)

  // ─── Pending duplicate file upload (awaiting user confirm/cancel in chat) ─
  const pendingDupUploadRef = useRef<{
    confirmId: string
    files: File[]
    source: 'new' | 'attach'
    taskId?: string
  } | null>(null)

  // ─── Other UI state ───────────────────────────────────────────────────────
  const [input, setInput] = useState('')
  /** Shared with ComposerWorkspaceHub so chip/input/actions mousedown is not treated as "outside" the hub. */
  const composerHubDismissBoundsRef = useRef<HTMLDivElement>(null)
  const composerTrimRef = useRef('')
  composerTrimRef.current = input.trim()

  const composerDraftByTaskRef = useRef<Record<string, string>>({})
  /** Last `activeTaskId` handled by composer draft restore (`undefined` = not initialized). */
  const composerDraftPrevActiveRef = useRef<string | null | undefined>(undefined)
  const composerEnsureTaskPromiseRef = useRef<Promise<string> | null>(null)

  const [sidebarWidth, setSidebarWidth] = useState(320)
  const [isSidebarResizing, setIsSidebarResizing] = useState(false)
  const {
    leftPanelWidth,
    setLeftPanelWidth,
    leftSidebarCollapsed,
    toggleLeftSidebarCollapsed,
  } = useLeftSidebarCollapse()
  const [rightPanelWidth, setRightPanelWidth] = useState(340)
  const [isLeftPanelResizing, setIsLeftPanelResizing] = useState(false)
  const [isRightPanelResizing, setIsRightPanelResizing] = useState(false)
  const [editingRecordId, setEditingRecordId] = useState<string | null>(null)
  const [openMenuId, setOpenMenuId] = useState<string | null>(null)
  const [apReceiptSignal, setApReceiptSignal] = useState<ApVlmReceiptSignal | null>(null)
  const [apTablePreset, setApTablePreset] = useState<ApVlmTablePreset | null>(null)
  const apReceiptSignalRef = useRef<ApVlmReceiptSignal | null>(null)
  const apTablePresetRef = useRef<ApVlmTablePreset | null>(null)
  apReceiptSignalRef.current = apReceiptSignal
  apTablePresetRef.current = apTablePreset
  /** Dismissible modal after enqueue when uploads are throttled (OCR and/or task caps). */
  const [ocrOverloadModal, setOcrOverloadModal] = useState<{ ocr: boolean; task: boolean } | null>(null)
  /** Blocking dialog for nullable AP composer rules (English strings). */
  const [apComposerDialog, setApComposerDialog] = useState<null | 'incomplete_upload' | 'typing_blocked'>(null)
  /** AP upload+chat follow-up keyed by `${taskId}::${uploadBatchKey}`. */
  const pendingApFollowUpChatByBatchKeyRef = useRef<Map<string, { slashSnapshot: string; text: string }>>(new Map())
  const apTypingBlockedPendingTextRef = useRef<string>('')

  const [processingMode, setProcessingMode] = useState<ProcessingMode>('AR')
  const processingModeRef = useRef<ProcessingMode>('AR')
  processingModeRef.current = processingMode as ProcessingMode
  const { bankChatTargetTaskId, setBankChatTargetTaskId } = useBankChatTargetTaskId(processingMode)

  /** When switching sidebar tasks, restore composer text from per-task draft memory. */
  useLayoutEffect(() => {
    if (composerDraftPrevActiveRef.current === undefined) {
      composerDraftPrevActiveRef.current = activeTaskId
      return
    }
    if (composerDraftPrevActiveRef.current === activeTaskId) return
    const nextKey = activeTaskId ?? COMPOSER_DRAFT_NULL_KEY
    setInput(composerDraftByTaskRef.current[nextKey] ?? '')
    composerDraftPrevActiveRef.current = activeTaskId
  }, [activeTaskId])

  /** Persist visible composer text for the active task (or welcome bucket). */
  useEffect(() => {
    const key = activeTaskId ?? COMPOSER_DRAFT_NULL_KEY
    if (
      processingMode === 'AP' &&
      apComposerDialog === 'typing_blocked' &&
      activeTaskId &&
      !input.trim()
    ) {
      const pend = apTypingBlockedPendingTextRef.current.trim()
      if (pend) {
        composerDraftByTaskRef.current[key] = pend
        return
      }
    }
    composerDraftByTaskRef.current[key] = input
  }, [input, activeTaskId, processingMode, apComposerDialog])

  useEffect(() => {
    if (processingMode !== 'AP' || !activeTaskId) return
    try {
      const raw = localStorage.getItem(AP_COMPOSER_LS_PREFIX + activeTaskId)
      if (!raw) {
        setApReceiptSignal(null)
        setApTablePreset(null)
        return
      }
      const j = JSON.parse(raw) as { receipt?: unknown; table?: unknown }
      const receipt: ApVlmReceiptSignal | null =
        j.receipt === null || j.receipt === undefined ? null :
        isApVlmReceiptSignal(j.receipt) ? j.receipt : null
      const table: ApVlmTablePreset | null =
        j.table === null || j.table === undefined ? null :
        isApVlmTablePreset(j.table) ? j.table : null
      setApReceiptSignal(receipt)
      setApTablePreset(table)
    } catch {
      setApReceiptSignal(null)
      setApTablePreset(null)
    }
  }, [processingMode, activeTaskId])

  useEffect(() => {
    if (processingMode !== 'AP' || !activeTaskId) return
    try {
      localStorage.setItem(
        AP_COMPOSER_LS_PREFIX + activeTaskId,
        JSON.stringify({ receipt: apReceiptSignal, table: apTablePreset }),
      )
    } catch {
      /* ignore quota */
    }
  }, [processingMode, activeTaskId, apReceiptSignal, apTablePreset])

  /** AP: both options set + non-empty composer is illegal unless OCR is busy — catch hub option changes & OCR idle transitions. */
  useEffect(() => {
    if (processingMode !== 'AP') return
    if (apComposerDialog === 'incomplete_upload') return
    if (!hasFullApComposerOptions(apReceiptSignal, apTablePreset)) return
    const trimmed = input.trim()
    if (!trimmed) return
    if (apActiveTaskOcrBusy) return
    if (apComposerDialog === 'typing_blocked') return
    setApComposerDialog('typing_blocked')
    apTypingBlockedPendingTextRef.current = trimmed
    setInput('')
  }, [
    processingMode,
    apReceiptSignal,
    apTablePreset,
    input,
    apActiveTaskOcrBusy,
    apComposerDialog,
  ])

  // Tracks the active task id before entering RECON, so we can restore it on exit
  const preReconActiveTaskIdRef = useRef<string | null>(null)
  // Persistent task used to store RECON match results in the sidebar / DB
  const reconTaskIdRef = useRef<string | null>(null)
  // Set to true while a backend reset is in flight; prevents startReconMode from re-fetching stale data
  const reconJustResetRef = useRef<boolean>(false)
  // IDs of tasks deleted this session — prevents the server taskApi.list() merge from re-adding them
  const deletedTaskIdsRef = useRef<Set<string>>(new Set())
  /** Tasks created locally before `taskApi.list()` returns them — only these may be merged as "unsynced"; avoids resurrecting soft-deleted tasks from stale localStorage. */
  const pendingLocalTaskIdsRef = useRef<Set<string>>(new Set())
  /** Set after each successful `taskApi.list()` for this workspace; BANK chat picker only shows ids in this set ∪ pendingLocal (null = no successful list yet — do not filter). */
  const lastSuccessfulServerTaskIdsRef = useRef<Set<string> | null>(null)
  /** Task ids that returned 404 from getMessages — skip repeat sync until taskApi.list() confirms the id again. */
  const taskMessageSync404Ref = useRef<Set<string>>(new Set())
  // Tracks the active task id and mode before entering REPORT mode
  const preReportActiveTaskIdRef = useRef<string | null>(null)
  const preReportModeRef = useRef<ProcessingMode>('AR')
  const [showSettings, setShowSettings] = useState(false)
  const [openSettingsToMemory, setOpenSettingsToMemory] = useState(false)
  const [showDashboard, setShowDashboard] = useState(false)
  const { showWizard, setShowWizard } = useCompanyOnboardingWizard(!!user, activeCompany?.id)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [workspaceOpen, setWorkspaceOpen] = useState(false)
  /** Legacy chat vs flat module grid (AR/AP only). */
  const [moduleGridView, setModuleGridView] = useState(false)
  /** Pick target assistant message for AR/AP row move (same task). */
  const [arapMoveTargetModal, setArapMoveTargetModal] = useState<
    null | { sourceMessageId: string; rows: ARAPTransaction[] }
  >(null)
  /** Undo bar after successful cross-table move (3s auto-dismiss). */
  const [arapMoveUndo, setArapMoveUndo] = useState<
    null | {
      snapshot: Message[]
      processingMode: ChatTask['processingMode']
      taskId: string
      messageIds: string[]
    }
  >(null)
  const arapUndoTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const { isMobile, isTablet, isDesktop } = useViewport()
  // Full CoA list (all accounts, for report aggregation)
  const [coaList, setCoaList] = useState<ChartOfAccountItem[]>([])
  const {
    reconSelectedSourceTxnIds,
    setReconSelectedSourceTxnIds,
    reconSelectedBankTxnIds,
    setReconSelectedBankTxnIds,
    reconMatchedRows,
    setReconMatchedRows,
    reconMatchedColumns,
    setReconMatchedColumns,
    reconUnmatchedRows,
    setReconUnmatchedRows,
    reconUnmatchedTxns,
    setReconUnmatchedTxns,
    reconMatchedGroups,
    setReconMatchedGroups,
    reconMatchedGroupsRef,
    reconPartialTxns,
    setReconPartialTxns,
    glJournalRefetchSignal,
    setGlJournalRefetchSignal,
    glApplyPatchSeeds,
    setGlApplyPatchSeeds,
    glStatusByGroupId,
    setGlStatusByGroupId,
    glVoucherNoByGroupId,
    setGlVoucherNoByGroupId,
    glVoucherNoByGroupIdRef,
    glJournalMetaByGroupId,
    setGlJournalMetaByGroupId,
    reconScrollTargetGroupId,
    setReconScrollTargetGroupId,
    reconScrollPendingGlDisplay,
    setReconScrollPendingGlDisplay,
    reconAiAccountCodeConfirm,
    setReconAiAccountCodeConfirm,
  } = useReconWorkspaceState()
  const {
    lastOcrUploadTimeRef,
    ocrStageReminderSentRef,
    lastReconActivityTimeRef,
    reconStageReminderSentRef,
  } = useStageProgressionReminders()

  const glPostedBankLockKeys = useMemo(
    () => buildGlPostedBankLockKeys(reconMatchedGroups, glStatusByGroupId),
    [reconMatchedGroups, glStatusByGroupId],
  )
  const glPostedLedgerLockKeys = useMemo(
    () => buildGlPostedLedgerLockKeys(reconMatchedGroups, glStatusByGroupId),
    [reconMatchedGroups, glStatusByGroupId],
  )

  const matchedGroupIdsKey = useMemo(
    () => reconMatchedGroups.map(g => g.id).sort().join(','),
    [reconMatchedGroups],
  )

  useEffect(() => {
    if (!matchedGroupIdsKey) return
    let cancelled = false
    const ids = reconMatchedGroupsRef.current.map(g => g.id)
    void (async () => {
      const snaps: Record<string, string> = {}
      const vouchers: Record<string, string> = {}
      const metas: Record<
        string,
        { journal_id: string; voucher_no: string; status: string; lines: GlJournalLinePayload[] }
      > = {}
      await Promise.all(
        ids.map(async gid => {
          try {
            let journal = (await reconciliationApi.glGetByGroup(gid)).journal
            if (!journal) {
              try {
                journal = await reconciliationApi.glEnsureDraft(gid)
              } catch {
                return
              }
            }
            if (!cancelled && journal) {
              if (journal.status) snaps[gid] = journal.status
              const vn = (journal.voucher_no || '').trim()
              if (vn) vouchers[gid] = vn
              metas[gid] = {
                journal_id: journal.id,
                voucher_no: vn,
                status: String(journal.status ?? ''),
                lines: Array.isArray(journal.lines) ? journal.lines : [],
              }
            }
          } catch {
            /* network / no journal */
          }
        }),
      )
      if (!cancelled && Object.keys(snaps).length > 0) {
        setGlStatusByGroupId(prev => {
          const next = { ...prev }
          for (const [k, v] of Object.entries(snaps)) {
            next[k] = v
          }
          return next
        })
      }
      if (!cancelled && Object.keys(vouchers).length > 0) {
        setGlVoucherNoByGroupId(prev => {
          const next = { ...prev }
          for (const [k, v] of Object.entries(vouchers)) {
            next[k] = v
          }
          return next
        })
      }
      if (!cancelled && Object.keys(metas).length > 0) {
        setGlJournalMetaByGroupId(prev => {
          const next = { ...prev, ...metas }
          return next
        })
      }
    })()
    return () => {
      cancelled = true
    }
  }, [matchedGroupIdsKey])

  // Messages displayed in the RECON chat panel (independent of any task)
  const [reconMessages, setReconMessages] = useState<Message[]>([...reconSeedMessages])

  const messages = activeTask?.messages ?? seedMessages

  const fileQueue = activeTask?.fileQueue ?? []
  const isProcessing = activeTask?.status === 'processing'

  const previewTaskFiles = useMemo(
    () =>
      fileQueue.map(f => ({
        taskFileId: f.taskFileId ?? f.id,
        originalFilename: f.file.name,
      })),
    [fileQueue],
  )

  const filePreview = useTaskFilePreview(activeTaskId, activeCompany?.id, previewTaskFiles)

  const previewQueuedFile = useCallback(
    (file: QueuedFile) => {
      const id = file.taskFileId ?? file.id
      if (file.file instanceof File) {
        filePreview.registerLocalFile(id, file.file)
      }
      void filePreview.openPreview(id)
    },
    [filePreview],
  )

  useEffect(() => {
    for (const f of fileQueue) {
      if (f.taskFileId && f.file instanceof File) {
        filePreview.registerLocalFile(f.taskFileId, f.file)
      }
    }
  }, [fileQueue, filePreview.registerLocalFile])

  const [reconStatusText, setReconStatusText] = useState('')
  const [reconState, setReconState] = useState<ReconState>({})
  const reconStateRef = useRef<ReconState>({})
  useEffect(() => {
    reconStateRef.current = reconState
  }, [reconState])
  // Matched UIDs persist across RECON re-entries (not cleared when switching modes)
  const [reconMatchedSourceUids, setReconMatchedSourceUids] = useState<string[]>([])
  const [reconMatchedBankUids, setReconMatchedBankUids] = useState<string[]>([])
  // Persistent map of DB txn ID → RECON group ID. Saved to localStorage so
  // matched_id values survive browser refreshes.
  const [reconMatchedIdMap, setReconMatchedIdMap] = useState<Record<string, string>>({})
  const reconMatchedIdMapRef = useRef<Record<string, string>>({})
  // Keep the ref in sync with state so async message-load callbacks can read it
  useEffect(() => { reconMatchedIdMapRef.current = reconMatchedIdMap }, [reconMatchedIdMap])
  useEffect(() => { glVoucherNoByGroupIdRef.current = glVoucherNoByGroupId }, [glVoucherNoByGroupId])

  useEffect(() => {
    if (processingMode !== 'RECON' || !reconScrollPendingGlDisplay) return
    const raw = reconScrollPendingGlDisplay.trim()
    if (!raw) {
      setReconScrollPendingGlDisplay(null)
      return
    }
    const norm = (s: string) => s.toLowerCase().replace(/\s+/g, '')
    const target = norm(raw)
    for (const [g, v] of Object.entries(glVoucherNoByGroupId)) {
      if (v && norm(v) === target) {
        setReconScrollTargetGroupId(g)
        setReconScrollPendingGlDisplay(null)
        return
      }
    }
    const m = raw.match(/GL-?\s*0*(\d+)/i)
    if (m) {
      const num = m[1].replace(/^0+/, '') || '0'
      for (const [g, v] of Object.entries(glVoucherNoByGroupId)) {
        const vm = (v || '').match(/GL-?\s*0*(\d+)/i)
        if (vm && (vm[1].replace(/^0+/, '') || '0') === num) {
          setReconScrollTargetGroupId(g)
          setReconScrollPendingGlDisplay(null)
          return
        }
      }
    }
  }, [processingMode, reconScrollPendingGlDisplay, glVoucherNoByGroupId])
  useEffect(() => {
    glPostedBankLockKeysRef.current = glPostedBankLockKeys
  }, [glPostedBankLockKeys])
  useEffect(() => {
    glPostedLedgerLockKeysRef.current = glPostedLedgerLockKeys
  }, [glPostedLedgerLockKeys])
  useEffect(() => {
    const allowed = new Set(reconMatchedGroups.map(g => g.id))
    setGlStatusByGroupId(prev => {
      let touched = false
      const next = { ...prev }
      for (const k of Object.keys(next)) {
        if (!allowed.has(k)) {
          delete next[k]
          touched = true
        }
      }
      return touched ? next : prev
    })
    setGlVoucherNoByGroupId(prev => {
      let touched = false
      const next = { ...prev }
      for (const k of Object.keys(next)) {
        if (!allowed.has(k)) {
          delete next[k]
          touched = true
        }
      }
      return touched ? next : prev
    })
  }, [reconMatchedGroups])
  const [reconMatchResult, setReconMatchResult] = useState<{ matchedCount: number; timestamp: number } | null>(null)
  const [duplicateAlerts, setDuplicateAlerts] = useState<DuplicateAlert[]>([])
  // Delete-task confirmation modal state
  const [deleteConfirm, setDeleteConfirm] = useState<{
    taskId: string
    taskTitle: string
    hasReconState: boolean  // true → show extra RECON-data-loss warning
  } | null>(null)
  // Task completion notifications
  type TaskNotification = { id: string; title: string }
  const [taskNotifications, setTaskNotifications] = useState<TaskNotification[]>([])
  const prevTaskStatusesRef2 = useRef<Map<string, string>>(new Map())
  const [categoryOptionsByMode, setCategoryOptionsByMode] = useState<{
    AR: string[]
    AP: string[]
    BANK: string[]
  }>({
    AR: [],
    AP: [],
    BANK: [],
  })

  const fileInputRef = useRef<HTMLInputElement>(null)
  const attachFileInputRef = useRef<HTMLInputElement>(null)
  const previewUrlsRef = useRef<Set<string>>(new Set())
  const messageListRef = useRef<HTMLDivElement>(null)
  const suppressScrollRef = useRef(false)
  const previousModeRef = useRef<ProcessingMode>(processingMode)
  const showFlatModuleGrid = processingMode === 'AR' || processingMode === 'AP'
  useEffect(() => {
    if (!showFlatModuleGrid) setModuleGridView(false)
  }, [showFlatModuleGrid])
  /** When true, next OCR→OCR mode change keeps activeTaskId (welcome "new task" path). */
  const skipNextOcrModeDeselectRef = useRef(false)
  const activeProcessingTasksRef = useRef<Set<string>>(new Set())
  /** Bank upload job_ids started in this tab — skip remote mirror for same job. */
  const localBankUploadJobIdsRef = useRef<Set<string>>(new Set())
  /** Background OCR / AI chat job_ids owned by this tab — skip remote mirror. */
  const localBackgroundJobIdsRef = useRef<Set<string>>(new Set())
  const pollingBackgroundJobIdsRef = useRef<Set<string>>(new Set())
  const syncWorkspaceTaskFromServerRef = useRef<((taskId: string, companyId?: string | null) => Promise<void>) | null>(null)
  const prevWorkspaceActivityKeysRef = useRef<Map<string, string>>(new Map())
  const submittedFileIdsRef = useRef<Set<string>>(new Set())

  const handleRetryOcrFile = useCallback(
    (fileId: string) => {
      if (processingMode !== 'AR' && processingMode !== 'AP') return
      const taskId = activeTaskIdRef.current
      if (!taskId) return
      const task = tasksRef.current.find(t => t.id === taskId)
      const qf = task?.fileQueue.find(f => f.id === fileId)
      if (!qf || qf.status !== 'failed') return
      if (!qf.file || !(qf.file instanceof File)) {
        updateTask(taskId, t => ({
          ...t,
          messages: [
            ...t.messages,
            { id: `no-retry-file-${Date.now()}`, role: 'assistant' as const, content: 'Cannot retry, please upload again.' },
          ],
        }))
        return
      }
      submittedFileIdsRef.current.delete(fileId)
      const fileName = qf.file.name
      updateTask(taskId, t => ({
        ...t,
        status: 'processing' as TaskStatus,
        fileQueue: t.fileQueue.map(f =>
          f.id === fileId
            ? {
                ...f,
                status: 'pending' as const,
                addedToSpreadsheet: undefined,
                result: undefined,
                ocrRetryCount: (f.ocrRetryCount ?? 0) + 1,
              }
            : f
        ),
        messages: [
          ...t.messages.filter(m => m.ocrErrorForFileId !== fileId),
          { id: `retry-started-${Date.now()}`, role: 'assistant' as const, content: `Retry started for: ${fileName}` },
        ],
      }))
    },
    [processingMode, updateTask],
  )

  const markUploadCancelled = useCallback((taskId: string, fileId: string | undefined, progressMessageId?: string) => {
    if (progressMessageId) activeProcessingTasksRef.current.delete(progressMessageId)
    if (fileId) submittedFileIdsRef.current.delete(fileId)
    updateTask(taskId, t => {
      const countUploadRows = (msgs: Message[]) =>
        msgs.reduce((n, m) => n + (m.uploadedFiles?.length ?? 0), 0)
      const beforeRows = countUploadRows(t.messages)
      const nextQueue = fileId
        ? t.fileQueue.map(f => f.id === fileId ? { ...f, status: 'cancelled' as const } : f)
        : t.fileQueue
      const terminal = nextQueue.every(f => f.status === 'completed' || f.status === 'failed' || f.status === 'cancelled')
      const hasCompleted = nextQueue.some(f => f.status === 'completed')
      const nextStatus: TaskStatus = terminal ? (hasCompleted ? 'completed' : 'failed') : t.status
      const nextMessages = t.messages.map(m =>
        m.id === progressMessageId
          ? {
              ...m,
              progressPercent: 100,
              progressLabel: 'Cancelled',
              content: m.content.replace(/\(\d+%\)/, '(100%)'),
              progressJob: undefined,
            }
          : m.uploadedFiles && fileId
            ? { ...m, uploadedFiles: m.uploadedFiles.map(f => f.id === fileId ? { ...f, status: 'cancelled' as const } : f) }
            : m
      )
      const afterRows = countUploadRows(nextMessages)
      return {
        ...t,
        status: nextStatus,
        fileQueue: nextQueue,
        messages: nextMessages,
      }
    })
  }, [updateTask])

  const handleCancelUploadJob = useCallback(async (message: Message) => {
    const job = message.progressJob
    if (!job) return
    markUploadCancelled(job.taskId, job.fileId, message.id)
    try {
      if (job.kind === 'bank') {
        await api.cancelBankStatementUploadJob(job.jobId)
      } else {
        await api.cancelBackgroundJob(job.jobId)
      }
    } catch (err) {
      console.warn('[Upload] Cancel request failed:', err)
    }
  }, [markUploadCancelled])

  const finalizingTasksRef = useRef<Set<string>>(new Set())
  const ocrAccountCodeDebounceRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({})
  const ocrLedgerDocTypeDebounceRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({})
  /** Last synced account_code per task|message|bank|ledger for debounced OCR→DB persist */
  const ocrLastPersistedAccountCodesRef = useRef<Record<string, Record<string, string>>>({})
  /** Last synced AR/AP (transaction_type) per ledger_txn_id for debounced OCR→DB doc_type persist */
  const ocrLastPersistedLedgerDocTypesRef = useRef<Record<string, Record<string, string>>>({})
  const refreshReconUnmatchedFnRef = useRef<((override?: ReconState) => void) | null>(null)
  /** Avoid duplicate getMessages prefetch per task when reconciling AR/AP folder after refresh. */
  const arapMessagesPrefetchStartedRef = useRef<Set<string>>(new Set())
  /** Coalesce overlapping lazy-hydrate + prefetch into one GET per task until it settles. */
  const taskMessagesInflightRef = useRef<Map<string, Promise<ServerTaskMessage[]>>>(new Map())
  const getTaskMessagesDeduped = useCallback((taskId: string) => {
    const existing = taskMessagesInflightRef.current.get(taskId)
    if (existing) return existing
    const p = taskApi.getMessages(taskId, activeCompany?.id).finally(() => {
      taskMessagesInflightRef.current.delete(taskId)
    })
    taskMessagesInflightRef.current.set(taskId, p)
    return p
  }, [activeCompany?.id])
  const glPostedBankLockKeysRef = useRef<ReadonlySet<string>>(new Set<string>())
  const glPostedLedgerLockKeysRef = useRef<ReadonlySet<string>>(new Set<string>())

  const isBankMode = (mode: ProcessingMode) => mode === 'BANK' || mode === 'RECON'
  const acceptsCsvUpload = (mode: ProcessingMode) =>
    mode === 'BANK' || mode === 'RECON' || mode === 'AP' || mode === 'AR'
  const isCsvFileName = (name: string) => name.toLowerCase().endsWith('.csv')
  const uploadNeedsApComposer = (files: File[]) => files.some(f => !isCsvFileName(f.name))
  const multiCsvGuardMessage = (files: File[]): string | null => {
    const csvCount = files.filter(f => isCsvFileName(f.name)).length
    if (csvCount > 1) {
      return 'Upload only one CSV at a time. You can still attach multiple PDFs with that CSV.'
    }
    return null
  }

  const getCategoryOptionsForMode = (mode: string): string[] => {
    if (mode === 'BANK') return categoryOptionsByMode.BANK
    if (mode === 'AP') return categoryOptionsByMode.AP
    if (mode === 'RECON') {
      // Full company CoA for AP/AR Account and Bank GL dropdowns.
      if (coaList.length > 0) {
        return coaList.map(a => coaOptionLabel(a)).filter(Boolean)
      }
      return Array.from(new Set([...categoryOptionsByMode.AR, ...categoryOptionsByMode.AP, ...categoryOptionsByMode.BANK]))
    }
    return categoryOptionsByMode.AR
  }

  const getCategoryOptionsForRows = (rows: SpreadsheetRow[] | undefined): string[] => {
    if (!rows || rows.length === 0) return getCategoryOptionsForMode(processingMode)
    const sample = rows[0]
    const isBankLike =
      '存入' in sample || '提取' in sample || '原幣結餘' in sample ||
      '分類' in sample || 'categorise' in sample
    if (isBankLike) return categoryOptionsByMode.BANK
    return Array.from(new Set([...categoryOptionsByMode.AR, ...categoryOptionsByMode.AP]))
  }

  const getFieldValue = (fields: Record<string, any>, keys: string[]) => {
    for (const key of keys) {
      const value = fields?.[key]
      if (value !== undefined && value !== null && String(value).trim() !== '') return value
    }
    return ''
  }

  /** After DB / GL sync: push account codes into task messages, RECON chat, unmatched rows, and group snapshots. */
  const applyAccountCategorySyncFromDb = useCallback(
    (bank: Record<string, string>, ledger: Record<string, string>) => {
      if (!Object.keys(bank).length && !Object.keys(ledger).length) return

      const patchSnap = (snap: any, tid: string, kind: 'bank' | 'ledger') => {
        const id = (tid || String(snap?.db_id || snap?.bank_txn_id || snap?.ledger_txn_id || '')).trim()
        if (!id) return snap
        const code = kind === 'bank' ? bank[id] : ledger[id]
        if (!code) return snap
        return { ...snap, account_code: code, categorise: code, category: code }
      }

      setReconMatchedGroups(prev =>
        prev.map(g => {
          const bankSnaps = g.bank_txn_snapshots?.length
            ? g.bank_txn_snapshots.map((s, i) => patchSnap(s, g.bank_txn_ids[i] || '', 'bank'))
            : g.bank_txn_snapshots
          const ledgerSnaps = g.ledger_txn_snapshots?.length
            ? g.ledger_txn_snapshots.map((s, i) => patchSnap(s, g.ledger_txn_ids[i] || '', 'ledger'))
            : g.ledger_txn_snapshots
          return { ...g, bank_txn_snapshots: bankSnaps, ledger_txn_snapshots: ledgerSnaps }
        }),
      )

      setReconUnmatchedRows(prev => ({
        bank: prev.bank.map(row => {
          const r = row as any
          const tid = String(r.bank_txn_id || r.db_id || '').trim()
          const c = tid ? bank[tid] : ''
          return c ? { ...row, account_code: c } : row
        }),
        ledger: prev.ledger.map(row => {
          const r = row as any
          const tid = String(r.ledger_txn_id || r.db_id || '').trim()
          const c = tid ? ledger[tid] : ''
          return c ? { ...row, account_code: c } : row
        }),
      }))

      setReconUnmatchedTxns(prev => ({
        bank: (prev.bank ?? []).map((t: any) => {
          const tid = String(t.id || t.bank_txn_id || t.db_id || '').trim()
          const c = tid ? bank[tid] : ''
          return c ? { ...t, account_code: c, account_category: c } : t
        }),
        ledger: (prev.ledger ?? []).map((t: any) => {
          const tid = String(t.id || t.ledger_txn_id || t.db_id || '').trim()
          const c = tid ? ledger[tid] : ''
          return c ? { ...t, account_code: c, account_category: c } : t
        }),
      }))

      setTasks(prev =>
        prev.map(task => ({
          ...task,
          messages: task.messages.map(msg => {
            let bankTransactions = msg.bankTransactions
            if (bankTransactions?.length) {
              bankTransactions = bankTransactions.map(t => {
                const tid = String((t as any).db_id || (t as any).bank_txn_id || '').trim()
                const c = tid ? bank[tid] : ''
                return c ? { ...t, account_code: c, categorise: c } : t
              })
            }
            let arapTransactions = msg.arapTransactions
            if (arapTransactions?.length) {
              arapTransactions = arapTransactions.map(t => {
                const tid = String((t as any).db_id || (t as any).ledger_txn_id || '').trim()
                const c = tid ? ledger[tid] : ''
                return c ? { ...t, account_code: c, category: c } : t
              })
            }
            let spreadsheetData = msg.spreadsheetData
            if (spreadsheetData?.length) {
              spreadsheetData = spreadsheetData.map((cell: any) => {
                const bt = String(cell.bank_txn_id || cell.db_id || '').trim()
                const lt = String(cell.ledger_txn_id || '').trim()
                const c = (bt && bank[bt]) || (lt && ledger[lt]) || ''
                return c ? { ...cell, account_code: c } : cell
              })
            }
            if (
              bankTransactions !== msg.bankTransactions ||
              arapTransactions !== msg.arapTransactions ||
              spreadsheetData !== msg.spreadsheetData
            ) {
              return { ...msg, bankTransactions, arapTransactions, spreadsheetData }
            }
            return msg
          }),
        })),
      )

      setReconMessages(prev =>
        prev.map(msg => {
          let bankTransactions = msg.bankTransactions
          if (bankTransactions?.length) {
            bankTransactions = bankTransactions.map(t => {
              const tid = String((t as any).db_id || (t as any).bank_txn_id || '').trim()
              const c = tid ? bank[tid] : ''
              return c ? { ...t, account_code: c, categorise: c } : t
            })
          }
          let arapTransactions = msg.arapTransactions
          if (arapTransactions?.length) {
            arapTransactions = arapTransactions.map(t => {
              const tid = String((t as any).db_id || (t as any).ledger_txn_id || '').trim()
              const c = tid ? ledger[tid] : ''
              return c ? { ...t, account_code: c, category: c } : t
            })
          }
          let spreadsheetData = msg.spreadsheetData
          if (spreadsheetData?.length) {
            spreadsheetData = spreadsheetData.map((cell: any) => {
              const bt = String(cell.bank_txn_id || cell.db_id || '').trim()
              const lt = String(cell.ledger_txn_id || '').trim()
              const c = (bt && bank[bt]) || (lt && ledger[lt]) || ''
              return c ? { ...cell, account_code: c } : cell
            })
          }
          if (
            bankTransactions !== msg.bankTransactions ||
            arapTransactions !== msg.arapTransactions ||
            spreadsheetData !== msg.spreadsheetData
          ) {
            return { ...msg, bankTransactions, arapTransactions, spreadsheetData }
          }
          return msg
        }),
      )
    },
    [],
  )

  const handlePrimaryJournalStatusByGroup = useCallback((snap: Record<string, string>) => {
    setGlStatusByGroupId(prev => {
      let changed = false
      const next = { ...prev }
      for (const [gid, st] of Object.entries(snap)) {
        if (next[gid] !== st) {
          next[gid] = st
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [])

  const handleGlVoucherNoByGroup = useCallback((partial: Record<string, string>) => {
    setGlVoucherNoByGroupId(prev => {
      let changed = false
      const next = { ...prev }
      for (const [gid, vn] of Object.entries(partial)) {
        const v = (vn || '').trim()
        if (v && next[gid] !== v) {
          next[gid] = v
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [])

  const flushOcrAccountCodePersist = useCallback(
    async (taskId: string, messageId: string, kind: 'bank' | 'ledger') => {
      const snapKey = `${taskId}|${messageId}|${kind}`
      const task = tasksRef.current.find(t => t.id === taskId)
      const msg = task?.messages.find(m => m.id === messageId)
      const rows = kind === 'bank' ? (msg?.bankTransactions ?? []) : (msg?.arapTransactions ?? [])
      const source: 'bank' | 'ledger' = kind === 'bank' ? 'bank' : 'ledger'
      const prevSent = ocrLastPersistedAccountCodesRef.current[snapKey] ?? {}
      const prunedNext: Record<string, string> = {}
      const postedB = glPostedBankLockKeysRef.current
      const postedL = glPostedLedgerLockKeysRef.current
      const updates: Array<{ source: 'bank' | 'ledger'; txn_id: string; account_category: string }> = []
      for (const t of rows) {
        const tid =
          kind === 'bank'
            ? String((t as any).db_id || (t as any).bank_txn_id || '').trim()
            : String((t as any).db_id || (t as any).ledger_txn_id || '').trim()
        if (!tid) continue
        const code = String((t as any).account_code ?? '').trim()
        prunedNext[tid] = code
        const was = prevSent[tid] ?? ''
        if (code === was) continue
        if (kind === 'bank' && isBankRowGlPosted(t, postedB)) continue
        if (kind === 'ledger' && isLedgerRowGlPosted(t, postedL)) continue
        updates.push({ source, txn_id: tid, account_category: code })
      }
      if (!updates.length) {
        ocrLastPersistedAccountCodesRef.current[snapKey] = { ...prunedNext }
        return
      }
      try {
        const res = await reconciliationApi.bulkTxnAccountCategory({
          updates,
          rebuild_draft_journals: false,
        })
        ocrLastPersistedAccountCodesRef.current[snapKey] = { ...prunedNext }
        const b: Record<string, string> = {}
        const l: Record<string, string> = {}
        for (const u of updates) {
          if (u.source === 'bank') b[u.txn_id] = u.account_category
          else l[u.txn_id] = u.account_category
        }
        applyAccountCategorySyncFromDb(b, l)
        if (res.rebuilt_group_ids?.length) {
          setGlJournalRefetchSignal({ nonce: Date.now(), groupIds: res.rebuilt_group_ids })
        }
      } catch (e) {
        console.warn('[OCR account_code] persist failed', e)
      }
    },
    [applyAccountCategorySyncFromDb],
  )

  const flushOcrLedgerDocTypePersist = useCallback(
    async (taskId: string, messageId: string) => {
      const snapKey = `${taskId}|${messageId}|ledger-doctype`
      const task = tasksRef.current.find(t => t.id === taskId)
      const msg = task?.messages.find(m => m.id === messageId)
      const rows = msg?.arapTransactions ?? []
      const prevSent = ocrLastPersistedLedgerDocTypesRef.current[snapKey] ?? {}
      const postedL = glPostedLedgerLockKeysRef.current
      const prunedNext: Record<string, string> = {}
      const updates: Array<{ txn_id: string; doc_type: string }> = []
      for (const t of rows) {
        const tid = String((t as any).db_id || (t as any).ledger_txn_id || '').trim()
        if (!tid) continue
        const raw = String((t as any).transaction_type ?? '').trim().toUpperCase()
        const docType = raw === 'AP' ? 'AP' : raw === 'AR' ? 'AR' : ''
        if (!docType) continue
        prunedNext[tid] = docType
        const was = prevSent[tid] ?? ''
        if (docType === was) continue
        if (isLedgerRowGlPosted(t, postedL)) continue
        updates.push({ txn_id: tid, doc_type: docType })
      }
      if (!updates.length) {
        ocrLastPersistedLedgerDocTypesRef.current[snapKey] = { ...prunedNext }
        return
      }
      try {
        const res = await reconciliationApi.bulkLedgerDocType({
          updates,
          rebuild_draft_journals: false,
        })
        ocrLastPersistedLedgerDocTypesRef.current[snapKey] = { ...prunedNext }
        if (res.rebuilt_group_ids?.length) {
          setGlJournalRefetchSignal({ nonce: Date.now(), groupIds: res.rebuilt_group_ids })
        }
        refreshReconUnmatchedFnRef.current?.()
      } catch (e) {
        console.warn('[OCR ledger doc_type] persist failed', e)
      }
    },
    [],
  )

  const scheduleOcrAccountCodePersist = useCallback(
    (taskId: string | null, messageId: string, kind: 'bank' | 'ledger') => {
      if (!taskId) return
      const key = `${taskId}|${messageId}|${kind}`
      const existing = ocrAccountCodeDebounceRef.current[key]
      if (existing) clearTimeout(existing)
      ocrAccountCodeDebounceRef.current[key] = setTimeout(() => {
        delete ocrAccountCodeDebounceRef.current[key]
        void flushOcrAccountCodePersist(taskId, messageId, kind)
      }, 500)
    },
    [flushOcrAccountCodePersist],
  )

  const scheduleOcrLedgerDocTypePersist = useCallback(
    (taskId: string | null, messageId: string) => {
      if (!taskId) return
      const key = `${taskId}|${messageId}|ledger-doctype`
      const existing = ocrLedgerDocTypeDebounceRef.current[key]
      if (existing) clearTimeout(existing)
      ocrLedgerDocTypeDebounceRef.current[key] = setTimeout(() => {
        delete ocrLedgerDocTypeDebounceRef.current[key]
        void flushOcrLedgerDocTypePersist(taskId, messageId)
      }, 500)
    },
    [flushOcrLedgerDocTypePersist],
  )

  // Collect all transactions from all tasks + messages (for Settings CoA delete-locked check)
  const allTransactions = tasks.flatMap(task =>
    (task.messages ?? []).flatMap(m => [
      ...(m.arapTransactions ?? []).map(t => ({
        account_code: t.account_code,
        id_number: t.id_number,
        date: t.date,
        amount: t.amount ?? undefined,
        transaction_type: t.transaction_type,
      })),
      ...(m.bankTransactions ?? []).map(t => ({
        account_code: t.account_code,
        id_number: t.id_number,
        date: t.date,
        amount: t.deposit ?? t.withdrawal ?? undefined,
        transaction_type: 'BANK',
      })),
    ])
  )

  // Deploy account codes via AI for a given message
  const handleDeployCodes = async (messageId: string, mode: string) => {
    const msg = messages.find(m => m.id === messageId)
    if (!msg) return
    const isBank = mode === 'BANK'
    const txns = isBank
      ? (msg.bankTransactions ?? []).map(t => ({
          id_number: t.id_number,
          date: t.date,
          amount: t.deposit ?? t.withdrawal ?? null,
          payer: '',
          payee: t.particulars ?? '',
          memo: t.particulars ?? '',
          transaction_type: 'BANK',
          category: '',
        }))
      : (msg.arapTransactions ?? []).map(t => ({
          id_number: t.id_number,
          date: t.date,
          amount: t.amount ?? null,
          payer: t.payer ?? '',
          payee: t.payee ?? '',
          memo: t.memo ?? '',
          transaction_type: t.transaction_type ?? mode,
          category: t.category ?? '',
        }))
    if (txns.length === 0) return

    const postedLockedCount = isBank
      ? (msg.bankTransactions ?? []).filter(t => isBankRowGlPosted(t, glPostedBankLockKeys)).length
      : (msg.arapTransactions ?? []).filter(t => isLedgerRowGlPosted(t, glPostedLedgerLockKeys)).length
    if (postedLockedCount > 0) {
      const unlocked = txns.length - postedLockedCount
      if (unlocked <= 0) {
        window.alert(
          `${postedLockedCount} row(s) are posted to the GL and cannot receive Deploy Codes.\n\n` +
            'Unpost the journal in RECON (back to draft), then Deploy Codes again.',
        )
        return
      }
      const proceed = window.confirm(
        `${postedLockedCount} row(s) are posted to the GL and will be skipped.\n` +
          `Deploy Codes will continue for ${unlocked} unlocked row(s).\n\n` +
          'To update locked rows: unpost the journal in RECON (back to draft), then Deploy Codes again.\n\n' +
          'Continue with unlocked rows?',
      )
      if (!proceed) return
    }

    const deployMsgId = `deploy-${Date.now()}`
    const deployTaskId = activeTaskId
    if (!deployTaskId) return

    // Group by transaction_type so AR and AP each get the correct CoA and AI character
    const arTxns = txns.filter(t => t.transaction_type === 'AR')
    const apTxns = txns.filter(t => t.transaction_type === 'AP')
    const bankTxns = txns.filter(t => t.transaction_type === 'BANK')
    const hasMixed = !isBank && arTxns.length > 0 && apTxns.length > 0
    const displayMode = hasMixed ? 'AR+AP' : mode

    setDeployingTaskIds(prev => new Set(prev).add(deployTaskId))

    setMessages(prev => [...prev, {
      id: deployMsgId,
      role: 'assistant',
      content: `Deploy Codes in progress...\nMode: ${displayMode} | Analysing ${txns.length} transaction(s)...`,
    }], deployTaskId)

    try {
      let results: Array<{ id_number: string; suggested_code: string | null; confidence: number }> = []

      if (isBank) {
        // BANK: single call
        const res = await reconciliationApi.deployAccountCodes(bankTxns.length > 0 ? bankTxns : txns, 'BANK')
        results = res.results
      } else if (hasMixed) {
        // Mixed AR+AP table: call each type in parallel with its own CoA + AI character
        const [arRes, apRes] = await Promise.all([
          arTxns.length > 0 ? reconciliationApi.deployAccountCodes(arTxns, 'AR') : Promise.resolve({ results: [] }),
          apTxns.length > 0 ? reconciliationApi.deployAccountCodes(apTxns, 'AP') : Promise.resolve({ results: [] }),
        ])
        results = [...arRes.results, ...apRes.results]
      } else {
        // Single-type table: use the effective mode from actual transaction types
        const effectiveMode = apTxns.length > 0 && arTxns.length === 0 ? 'AP' : mode
        const res = await reconciliationApi.deployAccountCodes(txns, effectiveMode)
        results = res.results
      }

      const codeMap = new Map(results.map(r => [r.id_number ?? '', r.suggested_code ?? '']))
      const nameByCode = coaNameByCodeMap(coaList)

      const persistUpdates: Array<{ source: 'bank' | 'ledger'; txn_id: string; account_category: string }> = []
      if (isBank) {
        for (const t of msg.bankTransactions ?? []) {
          const tid = String((t as any).db_id || (t as any).bank_txn_id || '').trim()
          const code = (codeMap.get(t.id_number ?? '') || '').trim()
          if (tid && code) persistUpdates.push({ source: 'bank', txn_id: tid, account_category: code })
        }
      } else {
        for (const t of msg.arapTransactions ?? []) {
          const tid = String((t as any).db_id || (t as any).ledger_txn_id || '').trim()
          const code = (codeMap.get(t.id_number ?? '') || '').trim()
          if (tid && code) persistUpdates.push({ source: 'ledger', txn_id: tid, account_category: code })
        }
      }
      const persistFiltered = persistUpdates.filter(u =>
        u.source === 'bank' ? !glPostedBankLockKeys.has(u.txn_id) : !glPostedLedgerLockKeys.has(u.txn_id),
      )
      if (persistFiltered.length > 0) {
        try {
          const bulkRes = await reconciliationApi.bulkTxnAccountCategory({
            updates: persistFiltered,
            rebuild_draft_journals: true,
          })
          const b: Record<string, string> = {}
          const l: Record<string, string> = {}
          for (const u of persistFiltered) {
            if (u.source === 'bank') b[u.txn_id] = u.account_category
            else l[u.txn_id] = u.account_category
          }
          applyAccountCategorySyncFromDb(b, l)
          if (bulkRes.rebuilt_group_ids?.length) {
            setGlJournalRefetchSignal({ nonce: Date.now(), groupIds: bulkRes.rebuilt_group_ids })
          }
        } catch (e) {
          console.warn('[DeployCodes] Persist account_category failed:', e)
        }
      }
      if (deployTaskId) {
        if (isBank) {
          const sk = `${deployTaskId}|${messageId}|bank`
          const m: Record<string, string> = { ...ocrLastPersistedAccountCodesRef.current[sk] }
          for (const t of msg.bankTransactions ?? []) {
            const tid = String((t as any).db_id || (t as any).bank_txn_id || '').trim()
            if (!tid) continue
            if (isBankRowGlPosted(t, glPostedBankLockKeys)) {
              m[tid] = String((t as any).account_code ?? '').trim()
            } else {
              m[tid] = String(
                codeMap.get(t.id_number ?? '') ?? (t as any).account_code ?? '',
              ).trim()
            }
          }
          ocrLastPersistedAccountCodesRef.current[sk] = m
        } else {
          const sk = `${deployTaskId}|${messageId}|ledger`
          const m: Record<string, string> = { ...ocrLastPersistedAccountCodesRef.current[sk] }
          for (const t of msg.arapTransactions ?? []) {
            const tid = String((t as any).db_id || (t as any).ledger_txn_id || '').trim()
            if (!tid) continue
            if (isLedgerRowGlPosted(t, glPostedLedgerLockKeys)) {
              m[tid] = String((t as any).account_code ?? '').trim()
            } else {
              m[tid] = String(
                codeMap.get(t.id_number ?? '') ?? (t as any).account_code ?? '',
              ).trim()
            }
          }
          ocrLastPersistedAccountCodesRef.current[sk] = m
        }
      }

      if (isBank) {
        setMessages(prev => {
          const updated = prev.map(m =>
          m.id !== messageId ? m : {
            ...m,
            bankTransactions: (m.bankTransactions ?? []).map(t => {
              const fromMap = codeMap.get(t.id_number ?? '') || ''
              if (isBankRowGlPosted(t, glPostedBankLockKeys)) {
                return { ...t, account_code: t.account_code || '' }
              }
              const code = fromMap || t.account_code || ''
              return {
                ...t,
                account_code: code,
                category: code ? nameByCode.get(code) || t.category || '' : '',
              }
            }),
          }
          )
          // Persist the updated bank transactions to DB so codes survive a restart
          const updatedMsg = updated.find(m => m.id === messageId)
          if (updatedMsg) {
            const merged = mergeBankMessagesForOcrSnapshot([updatedMsg])
            const content = mergedBankOcrSnapshotContent(merged, updatedMsg.content)
            debouncedSaveSnapshot(deployTaskId, messageId, content, {
              spreadsheetData: merged.spreadsheetData,
              bankTransactions: merged.bankTransactions,
              bankFilename: merged.bankFilename,
              fileRefs: merged.fileRefs,
            }, activeCompany?.id)
          }
          return updated
        }, deployTaskId)
      } else {
        setMessages(prev => {
          const updated = prev.map(m =>
          m.id !== messageId ? m : {
            ...m,
            arapTransactions: (m.arapTransactions ?? []).map(t => {
              const fromMap = codeMap.get(t.id_number ?? '') || ''
              if (isLedgerRowGlPosted(t, glPostedLedgerLockKeys)) {
                return { ...t, account_code: t.account_code || '' }
              }
              const code = fromMap || t.account_code || ''
              return {
                ...t,
                account_code: code,
                category: code ? nameByCode.get(code) || t.category || '' : '',
              }
            }),
          }
          )
          // Persist the updated AR/AP transactions to DB so codes survive a restart
          const updatedMsg = updated.find(m => m.id === messageId)
          if (updatedMsg) {
            debouncedSaveSnapshot(deployTaskId, messageId, updatedMsg.content, {
              spreadsheetData: updatedMsg.spreadsheetData,
              arapTransactions: updatedMsg.arapTransactions,
              fileRefs: updatedMsg.fileRefs,
              arapFilename: updatedMsg.arapFilename,
            }, activeCompany?.id)
          }
          return updated
        }, deployTaskId)
      }

      const assigned = results.filter(r => r.suggested_code)
      const unassigned = results.filter(r => !r.suggested_code)
      let content = `Deploy Codes complete\n`
      content += `Mode: ${displayMode} | Transactions: ${results.length} | Assigned: ${assigned.length} | Unassigned: ${unassigned.length}\n`
      if (postedLockedCount > 0) {
        content += `\nSkipped ${postedLockedCount} posted/locked row(s). Unpost in RECON (back to draft) to deploy codes for those rows.\n`
      }
      if (assigned.length > 0) {
        content += `\nAssigned account codes:\n`
        assigned.forEach(r => {
          const conf = r.confidence ? ` (confidence: ${(r.confidence * 100).toFixed(0)}%)` : ''
          content += `- ${r.id_number} → ${r.suggested_code}${conf}\n`
        })
      }
      if (unassigned.length > 0) {
        content += `\nCould not assign:\n`
        unassigned.forEach(r => { content += `- ${r.id_number}\n` })
      }
      setMessages(prev => prev.map(m => m.id === deployMsgId ? { ...m, content } : m), deployTaskId)
    } catch (err) {
      console.error('[DeployCodes] Failed:', err)
      const errMsg = err instanceof Error ? err.message : String(err)
      setMessages(prev => prev.map(m => m.id === deployMsgId ? {
        ...m,
        content: `Deploy Codes failed\nMode: ${displayMode} | Transactions: ${txns.length}\n\nError: ${errMsg}`,
      } : m), deployTaskId)
    } finally {
      if (deployTaskId) setDeployingTaskIds(prev => { const s = new Set(prev); s.delete(deployTaskId); return s })
    }
  }

  // Load chart-of-accounts on mount
  useEffect(() => {
    let cancelled = false
    const mapAccountLabels = (accounts: ChartOfAccountItem[]) =>
      (accounts || []).map(a => coaOptionLabel(a)).filter(Boolean)
    ;(async () => {
      try {
        const [ar, ap, bank, all] = await Promise.all([
          reconciliationApi.getChartOfAccounts('AR'),
          reconciliationApi.getChartOfAccounts('AP'),
          reconciliationApi.getChartOfAccounts('BANK'),
          reconciliationApi.getChartOfAccounts(),
        ])
        if (cancelled) return
        setCategoryOptionsByMode({
          AR: mapAccountLabels(ar.accounts || []),
          AP: mapAccountLabels(ap.accounts || []),
          BANK: mapAccountLabels(bank.accounts || []),
        })
        setCoaList(all.accounts || [])
      } catch (error) {
        console.error('[ChartOfAccounts] Failed to load category options:', error)
      }
    })()
    return () => { cancelled = true }
  }, [])

  // ─── Persistence helpers ──────────────────────────────────────────────────
  const _localCacheKey =
    user && activeCompany?.id ? workspaceTasksCacheKey(user.id, activeCompany.id) : null

  const _writeLocalCache = useCallback((taskList: ChatTask[]) => {
    if (!_localCacheKey) return
    try {
      localStorage.setItem(_localCacheKey, stringifyChatTasksForLocalCache(taskList))
    } catch { /* storage full — ignore */ }
  }, [_localCacheKey])

  const pruneStaleEmptyCachedTask = useCallback((taskId: string): boolean => {
    if (pendingLocalTaskIdsRef.current.has(taskId)) return false
    let pruned = false
    setTasks(prev => {
      const staleTask = prev.find(t => t.id === taskId)
      if (!staleTask || staleTask.messages.length > 0 || staleTask.fileQueue.length > 0) return prev
      pruned = true
      const next = prev.filter(t => t.id !== taskId)
      _writeLocalCache(next)
      return next
    })
    if (pruned && activeTaskIdRef.current === taskId) assignActiveTaskId(null)
    return pruned
  }, [_writeLocalCache, assignActiveTaskId])

  /** Await before jobs so the server row exists for message persistence and workspace re-entry. */
  const persistTaskToServer = useCallback(async (task: ChatTask, companyId?: string | null) => {
    pendingLocalTaskIdsRef.current.add(task.id)
    // Keep pendingLocal on create failure so list merge keeps the row in unsynced and active is not cleared.
    await taskApi.create(buildCreateTaskBody(task), companyId)
    if (lastSuccessfulServerTaskIdsRef.current) {
      lastSuccessfulServerTaskIdsRef.current.add(task.id)
    } else {
      lastSuccessfulServerTaskIdsRef.current = new Set([task.id])
    }
    // Keep task.id in pendingLocalTaskIdsRef until taskApi.list merge observes it server-side.
    // If we drop it here while an older list response is still in flight, merged + unsynced can omit
    // the new row, clear activeTaskId, and strand the UI on WorkspaceWelcome.
    setTasks(prev => [...prev])
  }, [])

  const addPersistedTask = useCallback(async (task: ChatTask, activate: boolean = true): Promise<boolean> => {
    try {
      await persistTaskToServer(task, activeCompany?.id)
      // Insert synchronously so an in-flight taskApi.list merge always sees prevMap[id] during unsynced.
      flushSync(() => {
        setTasks(prev => {
          const next = prev.some(t => t.id === task.id)
            ? prev.map(t => (t.id === task.id ? task : t))
            : [...prev, task]
          _writeLocalCache(next)
          return next
        })
      })
      if (activate) assignActiveTaskId(task.id)
      return true
    } catch (err) {
      console.warn('[Tasks] Failed to persist new task:', err)
      const failedTask: ChatTask = {
        ...task,
        status: 'failed',
        fileQueue: task.fileQueue.map(f =>
          f.status === 'pending' || f.status === 'processing'
            ? { ...f, status: 'failed' as const }
            : f,
        ),
        messages: [
          ...task.messages,
          {
            id: `persist-err-${Date.now()}`,
            role: 'assistant',
            content: (() => {
              const base =
                'Could not save task to the server. Check your connection and try again.'
              if (err instanceof Error && err.message) {
                const extra = err.message.trim().slice(0, 400)
                return extra ? `${base} (${extra})` : base
              }
              return base
            })(),
          },
        ],
      }
      setTasks(prev => {
        const next = prev.some(t => t.id === failedTask.id)
          ? prev.map(t => (t.id === failedTask.id ? failedTask : t))
          : [...prev, failedTask]
        _writeLocalCache(next)
        return next
      })
      if (activate) assignActiveTaskId(failedTask.id)
      return false
    }
  }, [persistTaskToServer, _writeLocalCache, assignActiveTaskId, activeCompany?.id])

  const { debouncedSaveSnapshot, cancelDebouncedOcrSnapshot } = useDebouncedOcrSnapshotSave()

  /** Same payload shape as debounced AR/AP OCR snapshot saves (WorkspaceApp onDataChange). */
  const buildArapOcrSnapshotPayload = useCallback((msg: Message) => {
    return {
      spreadsheetData: msg.spreadsheetData,
      arapTransactions: msg.arapTransactions,
      arapFilename: msg.arapFilename,
      fileRefs: msg.fileRefs,
    }
  }, [])

  const persistArapMessagesPatch = useCallback(
    async (taskId: string, messages: Message[], messageIds: string[], companyId?: string | null) => {
      for (const mid of messageIds) {
        const msg = messages.find((m) => m.id === mid)
        if (!msg) continue
        cancelDebouncedOcrSnapshot(taskId, mid)
        await taskApi.patchMessage(
          taskId,
          mid,
          {
            content_text: msg.content ?? '',
            payload_json: buildArapOcrSnapshotPayload(msg),
          },
          companyId,
        )
      }
    },
    [buildArapOcrSnapshotPayload, cancelDebouncedOcrSnapshot],
  )

  const clearArapMoveUndoTimer = useCallback(() => {
    if (arapUndoTimerRef.current) {
      clearTimeout(arapUndoTimerRef.current)
      arapUndoTimerRef.current = null
    }
  }, [])

  /** Cross-table move for AR/AP OCR tables: optimistic UI, await PATCH both messages, undo toast, rollback on failure. */
  const executeArapCrossTableMove = useCallback(
    async (sourceMessageId: string, targetMessageId: string, movedRows: ARAPTransaction[]) => {
      const taskId = activeTaskIdRef.current
      const companyId = activeCompany?.id
      if (!taskId) return
      if (processingMode !== 'AR' && processingMode !== 'AP') {
        window.alert('Cross-table move is only available in Accounts Receivable or Accounts Payable mode.')
        return
      }
      if (sourceMessageId === targetMessageId) return
      const task = tasksRef.current.find((t) => t.id === taskId)
      if (!task) return
      const srcMsg = task.messages.find((m) => m.id === sourceMessageId)
      const tgtMsg = task.messages.find((m) => m.id === targetMessageId)
      if (!srcMsg?.arapTransactions || !tgtMsg?.arapTransactions) {
        window.alert('Both tables must have AR/AP data to move rows.')
        return
      }
      if (movedRows.length === 0) return

      const rowCheck = validateRowsMovable(movedRows, reconState, glPostedLedgerLockKeys)
      if (rowCheck.ok === false) {
        window.alert(
          rowCheck.reason === 'recon_locked'
            ? 'Cannot move: the selection includes reconciled (matched) rows. Unlock or change them in RECON first.'
            : 'Cannot move: the selection includes GL-posted rows.',
        )
        return
      }

      const taskPm = String(task.processingMode ?? processingMode ?? 'AR')
      const applied = applyArapMoveMessages(task.messages, sourceMessageId, targetMessageId, movedRows, taskPm)
      if (applied.ok === false) {
        if (applied.error === 'id_conflict') {
          window.alert('Cannot move: a non-empty id_number already exists on the target table.')
        }
        return
      }

      const snapshotBefore = {
        messages: structuredClone(task.messages) as Message[],
        processingMode: task.processingMode,
      }

      suppressScrollRef.current = true
      setArapMoveTargetModal(null)

      setTasks((prev) => {
        const next = prev.map((t) =>
          t.id === taskId ? { ...t, messages: applied.nextMessages } : t,
        )
        _writeLocalCache(next)
        return next
      })

      const patchIds = [sourceMessageId, targetMessageId]

      try {
        await persistArapMessagesPatch(taskId, applied.nextMessages, patchIds, companyId)
      } catch (e) {
        console.warn('[ARAP move] Persist failed:', e)
        window.alert('Could not save the move. Your table was reverted to the previous state.')
        setTasks((prev) => {
          const next = prev.map((t) =>
            t.id === taskId
              ? { ...t, messages: snapshotBefore.messages, processingMode: snapshotBefore.processingMode }
              : t,
          )
          _writeLocalCache(next)
          return next
        })
        if (
          activeTaskIdRef.current === taskId &&
          (processingModeRef.current === 'AR' || processingModeRef.current === 'AP')
        ) {
          const pm = snapshotBefore.processingMode
          if (pm === 'AR' || pm === 'AP') setProcessingMode(pm)
        }
        suppressScrollRef.current = false
        return
      }

      const pmBefore = snapshotBefore.processingMode
      const prevCanonical = inferCanonicalHomogeneousArapFromMessages(snapshotBefore.messages)
      const nextInferred = inferCanonicalHomogeneousArapFromMessages(applied.nextMessages)
      if (pmBefore === 'AR' || pmBefore === 'AP') {
        if (nextInferred && nextInferred !== pmBefore) {
          setTasks((prev) => {
            const next = prev.map((t) => (t.id === taskId ? { ...t, processingMode: nextInferred } : t))
            _writeLocalCache(next)
            return next
          })
          patchTaskMetadataFireAndForget(taskId, { processing_mode: nextInferred })
          if (
            activeTaskIdRef.current === taskId &&
            (processingModeRef.current === 'AR' || processingModeRef.current === 'AP')
          ) {
            setProcessingMode(nextInferred)
          }
          const label =
            nextInferred === 'AP'
              ? 'Accounts Payable (AP)'
              : 'Accounts Receivable (AR)'
          window.alert(
            `This chat task was moved to the ${label} folder to match the Type column.\nTo undo, change all rows back to the previous type.`,
          )
        } else if (prevCanonical && !nextInferred) {
          window.alert(
            'The table mixes AR and AP (or has incomplete types). The sidebar folder is unchanged. When every row is the same type, the folder will update automatically.',
          )
        }
      }

      scheduleOcrAccountCodePersist(taskId, sourceMessageId, 'ledger')
      scheduleOcrAccountCodePersist(taskId, targetMessageId, 'ledger')
      scheduleOcrLedgerDocTypePersist(taskId, sourceMessageId)
      scheduleOcrLedgerDocTypePersist(taskId, targetMessageId)

      clearArapMoveUndoTimer()
      setArapMoveUndo({
        snapshot: snapshotBefore.messages,
        processingMode: snapshotBefore.processingMode,
        taskId,
        messageIds: patchIds,
      })
      arapUndoTimerRef.current = setTimeout(() => {
        setArapMoveUndo(null)
        arapUndoTimerRef.current = null
      }, 3000)

      suppressScrollRef.current = false
    },
    [
      processingMode,
      reconState,
      glPostedLedgerLockKeys,
      activeCompany?.id,
      _writeLocalCache,
      persistArapMessagesPatch,
      scheduleOcrAccountCodePersist,
      scheduleOcrLedgerDocTypePersist,
      clearArapMoveUndoTimer,
      setProcessingMode,
    ],
  )

  const handleArapMoveUndo = useCallback(async () => {
    const u = arapMoveUndo
    if (!u) return
    clearArapMoveUndoTimer()
    setArapMoveUndo(null)
    const { taskId, snapshot, processingMode: pmSnap, messageIds } = u
    suppressScrollRef.current = true
    const restored = structuredClone(snapshot) as Message[]
    setTasks((prev) => {
      const next = prev.map((t) =>
        t.id === taskId ? { ...t, messages: restored, processingMode: pmSnap ?? t.processingMode } : t,
      )
      _writeLocalCache(next)
      return next
    })
    if (activeTaskIdRef.current === taskId && (pmSnap === 'AR' || pmSnap === 'AP')) {
      setProcessingMode(pmSnap)
    }
    try {
      await persistArapMessagesPatch(taskId, restored, messageIds, activeCompany?.id)
    } catch (e) {
      console.warn('[ARAP move undo] Persist failed:', e)
      window.alert('Could not save the undo. Refresh the page to sync with the server.')
    }
    suppressScrollRef.current = false
  }, [arapMoveUndo, clearArapMoveUndoTimer, _writeLocalCache, persistArapMessagesPatch, activeCompany?.id, setProcessingMode])

  const handleArapTableDragOver = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }, [])

  const handleArapTableDropForMessage = useCallback(
    (targetMessageId: string) => (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault()
      e.stopPropagation()
      const raw = e.dataTransfer.getData('application/json')
      if (!raw) return
      let parsed: { sourceMessageId?: string; rowIdentities?: string[] }
      try {
        parsed = JSON.parse(raw) as { sourceMessageId?: string; rowIdentities?: string[] }
      } catch {
        return
      }
      if (!parsed.sourceMessageId || !Array.isArray(parsed.rowIdentities)) return
      if (parsed.sourceMessageId === targetMessageId) return
      const taskId = activeTaskIdRef.current
      if (!taskId) return
      const t = tasksRef.current.find((tt) => tt.id === taskId)
      const src = t?.messages.find((m) => m.id === parsed.sourceMessageId)
      const ident = new Set(parsed.rowIdentities)
      const rows = (src?.arapTransactions ?? []).filter((tx) => ident.has(arapRowIdentity(tx)))
      if (rows.length === 0) return
      void executeArapCrossTableMove(parsed.sourceMessageId, targetMessageId, rows)
    },
    [executeArapCrossTableMove],
  )

  useEffect(() => {
    return () => {
      if (arapUndoTimerRef.current) clearTimeout(arapUndoTimerRef.current)
    }
  }, [])

  const handleRetryOcrFailedPage = useCallback(
    async (messageId: string, jobId: string, page: number) => {
      const taskId = activeTaskIdRef.current
      const companyId = activeCompany?.id
      if (!taskId || !companyId) return
      const task = tasksRef.current.find((t) => t.id === taskId)
      if (!task) return
      const target = task.fileQueue.find((f) => f.ocrJobId === jobId)
      if (!target) {
        window.alert('Could not find this OCR job on the file queue.')
        return
      }
      try {
        const st = await api.retryOcrJobPage(jobId, page, companyId)
        const newResult = st.result_json
        if (!newResult || typeof newResult !== 'object') {
          window.alert('Retry completed but returned no result.')
          return
        }
        const batchKey = target.uploadBatchId ?? target.id
        updateTask(taskId, (t) => ({
          ...t,
          fileQueue: t.fileQueue.map((f) =>
            f.ocrJobId === jobId ? { ...f, result: newResult } : f,
          ),
        }))
        const batchFiles = task.fileQueue.filter((f) => {
          const k = f.uploadBatchId ?? f.id
          return k === batchKey && f.status === 'completed'
        })
        let rowIndex = 1
        const allRows: SpreadsheetRow[] = []
        for (const f of batchFiles) {
          const res = f.ocrJobId === jobId ? newResult : f.result
          if (!res) continue
          const chunk = buildSpreadsheetRowsFromOcrResult({
            fileId: f.id,
            fileName: f.file.name,
            result: res,
            processingMode: (f.processingMode || task.processingMode || 'AR') as string,
            rowIndexStart: rowIndex,
            ocrBackgroundJobId: f.ocrJobId ?? null,
          })
          allRows.push(...chunk.spreadsheetData)
          rowIndex = chunk.nextRowIndex
        }
        const arapTxns = spreadsheetRowsToArapTransactions(allRows, task.processingMode)
        setMessages((prev) =>
          prev.map((m) =>
            m.id === messageId ? { ...m, arapTransactions: arapTxns, spreadsheetData: allRows } : m,
          ),
        )
        updateTask(taskId, (t) => ({ ...t, spreadsheetData: allRows }))
        const msg = task.messages.find((m) => m.id === messageId)
        debouncedSaveSnapshot(taskId, messageId, msg?.content ?? '', {
          spreadsheetData: allRows,
          arapTransactions: arapTxns,
          arapFilename: msg?.arapFilename,
          fileRefs: msg?.fileRefs,
        }, companyId)
      } catch (e) {
        console.error('[OcrRetryPage]', e)
        window.alert(e instanceof Error ? e.message : String(e))
      }
    },
    [activeCompany?.id, updateTask, debouncedSaveSnapshot],
  )

  // Per-workspace: hydrate from cache before paint, then merge taskApi.list() (uses X-Company-ID).
  useLayoutEffect(() => {
    if (!user?.id || !activeCompany?.id) return
    const key = workspaceTasksCacheKey(user.id, activeCompany.id)
    let cancelled = false

    const prevWs = lastWorkspaceIdRef.current
    const switchedWorkspace = prevWs !== null && prevWs !== activeCompany.id
    lastWorkspaceIdRef.current = activeCompany.id
    if (switchedWorkspace) {
      assignActiveTaskId(null)
      setProcessingMode('AR')
      arapMessagesPrefetchStartedRef.current.clear()
      taskMessagesInflightRef.current.clear()
      deletedTaskIdsRef.current.clear()
      taskMessageSync404Ref.current.clear()
      // Keep pendingLocalTaskIdsRef across switches so a mid-upload task re-merges when user returns
      lastSuccessfulServerTaskIdsRef.current = null
    }

    for (let i = 0; i < localStorage.length; i++) {
      const bgKey = localStorage.key(i)
      if (!bgKey?.startsWith(BG_JOB_STORAGE_PREFIX)) continue
      try {
        const raw = localStorage.getItem(bgKey)
        if (!raw) continue
        const meta = JSON.parse(raw) as Record<string, unknown>
        const taskId = typeof meta.taskId === 'string' ? meta.taskId : undefined
        const companyId = typeof meta.companyId === 'string' ? meta.companyId : undefined
        if (taskId && companyId === activeCompany.id) pendingLocalTaskIdsRef.current.add(taskId)
      } catch { /* ignore stale bg metadata */ }
    }

    try {
      const cached = localStorage.getItem(key)
      if (cached) {
        const parsed = JSON.parse(cached) as ChatTask[]
        setTasks(Array.isArray(parsed)
          ? hydrateChatTasksFromCache(parsed).map(t => ({ ...t, processingMode: normalizeClientProcessingMode(t.processingMode) }))
          : [])
      } else {
        setTasks([])
      }
    } catch {
      setTasks([])
    }

    taskApi
      .list()
      .then(serverTasks => {
        if (cancelled) return
        lastSuccessfulServerTaskIdsRef.current = new Set(serverTasks.map(s => s.id))
        for (const st of serverTasks) taskMessageSync404Ref.current.delete(st.id)
        setTasks(prev => {
          const prevMap = new Map(prev.map(t => [t.id, t]))
          const deletedIds = deletedTaskIdsRef.current
          const pendingLocal = pendingLocalTaskIdsRef.current
          for (const st of serverTasks) pendingLocal.delete(st.id)
          const merged = serverTasks
            .filter(st => !deletedIds.has(st.id))
            .map(st => {
              const existing = prevMap.get(st.id)
              const base = serverTaskToFrontend(st)
              return {
                ...base,
                status: mergePreferLocalCompletedWhenQueueDone(existing, base.status),
                messages: existing?.messages?.length ? existing.messages : base.messages,
                fileQueue: existing?.fileQueue?.length ? existing.fileQueue : base.fileQueue,
                spreadsheetData: existing?.spreadsheetData ?? base.spreadsheetData,
              }
            })
          const serverIds = new Set(serverTasks.map(s => s.id))
          /** Keep sidebar-selected row merged in while list is stale or pending bookkeeping slips (see persist failures). */
          const stickyActiveId = activeTaskIdRef.current
          const unsynced = prev.filter(
            t =>
              !serverIds.has(t.id) &&
              !deletedIds.has(t.id) &&
              (pendingLocal.has(t.id) || t.id === stickyActiveId),
          )
          const presentIds = new Set<string>()
          for (const t of merged) presentIds.add(t.id)
          for (const t of unsynced) presentIds.add(t.id)
          const ghostTasks =
            activeCompany?.id ?
              ghostChatTasksFromActiveOcrBgJobs(activeCompany.id, presentIds)
            : []
          const final = hydrateChatTasksFromCache([...merged, ...unsynced, ...ghostTasks]).map(t => ({
            ...t,
            processingMode: normalizeClientProcessingMode(t.processingMode),
          }))
          const activeId = activeTaskIdRef.current
          const ghostActive = Boolean(activeId && !final.some(t => t.id === activeId))
          if (
            activeId &&
            !final.some(t => t.id === activeId) &&
            !pendingLocal.has(activeId)
          ) {
            assignActiveTaskId(null)
          }
          try {
            localStorage.setItem(key, stringifyChatTasksForLocalCache(final))
          } catch { /* storage full */ }
          return final
        })
      })
      .catch(err => console.warn('[Tasks] Failed to load from API, using cache:', err))

    return () => {
      cancelled = true
    }
  }, [user?.id, activeCompany?.id])

  // Clear task cache when user logs out
  useEffect(() => {
    if (!user) {
      lastWorkspaceIdRef.current = null
      arapMessagesPrefetchStartedRef.current.clear()
      taskMessagesInflightRef.current.clear()
      deletedTaskIdsRef.current.clear()
      pendingLocalTaskIdsRef.current.clear()
      taskMessageSync404Ref.current.clear()
      lastSuccessfulServerTaskIdsRef.current = null
      const keysToRemove: string[] = []
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i)
        if (k?.startsWith('tasks_v1_') || k?.startsWith('task_msg_v1_')) keysToRemove.push(k)
      }
      keysToRemove.forEach(k => localStorage.removeItem(k))
    }
  }, [user?.id])

  // Matched Results sheet rows are derived from reconMatchedGroups; preserve auto/AI rows (no group).
  // Declared before localStorage restore so init runs after this and overwrites the empty first sync.
  useEffect(() => {
    setReconMatchedRows(prev => {
      const preserved = prev.filter(isReconNonGroupMatchedRow)
      const preservedFiltered = filterPreservedMatchedRowsCoveredByGroups(preserved, reconMatchedGroups)
      const fromGroups = matchedGroupsToSpreadsheetRows(reconMatchedGroups, glVoucherNoByGroupId)
      const merged = mergeReconMatchedSheetRows(fromGroups, preservedFiltered)
      return merged
    })
    setReconMatchedColumns(prev => {
      const baseSheetCols = [
        ...RECON_MATCHED_SHEET_COLUMNS,
        ...RECON_SHEET_LEGACY_EXTRA.filter(
          k => !RECON_MATCHED_SHEET_COLUMNS.includes(k as (typeof RECON_MATCHED_SHEET_COLUMNS)[number]),
        ),
      ]
      const rest = (prev || []).filter(c => !baseSheetCols.includes(c as (typeof baseSheetCols)[number]))
      return [...baseSheetCols, ...rest]
    })
  }, [reconMatchedGroups, glVoucherNoByGroupId])

  // On app init (or when user changes): restore full RECON workspace state from localStorage
  // so match results, groups, and matched_id columns are all available immediately on any mode.
  useEffect(() => {
    if (!user) return
    try {
      const cached = localStorage.getItem(`recon_v1_${user.id}_default`)
      if (!cached) return
      const saved = JSON.parse(cached)
      if (saved.reconMatchedIdMap && Object.keys(saved.reconMatchedIdMap).length > 0) {
        setReconMatchedIdMap(saved.reconMatchedIdMap)
        reconMatchedIdMapRef.current = saved.reconMatchedIdMap
      }
      const baseSheetCols = [
        ...RECON_MATCHED_SHEET_COLUMNS,
        ...RECON_SHEET_LEGACY_EXTRA.filter(
          k => !RECON_MATCHED_SHEET_COLUMNS.includes(k as (typeof RECON_MATCHED_SHEET_COLUMNS)[number]),
        ),
      ]
      if (saved.reconMatchedGroups?.length) {
        const normGroups = filterSubsumedLedgerPendingGroups(saved.reconMatchedGroups)
        setReconMatchedGroups(normGroups)
        const preserved = (saved.reconMatchedRows || []).filter(isReconNonGroupMatchedRow)
        const preservedFiltered = filterPreservedMatchedRowsCoveredByGroups(preserved, normGroups)
        setReconMatchedRows(
          mergeReconMatchedSheetRows(matchedGroupsToSpreadsheetRows(normGroups, {}), preservedFiltered),
        )
      } else if (saved.reconMatchedRows?.length) {
        setReconMatchedRows(saved.reconMatchedRows)
      }
      if (saved.reconMatchedColumns?.length) {
        const extra = saved.reconMatchedColumns.filter((c: string) => !baseSheetCols.includes(c as (typeof baseSheetCols)[number]))
        setReconMatchedColumns([...baseSheetCols, ...extra])
      } else if (saved.reconMatchedGroups?.length) {
        setReconMatchedColumns(baseSheetCols)
      }
      // Restore locked UID sets so matched chips are hidden in the right panel immediately.
      if (saved.reconMatchedBankUids?.length)   setReconMatchedBankUids(saved.reconMatchedBankUids)
      if (saved.reconMatchedSourceUids?.length) setReconMatchedSourceUids(saved.reconMatchedSourceUids)
      // Restore unmatched display rows so 未配對交易 checkbox tables are visible
      // immediately (localStorage fallback; backend session is authoritative on RECON entry).
      if (saved.reconUnmatchedRows) setReconUnmatchedRows(saved.reconUnmatchedRows)
    } catch { /* corrupt cache, ignore */ }
  }, [user?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Lazy-load messages when a task is opened (Phase 2)
  // Only fetches if the task has no messages in memory yet and was loaded from API
  useEffect(() => {
    if (!activeTaskId || !user) return
    const task = tasksRef.current.find(t => t.id === activeTaskId)
    // Skip if messages already in memory, or if task was just created (has seedMessages)
    if (!task || task.messages.length > 0) return

    let cancelled = false
    void getTaskMessagesDeduped(activeTaskId)
      .then(serverMsgs => {
        if (cancelled) return
        if (serverMsgs.length === 0) {
          // No server messages yet — show seed welcome
          updateTask(activeTaskId, t => ({ ...t, messages: [...seedMessages] }))
          return
        }
        const messages = mapServerTaskMessagesToClient(serverMsgs)
        const tid = activeTaskId
        const taskBeforeMerge = tasksRef.current.find(x => x.id === tid)
        const reconciledPm = taskBeforeMerge
          ? processingModeReconciledWithArapSnapshot(taskBeforeMerge.processingMode, messages)
          : null
        const shouldPatchFolder =
          Boolean(taskBeforeMerge && reconciledPm && reconciledPm !== taskBeforeMerge.processingMode)

        setTasks(prev => {
          const next = prev.map(t => {
            if (t.id !== tid) return t
            let loaded: ChatTask = { ...t, messages }
            // Re-hydrate task-level spreadsheetData so RECON mode can see AR/AP rows
            // even when the task was initially loaded from the server (messages: []).
            if (!loaded.spreadsheetData || loaded.spreadsheetData.length === 0) {
              const ssMsg = messages.find(m => m.spreadsheetData)
              if (ssMsg?.spreadsheetData) loaded.spreadsheetData = ssMsg.spreadsheetData
            }
            // Apply reconMatchedIdMap so BANK/AR/AP tables show matched_id immediately,
            // even if the user hasn't entered RECON mode after a browser refresh.
            const idMap = reconMatchedIdMapRef.current
            if (Object.keys(idMap).length > 0) {
              loaded.messages = loaded.messages.map(msg => {
                let changed = false
                const nextBank = msg.bankTransactions?.map((tr: any) => {
                  const key = tr.bank_txn_id || tr.id_number || ''
                  const gid = idMap[key]
                  if (gid && tr.matched_id !== gid) { changed = true; return { ...tr, matched_id: gid } }
                  return tr
                }) ?? msg.bankTransactions
                const nextArap = msg.arapTransactions?.map((tr: any) => {
                  const key = tr.ledger_txn_id || tr.id_number || ''
                  const baseKey = key.replace(/^(AR|AP)-/, '')
                  const gid = idMap[key] || idMap[baseKey]
                  if (gid && tr.matched_id !== gid) { changed = true; return { ...tr, matched_id: gid } }
                  return tr
                }) ?? msg.arapTransactions
                if (!changed) return msg
                return { ...msg, bankTransactions: nextBank, arapTransactions: nextArap }
              })
            }
            if (shouldPatchFolder && reconciledPm) {
              loaded = { ...loaded, processingMode: reconciledPm }
            }
            return loaded
          })
          _writeLocalCache(next)
          return next
        })
        if (shouldPatchFolder && reconciledPm) {
          patchTaskMetadataFireAndForget(tid, { processing_mode: reconciledPm }, activeCompany?.id)
        }
      })
      .catch(err => {
        if ((err as { status?: number })?.status === 404) {
          pruneStaleEmptyCachedTask(activeTaskId)
          return
        }
        // Fallback to seed messages if load fails
        console.warn('[Tasks] Failed to load messages:', err)
        const t = tasksRef.current.find(x => x.id === activeTaskId)
        if (t && t.messages.length === 0) {
          updateTask(activeTaskId, task => ({ ...task, messages: [...seedMessages] }))
        }
      })

    return () => { cancelled = true }
  }, [activeTaskId, user?.id, activeCompany?.id, pruneStaleEmptyCachedTask, getTaskMessagesDeduped])

  // Prefetch messages for AR/AP tasks with empty messages so folder + RECON chips match ocr_snapshot
  // without requiring the user to open each task (fixes sidebar stuck on stale processing_mode).
  useEffect(() => {
    if (!user?.id) return
    const ids: string[] = []
    for (const task of tasks) {
      if (task.processingMode !== 'AR' && task.processingMode !== 'AP') continue
      if (task.messages.length > 0) continue
      const serverTaskIds = lastSuccessfulServerTaskIdsRef.current
      if (!serverTaskIds?.has(task.id)) continue
      if (arapMessagesPrefetchStartedRef.current.has(task.id)) continue
      arapMessagesPrefetchStartedRef.current.add(task.id)
      ids.push(task.id)
    }
    if (ids.length === 0) return

    let cancelled = false
    const runPrefetch = async () => {
      const K = MAX_ARAP_MESSAGES_PREFETCH_CONCURRENT
      for (let i = 0; i < ids.length; i += K) {
        if (cancelled) return
        const slice = ids.slice(i, i + K)
        await Promise.all(
          slice.map(taskId =>
            getTaskMessagesDeduped(taskId)
              .then(serverMsgs => {
                if (cancelled) return
                if (!serverMsgs.length) return
                if (tasksRef.current.find(t => t.id === taskId)?.messages.length) return
                const loadedMessages = mapServerTaskMessagesToClient(serverMsgs)
                const hydratedMessages = hydrateMessagesWithReconIdMap(loadedMessages, reconMatchedIdMapRef.current)
                const taskRow = tasksRef.current.find(t => t.id === taskId)
                if (!taskRow || taskRow.messages.length > 0) return
                const reconciledPm = processingModeReconciledWithArapSnapshot(taskRow.processingMode, hydratedMessages)
                const needsFolderPatch = reconciledPm !== taskRow.processingMode
                setTasks(prev => {
                  const next = prev.map(t => {
                    if (t.id !== taskId) return t
                    if (t.messages.length > 0) return t
                    let updated: ChatTask = { ...t, messages: hydratedMessages }
                    if (!updated.spreadsheetData || updated.spreadsheetData.length === 0) {
                      const ssMsg = hydratedMessages.find((m: any) => m.spreadsheetData)
                      if ((ssMsg as any)?.spreadsheetData) updated.spreadsheetData = (ssMsg as any).spreadsheetData
                    }
                    if (needsFolderPatch) updated = { ...updated, processingMode: reconciledPm }
                    return updated
                  })
                  _writeLocalCache(next)
                  return next
                })
                if (needsFolderPatch) patchTaskMetadataFireAndForget(taskId, { processing_mode: reconciledPm }, activeCompany?.id)
              })
              .catch((err) => {
                if ((err as { status?: number })?.status === 404) {
                  pruneStaleEmptyCachedTask(taskId)
                } else {
                  arapMessagesPrefetchStartedRef.current.delete(taskId)
                }
              }),
          ),
        )
      }
    }

    void runPrefetch()

    return () => {
      cancelled = true
    }
  }, [user?.id, tasks, _writeLocalCache, pruneStaleEmptyCachedTask, activeCompany?.id, getTaskMessagesDeduped])

  // ─── Message helpers (active-task shims) ──────────────────────────────────
  const addMessageBeforeSpreadsheet = (message: Message) => {
    setMessages((prev) => [...prev, message])
  }

  // ─── Duplicate file confirm/cancel handlers ────────────────────────────────
  const handleDupConfirm = async (confirmId: string) => {
    const pending = pendingDupUploadRef.current
    if (!pending || pending.confirmId !== confirmId) return
    if (processingMode === 'AP') {
      if (!hasFullApComposerOptions(apReceiptSignalRef.current, apTablePresetRef.current)) {
        setApComposerDialog('incomplete_upload')
        return
      }
    }
    const files = pending.files

    pendingDupUploadRef.current = null

    // Replace the confirm card with a "continued" message
    setTasks(prev => prev.map(t => ({
      ...t,
      messages: t.messages.map(m =>
        m.dupConfirmId === confirmId
          ? { ...m, dupConfirmPending: false, dupAlertType: 'warn' as const, content: `[Duplicate file] ${m.dupFileNames || ''}\nContinue upload was confirmed; review duplicate transactions in the table.` }
          : m
      ),
    })))

    // Now proceed with the upload
    if (pending.source === 'new') {
      const uploadBatchId = makeChatRecordId()
      const newQueue: QueuedFile[] = files.map((file, index) => {
        const previewUrl = URL.createObjectURL(file)
        previewUrlsRef.current.add(previewUrl)
        return { id: `f-${Date.now()}-${index}`, file, status: 'pending', previewUrl, processingMode, uploadBatchId }
      })
      const userMessage: Message = {
        id: `u-${Date.now()}`, role: 'user',
        content: buildZhUploadCaptionPrefixedAp(`Uploaded ${files.length} file(s)`, processingMode),
        uploadedFiles: newQueue,
      }
      const queueMessage: Message = {
        id: `q-${Date.now()}`, role: 'assistant',
        content: `Queued ${files.length} file(s). Starting OCR...`,
      }
      const processingCountDup = tasksRef.current.filter(t => t.status === 'processing').length
      const willQueueDup = processingCountDup >= MAX_CONCURRENT_TASKS
      const overloadDup = ocrScanOverloadInfo([], newQueue.length, willQueueDup, totalOcrProcessingNonBankTasks(tasksRef.current))
      if (overloadDup.open) setOcrOverloadModal({ ocr: overloadDup.ocr, task: overloadDup.task })
      const newTask = createNewTask(processingMode, [userMessage, queueMessage], newQueue, files.length)
      await addPersistedTask(newTask)
      stashApQueuedFollowUpIfTyped(newTask.id, uploadBatchId)
    } else if (pending.source === 'attach' && pending.taskId) {
      const taskId = pending.taskId
      const uploadBatchId = makeChatRecordId()
      const newQueue: QueuedFile[] = files.map((file, index) => {
        const previewUrl = URL.createObjectURL(file)
        previewUrlsRef.current.add(previewUrl)
        return { id: `f-${Date.now()}-${index}`, file, status: 'pending', previewUrl, processingMode, uploadBatchId }
      })
      const currentProcessingCount = tasksRef.current.filter(t => t.status === 'processing').length
      const isActiveTaskAlreadyProcessing = tasksRef.current.find(t => t.id === taskId)?.status === 'processing'
      const needsSlot = !isActiveTaskAlreadyProcessing
      const willQueue = needsSlot && currentProcessingCount >= MAX_CONCURRENT_TASKS
      const userMessage: Message = {
        id: `u-${Date.now()}`, role: 'user',
        content: buildZhUploadCaptionPrefixedAp(`Uploaded ${files.length} file(s) (continued)`, processingMode),
        uploadedFiles: newQueue,
      }
      const queueMsg: Message = {
        id: `q-${Date.now()}`, role: 'assistant',
        content: willQueue
          ? `Queued ${files.length} file(s). ${currentProcessingCount} job(s) are already running; this task will wait.`
          : `Queued ${files.length} file(s). Starting OCR...`,
      }
      const queueNoticeMsg: Message | null = willQueue
        ? { id: `queue-notice-${Date.now()}`, role: 'assistant', content: '__QUEUE_NOTICE__' }
        : null
      const taskSnapDup = tasksRef.current.find(t => t.id === taskId)
      const overloadDupAttach = ocrScanOverloadInfo(taskSnapDup?.fileQueue ?? [], newQueue.length, willQueue, totalOcrProcessingNonBankTasks(tasksRef.current))
      if (overloadDupAttach.open) setOcrOverloadModal({ ocr: overloadDupAttach.ocr, task: overloadDupAttach.task })
      updateTask(taskId, t => {
        const newStatus = (t.status === 'idle' || t.status === 'completed' || t.status === 'failed')
          ? (willQueue ? 'queued' as TaskStatus : 'processing' as TaskStatus)
          : t.status
        const baseMsgs = [...t.messages, userMessage, queueMsg]
        const msgs = queueNoticeMsg ? [...baseMsgs, queueNoticeMsg] : baseMsgs
        return {
          ...t,
          processingMode,
          fileQueue: [...t.fileQueue, ...newQueue],
          fileCount: t.fileCount + files.length,
          messages: msgs,
          status: newStatus,
        }
      })
      stashApQueuedFollowUpIfTyped(taskId, uploadBatchId)
    }
  }

  const handleDupCancel = (confirmId: string) => {
    const pending = pendingDupUploadRef.current
    if (!pending || pending.confirmId !== confirmId) return
    pendingDupUploadRef.current = null

    // Replace the confirm card with a "cancelled" message
    setTasks(prev => prev.map(t => ({
      ...t,
      messages: t.messages.map(m =>
        m.dupConfirmId === confirmId
          ? { ...m, dupConfirmPending: false, dupAlertType: 'cancel' as const, content: `Upload cancelled for duplicate file ${m.dupFileNames || ''}.` }
          : m
      ),
    })))
  }

  const buildProgressMeta = (
    fileIndex: number, totalFiles: number,
    pageCurrent?: number, pageTotal?: number,
    pageVerification?: Record<string, string>,
  ): NonNullable<Message['progressMeta']> => {
    const safePageTotal = Math.max(1, pageTotal ?? 1)
    return {
      fileIndex, totalFiles,
      processingFiles: activeProcessingTasksRef.current.size,
      pageTotal: safePageTotal,
      pageCurrent: Math.max(1, Math.min(safePageTotal, pageCurrent ?? 1)),
      ...(pageVerification && Object.keys(pageVerification).length > 0 ? { pageVerification } : {}),
    }
  }

  const estimateFilePageCount = async (file: File): Promise<number> => {
    try { return await api.getFilePageCount(file) } catch { return 1 }
  }

  // ─── Mode change: reset to clean welcome screen ───────────────────────────
  // When the user switches modes, always deselect the current task so the
  // previous mode's messages are never visible in the new mode context.
  // The old task remains safely in the sidebar and can be reopened any time.
  // RECON and REPORT have their own entry/exit flows — skip this reset for REPORT.
  useEffect(() => {
    if (previousModeRef.current === processingMode) return
    if (
      processingMode === 'REPORT' || previousModeRef.current === 'REPORT'
    ) {
      previousModeRef.current = processingMode
      return
    }
    if (skipNextOcrModeDeselectRef.current) {
      skipNextOcrModeDeselectRef.current = false
      previousModeRef.current = processingMode
      return
    }
    // Navigate to a clean new-chat state for the new mode
    assignActiveTaskId(null)
    previousModeRef.current = processingMode
  }, [processingMode])

  // ─── BANK statement upload ────────────────────────────────────────────────
  const handleBankStatementUpload = async (taskId: string, queuedFiles: QueuedFile[]) => {
    const activeBankMode = processingMode
    const files = queuedFiles.map(f => f.file)

    // Local shims targeting this specific task
    const setMsgsInTask = (updater: Message[] | ((prev: Message[]) => Message[])) => {
      setTasks(prev => prev.map(t => {
        if (t.id !== taskId) return t
        const newMsgs = typeof updater === 'function' ? updater(t.messages) : updater
        return { ...t, messages: newMsgs }
      }))
    }
    const addMsgBeforeSpreadsheet = (msg: Message) => {
      setMsgsInTask(prev => [...prev, msg])
    }
    const upsertProgressMsg = (
      messageId: string, fileName: string, percent: number,
      label: string, progressMeta?: Message['progressMeta'] | (() => Message['progressMeta'])
    ) => {
      const safePercent = Math.max(0, Math.min(100, Math.round(percent)))
      const baseMeta = typeof progressMeta === 'function' ? progressMeta() : progressMeta
      const resolvedMeta = baseMeta && typeof baseMeta.pageTotal === 'number'
        ? { ...baseMeta, pageCurrent: baseMeta.pageCurrent ?? Math.max(1, Math.min(baseMeta.pageTotal, Math.round((safePercent / 100) * baseMeta.pageTotal))) }
        : baseMeta
      setMsgsInTask(prev => {
        const nextMessage: Message = {
          id: messageId, role: 'assistant',
          content: `Processing: ${fileName} (${safePercent}%)`,
          progressPercent: safePercent, progressLabel: label, progressMeta: resolvedMeta
        }
        const existingIndex = prev.findIndex(m => m.id === messageId)
        if (existingIndex !== -1) {
          const updated = [...prev]
          updated[existingIndex] = { ...updated[existingIndex], ...nextMessage }
          return updated
        }
        return [...prev, nextMessage]
      })
    }

    const batchPageCountBase = tasksRef.current.find(t => t.id === taskId)?.pageCount ?? 0
    const acc = {
      spreadsheetRows: [] as SpreadsheetRow[],
      bankTransactions: [] as BankTransaction[],
      fileRefs: [] as { id: string; name: string }[],
      bankBatchIds: new Set<string>(),
      totalPages: 0,
      contentParts: [] as string[],
      fileNamesSucceeded: [] as string[],
    }
    const batchErrorLines: string[] = []

    for (const [fileLoopIndex, queuedFile] of queuedFiles.entries()) {
      const file = queuedFile.file
      const taskCompanyId = queuedFile.companyId ?? activeCompany?.id ?? null
      const progressMessageId = `bank-progress-${queuedFile.id}`
      const estimatedPages = await estimateFilePageCount(file)
      const progressBaseMeta = (pageCurrent?: number, pageTotalOverride?: number, pageVerification?: Record<string, string>) =>
        buildProgressMeta(fileLoopIndex + 1, files.length, pageCurrent, pageTotalOverride ?? estimatedPages, pageVerification)
      activeProcessingTasksRef.current.add(progressMessageId)
      let bankJobId: string | undefined
      try {
        updateTask(taskId, t => ({
          ...t,
          fileQueue: t.fileQueue.map(f => f.id === queuedFile.id ? { ...f, companyId: taskCompanyId ?? undefined } : f),
        }))
        upsertProgressMsg(progressMessageId, file.name, 5, 'Queued', () => progressBaseMeta())
        let bankStorageRef: { id: string; name: string } | undefined
        try {
          const uploaded = await taskApi.uploadFile(taskId, file, taskCompanyId)
          bankStorageRef = { id: uploaded.id, name: file.name }
          updateTask(taskId, t => ({
            ...t,
            fileQueue: t.fileQueue.map(f =>
              f.id === queuedFile.id ? { ...f, taskFileId: uploaded.id } : f,
            ),
          }))
        } catch (err) {
          console.warn('[Tasks] Bank file storage upload failed:', err)
        }
        const started = await api.startBankStatementUploadJob(file, taskId, taskCompanyId)
        if (!started?.job_id) throw new Error('Failed to start bank statement job')
        bankJobId = started.job_id
        localBankUploadJobIdsRef.current.add(bankJobId)
        updateTask(taskId, t => ({
          ...t,
          fileQueue: t.fileQueue.map(f => f.id === queuedFile.id ? { ...f, bankJobId, companyId: taskCompanyId ?? undefined } : f),
          messages: t.messages.map(m =>
            m.id === progressMessageId
              ? { ...m, progressJob: { kind: 'bank' as const, jobId: bankJobId!, taskId, fileId: queuedFile.id } }
              : m
          ),
        }))

        let result: any = null
        let lastPercent = 5
        for (let pollCount = 0; pollCount < 4500; pollCount++) {
          const status = await api.getBankStatementUploadJobStatus(started.job_id, taskCompanyId)
          const pageTotal = Number(status?.page_total || estimatedPages || 1)
          const pageCurrent = Number(status?.page_current || 0)
          const percent = Number(status?.progress_percent ?? lastPercent)
          const label = typeof status?.label === 'string' && status.label.trim() ? status.label : 'BANK processing'
          lastPercent = Number.isFinite(percent) ? percent : lastPercent
          const pvRaw = status?.page_verification
          const pageVerification = pvRaw && typeof pvRaw === 'object' && !Array.isArray(pvRaw)
            ? pvRaw as Record<string, string>
            : undefined
          upsertProgressMsg(progressMessageId, file.name, lastPercent, label, () => progressBaseMeta(pageCurrent, pageTotal, pageVerification))
          if (status?.status === 'completed') { result = status?.result; break }
          if (status?.status === 'cancelled') {
            throw new DOMException('Bank statement upload cancelled', 'AbortError')
          }
          if (status?.status === 'failed') throw new Error(status?.error || 'Bank statement parse failed')
          await new Promise((resolve) => setTimeout(resolve, 1000))
        }
        if (!result) throw new Error('Bank statement processing timed out. Please try again.')

        const ocrPreviewText = typeof result.ocr_preview_text === 'string' ? result.ocr_preview_text.trim() : ''
        if (ocrPreviewText) {
          let ocrContent = `--- ${file.name} ---\nOCR complete (BANK upload)\n\nRaw OCR text:\n\`\`\`\n`
          ocrContent += ocrPreviewText.substring(0, 1000)
          if (ocrPreviewText.length > 1000) ocrContent += `\n... (${ocrPreviewText.length} characters)`
          ocrContent += '\n```\n'
          addMsgBeforeSpreadsheet({ id: `bank-ocr-${Date.now()}`, role: 'assistant', content: ocrContent, fullOcrText: ocrPreviewText })
        }
        addMsgBeforeSpreadsheet({
          id: `bank-parse-${Date.now()}`, role: 'assistant',
          content: `--- ${file.name} ---\nBANK parse complete\nBank: ${result.bank || 'UNKNOWN'}\nTransactions: ${result.count || 0}\nPages: ${result.pages_processed || 1}`,
        })

        const getTxnValue = (txn: any, keys: string[]) => {
          for (const key of keys) {
            const value = txn?.[key]
            if (value !== undefined && value !== null && String(value).trim() !== '') return value
          }
          return ''
        }
        const bankTxnRows = result?.transactions?.map((txn: any, idx: number) => {
          const depositValue = getTxnValue(txn, ['存入', 'received', 'deposit'])
          const withdrawalValue = getTxnValue(txn, ['提取', 'spent', 'withdrawal'])
          const originalBalanceValue = getTxnValue(txn, ['原幣結餘', 'balance', '結餘', '结余'])
          const accountTypeValue = getTxnValue(txn, ['賬戶類型', '帳戶類型', '账户类型', 'account_type', 'account_name', '賬戶名稱', '帳戶名稱', '账户名称'])
          const confidenceValue = getTxnValue(txn, ['信心度', 'confidence'])
          const formattedConfidence = typeof confidenceValue === 'number' ? confidenceValue.toFixed(2) : confidenceValue
          const bankTxnId = getTxnValue(txn, ['db_id', 'bank_txn_id', 'id'])
          const pageNum = txn['_page']
          const filePosition = pageNum ? `${file.name} P${pageNum}` : file.name
          return {
            id: bankTxnId || txn.id || `txn-${Date.now()}-${idx}`,
            bank_txn_id: bankTxnId,
            'No.': txn['No.'] || idx + 1,
            '憑證號': getTxnValue(txn, ['憑證號', 'reference', 'ref']),
            '類型': getTxnValue(txn, ['類型', 'type', 'transaction_type']),
            '存入': depositValue, '提取': withdrawalValue, '原幣結餘': originalBalanceValue,
            '幣別': getTxnValue(txn, ['幣別', 'currency']) || 'HKD',
            '日期': getTxnValue(txn, ['日期', 'date', 'transaction_date', 'bank_date']),
            '付款人': getTxnValue(txn, ['付款人', 'payer']),
            '收款人': getTxnValue(txn, ['收款人', 'payee']),
            '銀行': getTxnValue(txn, ['銀行', 'bank', 'source']) || result.bank || '',
            '賬戶類型': accountTypeValue,
            '備註': getTxnValue(txn, ['備註', 'description', 'memo', 'description_raw']),
            'categorise': getTxnValue(txn, ['categorise', '分類', 'category', 'account_category']),
            '信心度': formattedConfidence,
            '檔案位置': filePosition,
            'AR覆核': (() => {
              const st = txn._ar_manager_status as string | undefined
              if (st === 'verified') return '\u2713'
              if (st === 'needs_review' || st === 'error') return '\u2717'
              return txn._ar_manager_added ? 'Added' : txn._ar_manager_amended ? 'Yes' : ''
            })(),
          }
        }) || []

        if (result) {
          const bankPages = Number(result?.pages_processed || 1)
          upsertProgressMsg(progressMessageId, file.name, 96, 'Preparing results', () => progressBaseMeta(bankPages, bankPages))
          const rawBankTxns = (result.transactions || []) as Array<Record<string, unknown>>
          const arTouched = rawBankTxns.filter(t => t._ar_manager_amended)
          const arAdded = arTouched.filter(t => t._ar_manager_added).length
          const arPatched = arTouched.length - arAdded
          const arByStatus = rawBankTxns.reduce<{ verified: number; manual: number }>(
            (accStat, t) => {
              const s = t._ar_manager_status as string | undefined
              if (s === 'verified') accStat.verified += 1
              else if (s === 'needs_review' || s === 'error') accStat.manual += 1
              return accStat
            },
            { verified: 0, manual: 0 }
          )
          const pv = result.page_verification as Record<string, string> | undefined
          const pvEntries = pv && typeof pv === 'object' && !Array.isArray(pv) ? Object.entries(pv) : []
          const pagesOk = pvEntries.filter(([, v]) => v === 'verified').length
          const pagesBad = pvEntries.filter(([, v]) => v === 'needs_review').length
          const arNoticeFromStatus =
            arByStatus.verified + arByStatus.manual > 0
              ? `\n\nAR manager: ${arByStatus.verified} verified, ${arByStatus.manual} need manual review (see AR review column).`
              : ''
          const arNoticeFromPages =
            pagesOk + pagesBad > 0
              ? `\nPage review: ${pagesOk} verified, ${pagesBad} need review.`
              : ''
          const arNoticeLegacy =
            arTouched.length > 0 && arByStatus.verified + arByStatus.manual === 0
              ? `\n\nAR manager (model B): ${arPatched} row(s) field-amended, ${arAdded} new row(s); see AR review column.`
              : ''
          const arNotice = arNoticeFromStatus + arNoticeFromPages + arNoticeLegacy
          const rowBaseSheet = acc.spreadsheetRows.length
          const bankTxnRowsNumbered = bankTxnRows.map((row: SpreadsheetRow, idx: number) => ({
            ...row,
            'No.': rowBaseSheet + idx + 1,
          }))
          acc.spreadsheetRows.push(...bankTxnRowsNumbered)
          const rowBaseTxn = acc.bankTransactions.length
          const fileBankTxns = (result.transactions || []).map((txn: any, idx: number) => {
            const toN = (v: any): number | null => {
              if (v === null || v === undefined || String(v).trim() === '') return null
              const n = parseFloat(String(v).replace(/,/g, ''))
              return isNaN(n) ? null : n
            }
            const _dep = getTxnValue(txn, ['存入', 'received', 'deposit'])
            const _wit = getTxnValue(txn, ['提取', 'spent', 'withdrawal'])
            const _bal = getTxnValue(txn, ['原幣結餘', 'balance', '結餘', '结余'])
            const pageNum = txn['_page']
            const stem = file.name.replace(/\.[^.]+$/, '')
            const balNum = toN(_bal)
            const refVal = getTxnValue(txn, ['憑證號', 'reference']) || ''
            const bankDbId = getTxnValue(txn, ['db_id', 'bank_txn_id', 'id']) || String(idx + 1)
            return {
              ...txn,
              id_number: refVal || bankDbId,
              date: getTxnValue(txn, ['日期', 'date', 'transaction_date', 'bank_date']) || '',
              source_file: pageNum != null ? `${stem} P${pageNum}` : stem,
              account_type: getTxnValue(txn, ['賬戶類型', '帳戶類型', '账户类型', 'account_type']) || '',
              account_number: getTxnValue(txn, ['account_number']) || '',
              deposit: toN(_dep),
              withdrawal: toN(_wit),
              balance: balNum ?? undefined,
              particulars: getTxnValue(txn, ['備註', 'description', 'memo', 'description_raw']) || '',
              currency: getTxnValue(txn, ['幣別', 'currency']) || 'HKD',
              categorise: getTxnValue(txn, ['categorise', '分類', 'category']) || '',
              reference: refVal,
              _row: rowBaseTxn + idx + 1,
            }
          }) as BankTransaction[]
          acc.bankTransactions.push(...fileBankTxns)
          if (bankStorageRef) acc.fileRefs.push(bankStorageRef)
          acc.totalPages += bankPages
          const bankBatchId = result.import_batch_id || undefined
          if (bankBatchId) acc.bankBatchIds.add(bankBatchId)
          acc.fileNamesSucceeded.push(file.name)
          acc.contentParts.push(
            `--- ${file.name} ---\nBank statement uploaded: ${result.count} transaction(s) (${result.bank})${arNotice}`,
          )
          const taskRowBefore = tasksRef.current.find(t => t.id === taskId)
          const nextFqPreview = (taskRowBefore?.fileQueue || []).map(f =>
            f.id === queuedFile.id ? { ...f, status: 'completed' as const, result } : f,
          )
          const bankAllDone =
            nextFqPreview.length > 0 &&
            nextFqPreview.every(f => f.status === 'completed' || f.status === 'failed' || f.status === 'cancelled')
          updateTask(taskId, t => ({
            ...t,
            messages: t.messages.map(m =>
              m.uploadedFiles
                ? { ...m, uploadedFiles: m.uploadedFiles.map(f => f.id === queuedFile.id ? { ...f, status: 'completed' as const, result } : f) }
                : m
            ),
            fileQueue: t.fileQueue.map(f => f.id === queuedFile.id ? { ...f, status: 'completed' as const, result } : f),
            status: bankAllDone ? ('completed' as TaskStatus) : ('processing' as TaskStatus),
            processingMode: activeBankMode,
          }))
        }        const doneBankPages = Number(result?.pages_processed || 1)
        upsertProgressMsg(progressMessageId, file.name, 100, 'BANK OCR + AI complete', () => progressBaseMeta(doneBankPages, doneBankPages))
      } catch (error) {
        console.error('Bank statement upload failed:', error)
        if (error instanceof DOMException && error.name === 'AbortError') {
          markUploadCancelled(taskId, queuedFile.id, progressMessageId)
          continue
        }
        batchErrorLines.push(`${file.name}: ${error instanceof Error ? error.message : String(error)}`)
        upsertProgressMsg(progressMessageId, file.name, 100, 'Failed', () => progressBaseMeta())
        updateTask(taskId, t => ({
          ...t,
          fileQueue: t.fileQueue.map(f => f.id === queuedFile.id ? { ...f, status: 'failed' as const } : f),
          messages: t.messages.map(m =>
            m.uploadedFiles
              ? { ...m, uploadedFiles: m.uploadedFiles.map(f => f.id === queuedFile.id ? { ...f, status: 'failed' as const } : f) }
              : m
          ),
        }))
      } finally {
        activeProcessingTasksRef.current.delete(progressMessageId)
        if (bankJobId) localBankUploadJobIdsRef.current.delete(bankJobId)
      }
    }

    const taskAfterBatch = tasksRef.current.find(t => t.id === taskId)
    const taskCompanyIdBatch = taskAfterBatch?.fileQueue.find(f => f.companyId)?.companyId ?? activeCompany?.id ?? null
    const fq = taskAfterBatch?.fileQueue || []
    const bankAllDoneTask =
      fq.length > 0 && fq.every(f => f.status === 'completed' || f.status === 'failed' || f.status === 'cancelled')

    if (acc.bankTransactions.length > 0) {
      const snapshotFileRefs = acc.fileRefs.filter((r): r is { id: string; name: string } => Boolean(r?.id))
      const snapshotContent =
        mergedBankOcrSnapshotContent(
          { bankTransactions: acc.bankTransactions, fileRefs: snapshotFileRefs },
          acc.contentParts[0] || '',
        ) + (batchErrorLines.length > 0 ? `\n\nPartial failures:\n${batchErrorLines.join('\n')}` : '')
      try {
        const serverMsg = await taskApi.appendMessage(
          taskId,
          {
            role: 'assistant',
            content_text: snapshotContent,
            content_type: 'ocr_snapshot',
            payload_json: {
              spreadsheetData: acc.spreadsheetRows,
              bankTransactions: acc.bankTransactions,
              bankFilename: acc.fileNamesSucceeded.length === 1 ? acc.fileNamesSucceeded[0] : undefined,
              fileRefs: snapshotFileRefs,
            },
          },
          taskCompanyIdBatch,
        )
        const [mappedMsg] = mapServerTaskMessagesToClient([serverMsg])
        const mergedBids = acc.bankBatchIds.size
          ? Array.from(new Set([...(taskAfterBatch?.bankBatchIds || []), ...acc.bankBatchIds]))
          : taskAfterBatch?.bankBatchIds
        const terminalStatus: TaskStatus =
          bankAllDoneTask ? (acc.bankTransactions.length > 0 ? 'completed' : 'failed') : 'processing'
        updateTask(taskId, t => ({
          ...t,
          messages: [...t.messages, mappedMsg],
          spreadsheetData: acc.spreadsheetRows,
          pageCount: batchPageCountBase + acc.totalPages,
          hasSpreadsheet: true,
          bankBatchIds: mergedBids,
          status: terminalStatus,
          processingMode: activeBankMode,
        }))
        patchTaskMetadataFireAndForget(
          taskId,
          {
            page_count: batchPageCountBase + acc.totalPages,
            ...(bankAllDoneTask
              ? { status: acc.bankTransactions.length > 0 ? 'completed' : 'failed' }
              : { status: 'processing' }),
            has_spreadsheet: true,
          },
          taskCompanyIdBatch,
        )
      } catch (err) {
        console.warn('[Tasks] Bank OCR snapshot append failed:', err)
      }
    } else if (batchErrorLines.length > 0) {
      addMsgBeforeSpreadsheet({
        id: `bank-batch-err-${Date.now()}`,
        role: 'assistant',
        content: `Bank statement batch failed:\n${batchErrorLines.join('\n')}`,
      })
      if (bankAllDoneTask) {
        updateTask(taskId, t => ({ ...t, status: 'failed' as TaskStatus }))
        patchTaskMetadataFireAndForget(taskId, { status: 'failed', has_spreadsheet: false }, taskCompanyIdBatch)
      }
    }
  }

  // ─── RECON helpers ────────────────────────────────────────────────────────
  const handleReconUnmatchedChange = (
    messageId: string, section: 'bank' | 'ledger', updatedData: SpreadsheetRow[]
  ) => {
    suppressScrollRef.current = true
    setMessages((prev) => prev.map((m) => {
      if (m.id !== messageId || !m.reconUnmatched) return m
      return { ...m, reconUnmatched: { ...m.reconUnmatched, [section]: updatedData } }
    }))
  }

  const handleRunRecon = async () => {
    // In RECON mode activeTaskId is null; messages go to reconMessages
    const addMsgBeforeSpreadsheet = (msg: Message) => {
      setReconMessages(prev => [...prev, { ...msg, isReconResult: true }])
    }
    try {
      const pools = getReconPools()
      const selectedSourceRows = pools.selectedSource
      const selectedBankRows = pools.selectedBank
      const sourceMatchableRows = selectedSourceRows.filter((row) => row.matchable && row.txnId)
      const bankMatchableRows = selectedBankRows.filter((row) => row.matchable && row.txnId)

      const toUnmatchedRow = (row: ReconTransactionItem): SpreadsheetRow => {
        const base = { ...row.row }
        // Preserve the actual DB transaction ID so ReconciliationTable can use it for multi-match API calls
        if (row.txnId) base.txn_id = row.txnId
        if (!row.matchable) {
          base.unmatch_reason = 'missing_transaction_id'
          base['備註'] = base['備註'] || '[Missing ID] cannot auto-match'
          base.memo = base.memo || '[Missing ID] cannot auto-match'
        }
        return base
      }

      // Build a raw txn object from a ReconTransactionItem for ReconciliationTable
      const toRawBankTxn = (row: ReconTransactionItem) => ({
        id: row.txnId || row.uid,
        bank_date: row.date,
        description_raw: row.memo || row.recordTitle || '',
        amount: row.amount,
        currency: row.currency,
        reference: row.voucherNo || '',
        recordMode: row.recordMode,
        row: row.row,
      })
      const toRawLedgerTxn = (row: ReconTransactionItem) => ({
        id: row.txnId || row.uid,
        book_date: row.date,
        amount: row.amount,
        currency: row.currency,
        reference: row.voucherNo || '',
        counterparty: row.recordTitle || '',
        recordMode: row.recordMode,
      })

      // Single AR/AP in container, no bank: pending-bank / GL suspense group (0:N)
      const ledgerPendingSingle =
        bankMatchableRows.length === 0 && sourceMatchableRows.length === 1

      if (ledgerPendingSingle) {
        const row0 = sourceMatchableRows[0]
        const lid = row0.txnId!
        const fallbackSnapshots = { bank: [] as any[], ledger: [toRawLedgerTxn(row0)] }
        try {
          const res = await reconciliationApi.ledgerPendingMatch({ ledger_txn_ids: [lid] })
          applyReconMultiManualMatchResult([], [lid], res, fallbackSnapshots)
          addMsgBeforeSpreadsheet({
            id: `recon-ledger-pending-${Date.now()}`,
            role: 'assistant',
            content:
              'Created a pending-bank match group. The GL draft will balance through suspense; you can rematch or edit after bank rows load.',
          })
          setReconSelectedSourceTxnIds([])
          setReconSelectedBankTxnIds([])
        } catch (error) {
          setReconStatusText(`Match failed: ${error instanceof Error ? error.message : String(error)}`)
          addMsgBeforeSpreadsheet({
            id: `recon-ledger-pending-err-${Date.now()}`,
            role: 'assistant',
            content: `Match failed: ${error instanceof Error ? error.message : String(error)}`,
          })
        }
        return
      }

      // Single bank in container, no AR/AP: mark cleared (N:0 group) — symmetric to ledgerPendingSingle
      const bankPendingSingle =
        sourceMatchableRows.length === 0 && bankMatchableRows.length === 1

      if (bankPendingSingle) {
        const row0 = bankMatchableRows[0]
        const bid = row0.txnId!
        const fallbackSnapshots = { bank: [toRawBankTxn(row0)], ledger: [] as any[] }
        const rowData = row0.row as Record<string, unknown> | undefined
        const glCode = String(
          rowData?.account_category ?? rowData?.account_code ?? '',
        ).trim()
        try {
          const res = glCode
            ? await reconciliationApi.glOnlyMatch({ bank_txn_ids: [bid] })
            : await reconciliationApi.clearBankTransactions({ bank_txn_ids: [bid] })
          applyReconMultiManualMatchResult([bid], [], res, fallbackSnapshots)
          addMsgBeforeSpreadsheet({
            id: `recon-bank-cleared-${Date.now()}`,
            role: 'assistant',
            content: glCode
              ? `Bank GL match created (${glCode}). Approve to post cash + offset.`
              : 'Marked this bank transaction as cleared (no matching GL). Remove it from the matched group to undo.',
          })
          setReconSelectedSourceTxnIds([])
          setReconSelectedBankTxnIds([])
        } catch (error) {
          setReconStatusText(`Match failed: ${error instanceof Error ? error.message : String(error)}`)
          addMsgBeforeSpreadsheet({
            id: `recon-bank-cleared-err-${Date.now()}`,
            role: 'assistant',
            content: `Match failed: ${error instanceof Error ? error.message : String(error)}`,
          })
        }
        return
      }

      // Same-mode from match bar (mirror ReconciliationTable.handleMultiMatch): ≥2 on one side only
      const ledgerOnlyManual =
        bankMatchableRows.length === 0 && sourceMatchableRows.length >= 2
      const bankOnlyManual =
        sourceMatchableRows.length === 0 && bankMatchableRows.length >= 2

      if (ledgerOnlyManual || bankOnlyManual) {
        let finalBankIds: string[] = []
        let finalLedgerIds: string[] = []
        let fallbackSnapshots: { bank: any[]; ledger: any[] } | undefined
        if (ledgerOnlyManual) {
          const ids = sourceMatchableRows.map(r => r.txnId!).filter(Boolean)
          const half = Math.ceil(ids.length / 2)
          finalBankIds = ids.slice(0, half)
          finalLedgerIds = ids.slice(half)
          fallbackSnapshots = {
            bank: sourceMatchableRows.filter(r => finalBankIds.includes(r.txnId!)).map(toRawLedgerTxn),
            ledger: sourceMatchableRows.filter(r => finalLedgerIds.includes(r.txnId!)).map(toRawLedgerTxn),
          }
        } else {
          const ids = bankMatchableRows.map(r => r.txnId!).filter(Boolean)
          const half = Math.ceil(ids.length / 2)
          finalBankIds = ids.slice(0, half)
          finalLedgerIds = ids.slice(half)
          fallbackSnapshots = {
            bank: bankMatchableRows.filter(r => finalBankIds.includes(r.txnId!)).map(toRawBankTxn),
            ledger: bankMatchableRows.filter(r => finalLedgerIds.includes(r.txnId!)).map(toRawBankTxn),
          }
        }
        try {
          const res = await reconciliationApi.multiManualMatch({
            bank_txn_ids: finalBankIds,
            ledger_txn_ids: finalLedgerIds,
          })
          applyReconMultiManualMatchResult(finalBankIds, finalLedgerIds, res, fallbackSnapshots)
          addMsgBeforeSpreadsheet({
            id: `recon-manual-container-${Date.now()}`,
            role: 'assistant',
            content:
              res.difference !== 0
                ? `${ledgerOnlyManual ? 'AR/AP' : 'Bank'} match complete. Difference ${res.difference} is in the results; handle remainders in Unmatched / Partial.`
                : ledgerOnlyManual
                  ? 'Matched AR/AP (no bank in container): group created. Handle the GL voucher on the right.'
                  : 'Matched bank (no AR/AP in container): group created. Handle the GL voucher on the right.',
          })
          setReconSelectedSourceTxnIds([])
          setReconSelectedBankTxnIds([])
        } catch (error) {
          setReconStatusText(`Match failed: ${error instanceof Error ? error.message : String(error)}`)
          addMsgBeforeSpreadsheet({
            id: `recon-manual-err-${Date.now()}`,
            role: 'assistant',
            content: `Match failed: ${error instanceof Error ? error.message : String(error)}`,
          })
        }
        return
      }

      if (sourceMatchableRows.length === 0 || bankMatchableRows.length === 0) {
        const unmatchedBankRows = selectedBankRows.map(toUnmatchedRow)
        const unmatchedLedgerRows = selectedSourceRows.map(toUnmatchedRow)
        setReconUnmatchedRows({ bank: unmatchedBankRows, ledger: unmatchedLedgerRows })
        setReconUnmatchedTxns({
          // Only rows with a real DB txnId can participate in multi-match
          bank: selectedBankRows.filter(r => r.matchable && r.txnId).map(toRawBankTxn),
          ledger: selectedSourceRows.filter(r => r.matchable && r.txnId).map(toRawLedgerTxn),
        })
        const needTwo =
          (bankMatchableRows.length === 0 && sourceMatchableRows.length === 1) ||
          (sourceMatchableRows.length === 0 && bankMatchableRows.length === 1)
        setReconStatusText(
          needTwo
            ? 'One-sided match needs at least two matchable records on that side, or one on each side for auto-match.'
            : 'Missing matchable transaction IDs. Use records with a transaction ID, or put at least one on each side.',
        )
        addMsgBeforeSpreadsheet({
          id: `recon-empty-${Date.now()}`,
          role: 'assistant',
          content: needTwo
            ? 'When only one side has matchable rows, that side needs at least two to Match, or one on each side for cross-mode auto-match.'
            : 'No matchable transactions in the container (transaction ID required). Sent to Unmatched.',
        })
        return
      }

      // ── Same-mode guard: auto-match only runs on cross-mode pairs ────────────
      // Transactions whose mode appears on BOTH sides (e.g. BANK vs BANK, AR vs AR)
      // cannot be auto-matched — the user must reconcile them manually.
      const bankModeSet   = new Set(bankMatchableRows.map(r => r.recordMode).filter(Boolean))
      const sourceModeSet = new Set(sourceMatchableRows.map(r => r.recordMode).filter(Boolean))
      const overlappingModes = new Set([...bankModeSet].filter(m => sourceModeSet.has(m)))

      const bankForAutoMatch   = bankMatchableRows.filter(r => !overlappingModes.has(r.recordMode))
      const ledgerForAutoMatch = sourceMatchableRows.filter(r => !overlappingModes.has(r.recordMode))
      const sameModeBankRows   = bankMatchableRows.filter(r => overlappingModes.has(r.recordMode))
      const sameModeSourceRows = sourceMatchableRows.filter(r => overlappingModes.has(r.recordMode))

      // If only same-mode transactions were dragged in (no cross-mode pairs at all),
      // skip auto-match entirely and send everything to unmatched for manual processing.
      if (bankForAutoMatch.length === 0 || ledgerForAutoMatch.length === 0) {
        const allBankUnmatched   = selectedBankRows.map(toUnmatchedRow)
        const allLedgerUnmatched = selectedSourceRows.map(toUnmatchedRow)
        setReconUnmatchedRows({ bank: allBankUnmatched, ledger: allLedgerUnmatched })
        setReconUnmatchedTxns({
          bank:   bankMatchableRows.map(toRawBankTxn),
          ledger: sourceMatchableRows.map(toRawLedgerTxn),
        })
        setReconStatusText('Same-mode transactions loaded. Select matches manually.')
        addMsgBeforeSpreadsheet({
          id: `recon-same-mode-${Date.now()}`, role: 'assistant',
          content: `Same-mode transactions detected (${[...overlappingModes].join(', ')}). Moved to Unmatched for manual matching.`,
        })
        return
      }

      const selectedBankTxnIds   = bankForAutoMatch.map((row) => row.txnId)
      const selectedLedgerTxnIds = ledgerForAutoMatch.map((row) => row.txnId)
      const matchResults = await reconciliationApi.autoMatchSelected(selectedBankTxnIds, selectedLedgerTxnIds)
      const bankRowMap = new Map<string, SpreadsheetRow>()
      bankForAutoMatch.forEach((row) => { if (row.txnId) bankRowMap.set(String(row.txnId), row.row) })
      const ledgerRowMap = new Map<string, SpreadsheetRow>()
      ledgerForAutoMatch.forEach((row) => { if (row.txnId) ledgerRowMap.set(String(row.txnId), row.row) })

      const matchedBankIds = new Set(matchResults.matches.map((m) => m.bank_txn_id))
      const matchedLedgerIds = new Set(matchResults.matches.map((m) => m.ledger_txn_id))

      const matchingColumns = [
        'No.', RECON_GROUP_COL_HEADER, 'Match Type',
        'Bank Mode 憑證號', 'AR/AP Mode 憑證號',
        'Bank Total', 'AR/AP Total', 'Difference',
        '幣別', '日期', 'source', 'rule_hit',
      ]

      const matchingRows: SpreadsheetRow[] = matchResults.matches.map((match, index) => {
        const bankRow = bankRowMap.get(match.bank_txn_id)
        const ledgerRow = ledgerRowMap.get(match.ledger_txn_id)
        const bankAmt = Number(bankRow?.amount || bankRow?.['存入'] || bankRow?.['提取'] || 0)
        const ledgerAmt = Number(ledgerRow?.amount || 0)
        return {
          id: `recon-match-${match.bank_txn_id}-${match.ledger_txn_id}`,
          bank_txn_id: match.bank_txn_id, ledger_txn_id: match.ledger_txn_id,
          'No.': index + 1,
          [RECON_GROUP_COL_HEADER]: `${match.bank_txn_id}-${match.ledger_txn_id}`,
          'Match Type': '1:1',
          '匹配狀態': 'MATCHED',
          'Bank Mode 憑證號': bankRow?.['憑證號'] || '', 'AR/AP Mode 憑證號': ledgerRow?.voucher_no || '',
          'Bank Mode 類型': bankRow?.['類型'] || '', 'AR/AP Mode 類型': ledgerRow?.transaction_type || '',
          'AR/AP Mode categorise': ledgerRow?.category || ledgerRow?.account_category || '',
          'Bank Mode categorise': bankRow?.['categorise'] || bankRow?.['分類'] || bankRow?.category || '',
          'Bank Total': bankAmt || '',
          'AR/AP Total': ledgerAmt || '',
          'Difference': bankAmt && ledgerAmt ? Number((bankAmt - ledgerAmt).toFixed(2)) : 0,
          '存入': bankRow?.['存入'] || '', '提取': bankRow?.['提取'] || '',
          '原幣結餘': bankRow?.['原幣結餘'] || '',
          '幣別': bankRow?.['幣別'] || ledgerRow?.currency || '',
          '日期': bankRow?.['日期'] || ledgerRow?.date || '',
          '銀行': bankRow?.['銀行'] || ledgerRow?.bank || '',
          '賬戶類型': bankRow?.['賬戶類型'] || '',
          'AR/AP Mode 備註': ledgerRow?.memo || ledgerRow?.reference || '',
          'Bank Mode 備註': bankRow?.['備註'] || '',
          '信心度': bankRow?.['信心度'] || (match.score ? match.score.toFixed(2) : ''),
          source: 'reconciliation_auto_match', rule_hit: match.match_type || '',
          edited_by: '', edited_at: ''
        }
      })

      // Unmatched = items that weren't auto-matched + same-mode items skipped by the guard above
      const unmatchedBankItems = selectedBankRows.filter(
        (item) => !item.matchable || !matchedBankIds.has(String(item.txnId))
      )
      const unmatchedLedgerItems = selectedSourceRows.filter(
        (item) => !item.matchable || !matchedLedgerIds.has(String(item.txnId))
      )
      // Ensure same-mode rows are always included in their respective unmatched pools
      const sameModeBankSet   = new Set(sameModeBankRows.map(r => r.txnId))
      const sameModeSourceSet = new Set(sameModeSourceRows.map(r => r.txnId))
      const unmatchedBankRows = [
        ...unmatchedBankItems.map(toUnmatchedRow),
        ...sameModeBankRows.filter(r => !unmatchedBankItems.some(u => u.txnId === r.txnId)).map(toUnmatchedRow),
      ]
      const unmatchedLedgerRows = [
        ...unmatchedLedgerItems.map(toUnmatchedRow),
        ...sameModeSourceRows.filter(r => !unmatchedLedgerItems.some(u => u.txnId === r.txnId)).map(toUnmatchedRow),
      ]
      void sameModeBankSet; void sameModeSourceSet  // used above for dedup

      // ── (C) Write matched_id back to source transaction arrays ──────────────
      // Build txnId → voucherNo maps for quick lookup
      const bankTxnToVoucher = new Map<string, string>()
      bankMatchableRows.forEach(r => { if (r.txnId) bankTxnToVoucher.set(r.txnId, r.voucherNo || r.txnId) })
      const ledgerTxnToVoucher = new Map<string, string>()
      sourceMatchableRows.forEach(r => { if (r.txnId) ledgerTxnToVoucher.set(r.txnId, r.voucherNo || r.txnId) })

      const newReconState: ReconState = {}
      matchResults.matches.forEach(match => {
        const bankIdNum = bankTxnToVoucher.get(match.bank_txn_id) || match.bank_txn_id
        const ledgerIdNum = ledgerTxnToVoucher.get(match.ledger_txn_id) || match.ledger_txn_id
        newReconState[bankIdNum] = { status: 'matched', matched_id: ledgerIdNum }
        newReconState[ledgerIdNum] = { status: 'matched', matched_id: bankIdNum }
      })
      setReconState(newReconState)

      // Persist auto-match mappings into reconMatchedIdMap so matched_id survives browser refresh.
      // Keys are DB IDs (bank_txn_id / ledger_txn_id) AND voucherNos — the lazy-load code
      // looks up by whichever field is available first.
      setReconMatchedIdMap(prev => {
        const next = { ...prev }
        matchResults.matches.forEach(match => {
          const bankIdNum = bankTxnToVoucher.get(match.bank_txn_id) || match.bank_txn_id
          const ledgerIdNum = ledgerTxnToVoucher.get(match.ledger_txn_id) || match.ledger_txn_id
          if (match.bank_txn_id) next[match.bank_txn_id] = ledgerIdNum
          if (match.ledger_txn_id) next[match.ledger_txn_id] = bankIdNum
          // Also index by voucherNo as a secondary fallback key
          if (bankIdNum && bankIdNum !== match.bank_txn_id) next[bankIdNum] = ledgerIdNum
          if (ledgerIdNum && ledgerIdNum !== match.ledger_txn_id) next[ledgerIdNum] = bankIdNum
        })
        return next
      })

      // Propagate matched_id into bankTransactions / arapTransactions across all tasks
      setTasks(prev => prev.map(task => {
        const updatedMessages = task.messages.map(msg => {
          let changed = false
          let nextBank = msg.bankTransactions
          let nextArap = msg.arapTransactions
          if (msg.bankTransactions) {
            nextBank = msg.bankTransactions.map(t => {
              const bankKey = t.id_number || (t as any).reference || ''
              const entry = newReconState[bankKey]
              if (entry && entry.status === 'matched' && t.matched_id !== entry.matched_id) {
                changed = true
                return { ...t, matched_id: entry.matched_id }
              }
              return t
            })
          }
          if (msg.arapTransactions) {
            nextArap = msg.arapTransactions.map(t => {
              const idKey = t.id_number ?? ''
              // Strip AR-/AP- prefix when looking up — voucherNo in RECON pool may lack the prefix
              const baseKey = idKey.replace(/^(AR|AP)-/, '')
              const entry = newReconState[idKey] || newReconState[baseKey]
              if (entry && entry.status === 'matched' && t.matched_id !== entry.matched_id) {
                changed = true
                return { ...t, matched_id: entry.matched_id }
              }
              return t
            })
          }
          if (!changed) return msg
          return { ...msg, bankTransactions: nextBank, arapTransactions: nextArap }
        })
        return { ...task, messages: updatedMessages }
      }))
      // ────────────────────────────────────────────────────────────────────────

      setReconMatchedColumns(prevCols => {
        const merged = [...new Set([...prevCols, ...matchingColumns])]
        return merged
      })
      setReconMatchedRows(prev => {
        const fromGroups = matchedGroupsToSpreadsheetRows(
          reconMatchedGroupsRef.current,
          glVoucherNoByGroupIdRef.current,
        )
        const preserved = prev.filter(isReconNonGroupMatchedRow)
        const preservedFiltered = filterPreservedMatchedRowsCoveredByGroups(
          preserved,
          reconMatchedGroupsRef.current,
        )
        const existingPairs = new Set(
          preservedFiltered.map((r: any) => `${r.bank_txn_id ?? ''}-${r.ledger_txn_id ?? ''}`),
        )
        const dedupedNew = matchingRows.filter(
          r => !existingPairs.has(`${(r as any).bank_txn_id ?? ''}-${(r as any).ledger_txn_id ?? ''}`),
        )
        return mergeReconMatchedSheetRows(fromGroups, [...preservedFiltered, ...dedupedNew])
      })
      setReconUnmatchedRows({ bank: unmatchedBankRows, ledger: unmatchedLedgerRows })
      // Exclude any transactions that are already tracked as manually-matched UIDs
      // to prevent stale state after a browser refresh re-adding MATCHED txns to the pool.
      const alreadyMatchedUids = new Set([
        ...reconMatchedBankUids,
        ...reconMatchedSourceUids,
      ])
      setReconUnmatchedTxns({
        // Only rows with a real DB txnId can participate in multi-match,
        // and exclude any that were already manually matched.
        // Also include same-mode rows so they're available for manual matching.
        bank: [
          ...unmatchedBankItems.filter(r => r.matchable && r.txnId && !alreadyMatchedUids.has(r.uid)),
          ...sameModeBankRows.filter(r => r.matchable && r.txnId && !alreadyMatchedUids.has(r.uid)
            && !unmatchedBankItems.some(u => u.txnId === r.txnId)),
        ].map(toRawBankTxn),
        ledger: [
          ...unmatchedLedgerItems.filter(r => r.matchable && r.txnId && !alreadyMatchedUids.has(r.uid)),
          ...sameModeSourceRows.filter(r => r.matchable && r.txnId && !alreadyMatchedUids.has(r.uid)
            && !unmatchedLedgerItems.some(u => u.txnId === r.txnId)),
        ].map(toRawLedgerTxn),
      })
      // Fetch any existing PARTIAL-status remainder transactions for the matched table
      reconciliationApi.getPartialTransactions()
        .then(res => setReconPartialTxns(res.partial_transactions))
        .catch(err => console.error('Failed to fetch partial transactions', err))
      setReconStatusText(`Reconciliation complete: ${matchingRows.length} match(es)`)

      // Track matched UIDs so they can be preserved (locked) when re-entering RECON mode
      const newMatchedSourceUids = selectedSourceRows
        .filter(r => r.matchable && matchedLedgerIds.has(String(r.txnId)))
        .map(r => r.uid)
      const newMatchedBankUids = selectedBankRows
        .filter(r => r.matchable && matchedBankIds.has(String(r.txnId)))
        .map(r => r.uid)
      setReconMatchedSourceUids(prev => Array.from(new Set([...prev, ...newMatchedSourceUids])))
      setReconMatchedBankUids(prev => Array.from(new Set([...prev, ...newMatchedBankUids])))
      setReconMatchResult({ matchedCount: matchingRows.length, timestamp: Date.now() })

      const matchingMessage: Message = {
        id: `recon-match-${Date.now()}`, role: 'assistant',
        content: `Reconciliation complete: ${matchingRows.length} match(es)`,
        spreadsheetData: matchingRows, spreadsheetColumns: matchingColumns, spreadsheetHeaders: matchingColumns,
        isReconResult: true,
      }
      const unmatchedMessage: Message = {
        id: `recon-unmatched-${Date.now()}`, role: 'assistant',
        content: 'Unmatched results:',
        reconUnmatched: { bank: unmatchedBankRows, ledger: unmatchedLedgerRows },
        isReconResult: true,
      }

      // Replace previous RECON result messages so re-running doesn't stack results
      const nextReconMessages = [
        ...reconMessages.filter(m => !m.spreadsheetColumns && !m.reconUnmatched),
          matchingMessage,
          unmatchedMessage,
      ]
      setReconMessages(nextReconMessages)

      // Persist to dedicated RECON task so it survives page refresh and shows in sidebar
      const reconTaskId = await ensureReconTask()
      updateTask(reconTaskId, t => ({
        ...t,
        messages: nextReconMessages,
        spreadsheetData: matchingRows,
        pageCount: matchingRows.length,
        hasSpreadsheet: matchingRows.length > 0,
        status: 'completed' as TaskStatus,
      }))
      patchTaskMetadataFireAndForget(reconTaskId, { page_count: matchingRows.length, has_spreadsheet: matchingRows.length > 0 })
      taskApi.upsertOcrSnapshot(reconTaskId, {
        role: 'assistant',
        content_text: `Reconciliation complete: ${matchingRows.length} match(es)`,
        payload_json: {
          spreadsheetData: matchingRows,
          spreadsheetColumns: matchingColumns,
          reconUnmatched: { bank: unmatchedBankRows, ledger: unmatchedLedgerRows },
          isReconResult: true,
        },
      }).catch(err => console.warn('[RECON] snapshot save failed', err))

    } catch (error) {
      setReconStatusText(`Reconciliation failed: ${error instanceof Error ? error.message : String(error)}`)
      addMsgBeforeSpreadsheet({ id: `recon-error-${Date.now()}`, role: 'assistant', content: `Reconciliation failed: ${error instanceof Error ? error.message : String(error)}` })
    }
  }

  // ─── AI Match: duplicate detection + smart matching in one pass ───────────
  const handleAIMatch = async () => {
    // In RECON mode activeTaskId is null — use the first BANK task id for the API call
    // (the backend needs any valid task reference; RECON itself has no task row).
    const reconBankTask = tasks.find(t => t.processingMode === 'BANK' && (t.bankBatchIds?.length || t.hasSpreadsheet))
    const taskId = activeTaskId || reconBankTask?.id || tasks[0]?.id || 'recon'

    // Messages go to the RECON chat panel (not to an individual task)
    const addMsg = (msg: Message) => {
      setReconMessages(prev => [...prev, { ...msg, isReconResult: true }])
    }

    addMsg({ id: `ai-match-thinking-${Date.now()}`, role: 'assistant', content: 'AI is analysing duplicates and matches…' })

    try {
      const pools = getReconPools()
      const selectedSourceRows = pools.selectedSource
      const selectedBankRows   = pools.selectedBank

      const bankMatchable   = selectedBankRows.filter(r => r.matchable && r.txnId)
      const ledgerMatchable = selectedSourceRows.filter(r => r.matchable && r.txnId)

      if (bankMatchable.length === 0 || ledgerMatchable.length === 0) {
        addMsg({ id: `ai-match-empty-${Date.now()}`, role: 'assistant', content: 'Not enough matchable transactions in the container (transaction ID required).' })
        return
      }

      const bankTxnIds   = bankMatchable.map(r => r.txnId!)
      const ledgerTxnIds = ledgerMatchable.map(r => r.txnId!)

      const result = await reconciliationApi.aiMatch(bankTxnIds, ledgerTxnIds, taskId)

      // ── 1. Process duplicate alerts ──────────────────────────────────────
      if (result.duplicates && result.duplicates.length > 0) {
        const newAlerts: DuplicateAlert[] = result.duplicates.map(d => ({
          id:      `ai-dup-${Date.now()}-${Math.random()}`,
          level:   d.level as 1 | 2 | 3 | 4,
          taskId,
          message: d.reason,
          txnIds:  d.txn_ids.map(id => ({ msgId: '', txnIndex: -1, idNumber: id })),
        }))
        setDuplicateAlerts(prev => [...prev, ...newAlerts])
      }

      // ── 2. Build matched rows (same schema as handleRunRecon) ────────────
      const bankRowMap   = new Map<string, ReconTransactionItem>()
      const ledgerRowMap = new Map<string, ReconTransactionItem>()
      bankMatchable.forEach(r   => { if (r.txnId) bankRowMap.set(r.txnId, r) })
      ledgerMatchable.forEach(r => { if (r.txnId) ledgerRowMap.set(r.txnId, r) })

      const matchingColumns = [
        'No.', RECON_GROUP_COL_HEADER, 'Match Type',
        'Bank Mode 憑證號', 'AR/AP Mode 憑證號',
        'Bank Total', 'AR/AP Total', 'Difference',
        '幣別', '日期', '信心度', 'AI 配對原因', 'source', 'rule_hit',
      ]

      const matchedBankIds   = new Set(result.matches.map(m => m.bank_txn_id))
      const matchedLedgerIds = new Set(result.matches.map(m => m.ledger_txn_id))

      const matchingRows: SpreadsheetRow[] = result.matches.map((match, idx) => {
        const bankItem   = bankRowMap.get(match.bank_txn_id)
        const ledgerItem = ledgerRowMap.get(match.ledger_txn_id)
        const bankRow    = (bankItem?.row   ?? {}) as Record<string, any>
        const ledgerRow  = (ledgerItem?.row ?? {}) as Record<string, any>
        const bankAmt    = Number(bankRow?.amount || bankRow?.['存入'] || bankRow?.['提取'] || 0)
        const ledgerAmt  = Number(ledgerRow?.amount || 0)
        return {
          id:                   `ai-match-${match.bank_txn_id}-${match.ledger_txn_id}`,
          bank_txn_id:          match.bank_txn_id,
          ledger_txn_id:        match.ledger_txn_id,
          'No.':                idx + 1,
          [RECON_GROUP_COL_HEADER]: `${match.bank_txn_id}-${match.ledger_txn_id}`,
          'Match Type':         match.match_type || '1:1',
          '匹配狀態':           'MATCHED',
          'Bank Mode 憑證號':   bankRow?.['憑證號']         || '',
          'AR/AP Mode 憑證號':  ledgerRow?.voucher_no       || '',
          'Bank Mode 類型':     bankRow?.['類型']           || '',
          'AR/AP Mode 類型':    ledgerRow?.transaction_type || '',
          'AR/AP Mode categorise': ledgerRow?.category || ledgerRow?.account_category || '',
          'Bank Mode categorise':  bankRow?.['categorise'] || bankRow?.['分類'] || '',
          'Bank Total':         bankAmt  || '',
          'AR/AP Total':        ledgerAmt || '',
          'Difference':         bankAmt && ledgerAmt ? Number((bankAmt - ledgerAmt).toFixed(2)) : 0,
          '存入':               bankRow?.['存入']  || '',
          '提取':               bankRow?.['提取']  || '',
          '原幣結餘':           bankRow?.['原幣結餘'] || '',
          '幣別':               bankRow?.['幣別'] || ledgerRow?.currency || '',
          '日期':               bankRow?.['日期'] || ledgerRow?.date     || '',
          '銀行':               bankRow?.['銀行'] || ledgerRow?.bank     || '',
          '賬戶類型':           bankRow?.['賬戶類型'] || '',
          'AR/AP Mode 備註':    ledgerRow?.memo || ledgerRow?.reference || '',
          'Bank Mode 備註':     bankRow?.['備註'] || '',
          '信心度':             match.score ? match.score.toFixed(2) : '',
          'AI 配對原因':        match.ai_reason || '',
          source:               'ai_match',
          rule_hit:             'ai',
        }
      })

      // ── 3. Build unmatched lists ────────────────────────────────────────
      const toUnmatchedRow = (row: ReconTransactionItem): SpreadsheetRow => {
        const base = { ...row.row }
        if (row.txnId) base.txn_id = row.txnId
        if (!row.matchable) {
          base.unmatch_reason = 'missing_transaction_id'
          base['備註'] = base['備註'] || '[Missing ID] cannot auto-match'
        }
        return base
      }
      const unmatchedBankItems   = selectedBankRows.filter(r => !r.matchable || !matchedBankIds.has(String(r.txnId)))
      const unmatchedLedgerItems = selectedSourceRows.filter(r => !r.matchable || !matchedLedgerIds.has(String(r.txnId)))
      const unmatchedBankRows    = unmatchedBankItems.map(toUnmatchedRow)
      const unmatchedLedgerRows  = unmatchedLedgerItems.map(toUnmatchedRow)

      // ── 4. Update reconState so chips show as locked ─────────────────────
      const toRawBankTxn   = (r: ReconTransactionItem) => ({
        id: r.txnId || r.uid, bank_date: r.date, description_raw: r.memo || r.recordTitle || '', amount: r.amount, currency: r.currency, reference: r.voucherNo || '', recordMode: r.recordMode, row: r.row,
      })
      const toRawLedgerTxn = (r: ReconTransactionItem) => ({
        id: r.txnId || r.uid, book_date: r.date, amount: r.amount, currency: r.currency, reference: r.voucherNo || '', counterparty: r.recordTitle || '', recordMode: r.recordMode,
      })

      const newReconState: ReconState = {}
      result.matches.forEach(m => {
        const bankItem   = bankRowMap.get(m.bank_txn_id)
        const ledgerItem = ledgerRowMap.get(m.ledger_txn_id)
        const bKey = bankItem?.voucherNo   || m.bank_txn_id
        const lKey = ledgerItem?.voucherNo || m.ledger_txn_id
        newReconState[bKey] = { status: 'matched', matched_id: lKey }
        newReconState[lKey] = { status: 'matched', matched_id: bKey }
      })
      setReconState(newReconState)

      // Persist AI-match mappings into reconMatchedIdMap so matched_id survives browser refresh.
      setReconMatchedIdMap(prev => {
        const next = { ...prev }
        result.matches.forEach((m: any) => {
          const bankItem   = bankRowMap.get(m.bank_txn_id)
          const ledgerItem = ledgerRowMap.get(m.ledger_txn_id)
          const bKey = bankItem?.voucherNo   || m.bank_txn_id
          const lKey = ledgerItem?.voucherNo || m.ledger_txn_id
          if (m.bank_txn_id)   next[m.bank_txn_id]   = lKey
          if (m.ledger_txn_id) next[m.ledger_txn_id] = bKey
          if (bKey && bKey !== m.bank_txn_id)     next[bKey] = lKey
          if (lKey && lKey !== m.ledger_txn_id)   next[lKey] = bKey
        })
        return next
      })

      setReconMatchedColumns(prevCols => [...new Set([...prevCols, ...matchingColumns])])
      setReconMatchedRows(prev => {
        const fromGroups = matchedGroupsToSpreadsheetRows(
          reconMatchedGroupsRef.current,
          glVoucherNoByGroupIdRef.current,
        )
        const preserved = prev.filter(isReconNonGroupMatchedRow)
        const preservedFiltered = filterPreservedMatchedRowsCoveredByGroups(
          preserved,
          reconMatchedGroupsRef.current,
        )
        const existingPairs = new Set(
          preservedFiltered.map((r: any) => `${r.bank_txn_id ?? ''}-${r.ledger_txn_id ?? ''}`),
        )
        const dedupedNew = matchingRows.filter(
          r => !existingPairs.has(`${(r as any).bank_txn_id ?? ''}-${(r as any).ledger_txn_id ?? ''}`),
        )
        return mergeReconMatchedSheetRows(fromGroups, [...preservedFiltered, ...dedupedNew])
      })
      setReconUnmatchedRows({ bank: unmatchedBankRows, ledger: unmatchedLedgerRows })
      setReconUnmatchedTxns({
        bank:   unmatchedBankItems.filter(r => r.matchable && r.txnId).map(toRawBankTxn),
        ledger: unmatchedLedgerItems.filter(r => r.matchable && r.txnId).map(toRawLedgerTxn),
      })
      setReconMatchedSourceUids(prev => [...prev, ...ledgerMatchable.filter(r => matchedLedgerIds.has(r.txnId!)).map(r => r.uid)])
      setReconMatchedBankUids(prev   => [...prev, ...bankMatchable.filter(r => matchedBankIds.has(r.txnId!)).map(r => r.uid)])

      // ── 5. Push summary + result messages to chat ────────────────────────
      const dupCount   = result.duplicates?.length ?? 0
      const matchCount = result.matches.length
      const summaryMsg: Message = {
        id:            `ai-match-summary-${Date.now()}`,
        role:          'assistant',
        content:       `AI match complete\n\n${result.summary}\n\nDuplicates: ${dupCount} ｜ Matches: ${matchCount} ｜ Highest confidence: ${result.matches[0]?.score ? (result.matches[0].score * 100).toFixed(0) + '%' : 'N/A'}`,
        isReconResult: true,
      }
      const matchTableMsg: Message = {
        id:              `ai-match-table-${Date.now()}`,
        role:            'assistant',
        content:         'Match results:',
        spreadsheetData: matchingRows,
        spreadsheetColumns: matchingColumns,
        spreadsheetHeaders: matchingColumns,
        isReconResult:   true,
      }
      const unmatchedMsg: Message = {
        id:            `ai-match-unmatched-${Date.now()}`,
        role:          'assistant',
        content:       'Unmatched results:',
        reconUnmatched: { bank: unmatchedBankRows, ledger: unmatchedLedgerRows },
        isReconResult: true,
      }

      // Replace the "thinking…" placeholder with the real results in reconMessages
      const nextReconMessages = [
        ...reconMessages.filter(m => m.content !== 'AI is analysing duplicates and matches…'),
        { ...summaryMsg, isReconResult: true },
        { ...matchTableMsg, isReconResult: true },
        { ...unmatchedMsg, isReconResult: true },
      ]
      setReconMessages(nextReconMessages)

      // Persist to dedicated RECON task
      const reconTaskId = await ensureReconTask()
      updateTask(reconTaskId, t => ({
        ...t,
        messages: nextReconMessages,
        spreadsheetData: matchingRows,
        pageCount: matchingRows.length,
        hasSpreadsheet: matchingRows.length > 0,
        status: 'completed' as TaskStatus,
      }))
      patchTaskMetadataFireAndForget(reconTaskId, { page_count: matchingRows.length, has_spreadsheet: matchingRows.length > 0 })
      taskApi.upsertOcrSnapshot(reconTaskId, {
        role: 'assistant',
        content_text: result.summary,
        payload_json: {
          spreadsheetData: matchingRows,
          spreadsheetColumns: matchingColumns,
          reconUnmatched: { bank: unmatchedBankRows, ledger: unmatchedLedgerRows },
          isReconResult: true,
        },
      }).catch(err => console.warn('[RECON AI] snapshot save failed', err))

      setReconStatusText(result.summary)

    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error)
      setReconStatusText(`AI match failed: ${errMsg}`)
      setReconMessages(prev => [
        ...prev.filter(m => m.content !== 'AI is analysing duplicates and matches…'),
        { id: `ai-match-err-${Date.now()}`, role: 'assistant', content: `AI match failed: ${errMsg}`, isReconResult: true },
      ])
    }
  }

  // ─── Create a new chat task entry ─────────────────────────────────────────
  const createNewTask = (
    mode: ProcessingMode,
    initialMessages: Message[],
    initialFileQueue: QueuedFile[],
    fileCount: number
  ): ChatTask => {
    const processingCount = tasks.filter(t => t.status === 'processing').length
    const status: TaskStatus = processingCount < MAX_CONCURRENT_TASKS ? 'processing' : 'queued'
    const now = new Date()
    const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    const title = `${MONTHS[now.getMonth()]} ${now.getDate()} OCR`

    // Add queue notice message if task will be queued
    const msgs = status === 'queued'
      ? [...initialMessages, {
          id: `queue-notice-${Date.now()}`,
          role: 'assistant' as const,
          content: `__QUEUE_NOTICE__`
        }]
      : initialMessages

    return {
      id: makeChatRecordId(),
      title,
      createdAt: now.toISOString(),
      status,
      processingMode: mode,
      messages: msgs,
      fileQueue: initialFileQueue,
      fileCount,
      pageCount: 0,
      hasSpreadsheet: false,
    }
  }

  // ─── Ensure a task exists in tasks[] before first content is added ────────
  // Returns the task ID (generated before any async setState); awaits API create so chat jobs can persist messages.
  const ensureTaskSaved = async (): Promise<string> => {
    // Use ref, not `activeTaskId` state: `assignActiveTaskId` updates the ref synchronously but state
    // lags one render — after New Chat (null ref), an immediate send would otherwise read stale state
    // and attach messages to the previous task.
    const anchorId = activeTaskIdRef.current
    const serverKnown = lastSuccessfulServerTaskIdsRef.current
    if (
      anchorId &&
      (serverKnown?.has(anchorId) || pendingLocalTaskIdsRef.current.has(anchorId))
    ) {
      return anchorId
    }
    const now = new Date()
    const _MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    const tempTitle = `${_MONTHS[now.getMonth()]} ${now.getDate()} Chat`
    const hintMsg: Message | null = acceptsCsvUpload(processingMode) ? {
      id: `csv-hint-${Date.now()}`, role: 'assistant',
      content: 'You can upload multiple PDFs, or a CSV (CSV skips VLM and imports transactions directly).',
      csvHint: true,
    } : null
    const newTask: ChatTask = {
      id: makeChatRecordId(),
      title: tempTitle,
      createdAt: now.toISOString(),
      status: 'idle',
      processingMode,
      messages: hintMsg ? [...seedMessages, hintMsg] : [...seedMessages],
      fileQueue: [],
      fileCount: 0,
      pageCount: 0,
      hasSpreadsheet: false,
      titleGenerated: false,
    }
    // Before activation: hydrate per-task LS so `activeTaskId` effect does not clear welcome-time chip state.
    if (processingModeRef.current === 'AP') {
      try {
        localStorage.setItem(
          AP_COMPOSER_LS_PREFIX + newTask.id,
          JSON.stringify({ receipt: apReceiptSignalRef.current, table: apTablePresetRef.current }),
        )
      } catch {
        /* ignore quota */
      }
    }
    if (!(await addPersistedTask(newTask))) {
      throw new Error('Task create failed')
    }
    return newTask.id
  }

  /** Welcome screen: create a sidebar task as soon as the user types non-empty text (AR/AP/BANK/OTHER only). */
  async function ensureComposerTaskIfTyping(textSnapshot: string) {
    if (activeTaskIdRef.current) return
    if (!textSnapshot.trim()) return
    const mode = processingModeRef.current
    if (mode === 'RECON' || mode === 'REPORT') return
    try {
      if (!composerEnsureTaskPromiseRef.current) {
        composerEnsureTaskPromiseRef.current = ensureTaskSaved().finally(() => {
          composerEnsureTaskPromiseRef.current = null
        })
      }
      const id = await composerEnsureTaskPromiseRef.current
      if (id && textSnapshot.trim()) {
        composerDraftByTaskRef.current[id] = textSnapshot
      }
      if (!activeTaskIdRef.current && id) {
        assignActiveTaskId(id)
      }
    } catch {
      /* ignore task create failure */
    }
  }

  // ─── Ensure a dedicated RECON task exists for result persistence ─────────
  // Creates one the first time it is called per session; returns its ID.
  const ensureReconTask = async (): Promise<string> => {
    if (reconTaskIdRef.current) return reconTaskIdRef.current
    // Re-use an existing RECON task if available (e.g. after page refresh)
    const existing = reconTaskIdRef.current
      ? tasks.find(t => t.id === reconTaskIdRef.current)
      : tasks.find(t => t.processingMode === 'RECON')
    if (existing) {
      reconTaskIdRef.current = existing.id
      return existing.id
    }
    const now = new Date()
    const _MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    const newTask: ChatTask = {
      id: makeChatRecordId(),
      title: `GL journal ${_MONTHS[now.getMonth()]} ${now.getDate()}`,
      createdAt: now.toISOString(),
      status: 'completed',
      processingMode: 'BANK',
      messages: [...reconSeedMessages],
      fileQueue: [],
      fileCount: 0,
      pageCount: 0,
      hasSpreadsheet: false,
      titleGenerated: true,
    }
    if (!(await addPersistedTask(newTask, false))) {
      throw new Error('Journal task create failed')
    }
    reconTaskIdRef.current = newTask.id
    return newTask.id
  }

  // ─── Generate AI title and update sidebar after first content ──────────────
  // Fire-and-forget; skips if title was already generated for this task.
  const generateAndSetTitle = (taskId: string, msgs: Message[], companyId?: string | null) => {
    const task = tasksRef.current.find(t => t.id === taskId)
    if (task?.titleGenerated) return

    const titleMsgs = msgs
      .filter(m => (m.role === 'user' || m.role === 'assistant') && !m.progressPercent)
      .slice(0, 6)
      .map(m => ({ role: m.role, content: m.content }))
    if (titleMsgs.length < 1) return

    // Mark as generated immediately to prevent concurrent duplicate calls
    updateTask(taskId, t => ({ ...t, titleGenerated: true }))

    void api.generateTitle(titleMsgs, processingMode).then(title => {
      updateTask(taskId, t => ({ ...t, title }))
      // Sync title to DB
      patchTaskMetadataFireAndForget(taskId, { title, title_generated: true }, companyId)
    })
  }

  // ─── New Chat: resets to unsaved empty state ───────────────────────────────
  const handleNewChat = () => {
    if (processingMode === 'RECON') exitReconMode()
    if (processingMode === 'REPORT') exitReportMode()
    // Do NOT create a task — seedMessages fallback at line 159 provides the welcome UI.
    // The task is only created on first user interaction (ensureTaskSaved).
    assignActiveTaskId(null)
  }

  // ─── New Tasks menu: pick OCR mode then start fresh ────────────────────────
  const [newTasksMenuOpen, setNewTasksMenuOpen] = useState(false)
  const newTasksMenuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!newTasksMenuOpen) return
    const handleOutside = (e: MouseEvent) => {
      if (newTasksMenuRef.current && !newTasksMenuRef.current.contains(e.target as Node)) {
        setNewTasksMenuOpen(false)
      }
    }
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setNewTasksMenuOpen(false)
    }
    document.addEventListener('mousedown', handleOutside)
    document.addEventListener('keydown', handleEsc)
    return () => {
      document.removeEventListener('mousedown', handleOutside)
      document.removeEventListener('keydown', handleEsc)
    }
  }, [newTasksMenuOpen])

  // ─── Account menu (Dashboard, Company setting) ─────────────────────────────
  const [accountMenuOpen, setAccountMenuOpen] = useState(false)
  const accountMenuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!accountMenuOpen) return
    const handleOutside = (e: MouseEvent) => {
      if (accountMenuRef.current && !accountMenuRef.current.contains(e.target as Node)) {
        setAccountMenuOpen(false)
      }
    }
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setAccountMenuOpen(false)
    }
    document.addEventListener('mousedown', handleOutside)
    document.addEventListener('keydown', handleEsc)
    return () => {
      document.removeEventListener('mousedown', handleOutside)
      document.removeEventListener('keydown', handleEsc)
    }
  }, [accountMenuOpen])

  /** Mobile: position header “down” menus in viewport (absolute+wide panel was clipping off-screen). */
  const newTasksTriggerRef = useRef<HTMLButtonElement>(null)
  const accountMenuTriggerRef = useRef<HTMLButtonElement>(null)
  const [headerDownDropdownTop, setHeaderDownDropdownTop] = useState<number | null>(null)

  useLayoutEffect(() => {
    if (!isMobile) {
      setHeaderDownDropdownTop(null)
      return
    }
    if (!newTasksMenuOpen && !accountMenuOpen) {
      setHeaderDownDropdownTop(null)
      return
    }
    const el = newTasksMenuOpen ? newTasksTriggerRef.current : accountMenuTriggerRef.current
    if (!el) return
    setHeaderDownDropdownTop(el.getBoundingClientRect().bottom + 8)
  }, [isMobile, newTasksMenuOpen, accountMenuOpen])

  useEffect(() => {
    if (!isMobile || (!newTasksMenuOpen && !accountMenuOpen)) return
    const sync = () => {
      const el = newTasksMenuOpen ? newTasksTriggerRef.current : accountMenuTriggerRef.current
      if (el) setHeaderDownDropdownTop(el.getBoundingClientRect().bottom + 8)
    }
    window.addEventListener('resize', sync)
    window.addEventListener('scroll', sync, true)
    return () => {
      window.removeEventListener('resize', sync)
      window.removeEventListener('scroll', sync, true)
    }
  }, [isMobile, newTasksMenuOpen, accountMenuOpen])

  const accountInitials = (() => {
    if (!user) return '?'
    const name = user.display_name?.trim()
    if (name) {
      const parts = name.split(/\s+/).filter(Boolean)
      if (parts.length >= 2) {
        return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
      }
      return name.slice(0, 2).toUpperCase()
    }
    const handle = (user.username || user.email || '?').trim()
    return handle.slice(0, 2).toUpperCase()
  })()

  /** Welcome + New tasks dropdown: create a task in `mode` and select it (no file picker). */
  const handleWelcomeNewTaskWithMode = async (mode: ProcessingMode) => {
    if (processingMode === 'RECON') exitReconMode()
    if (processingMode === 'REPORT') exitReportMode()
    setNewTasksMenuOpen(false)
    if (mode !== processingMode) {
      skipNextOcrModeDeselectRef.current = true
    }

    const now = new Date()
    const _MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    const tempTitle = `${_MONTHS[now.getMonth()]} ${now.getDate()} Chat`
    const hintMsg: Message | null = acceptsCsvUpload(mode) ? {
      id: `csv-hint-${Date.now()}`,
      role: 'assistant',
      content: 'You can upload multiple PDFs, or a CSV (CSV skips VLM and imports transactions directly).',
      csvHint: true,
    } : null
    const newTask: ChatTask = {
      id: makeChatRecordId(),
      title: tempTitle,
      createdAt: now.toISOString(),
      status: 'idle',
      processingMode: mode,
      messages: hintMsg ? [...seedMessages, hintMsg] : [...seedMessages],
      fileQueue: [],
      fileCount: 0,
      pageCount: 0,
      hasSpreadsheet: false,
      titleGenerated: false,
    }
    await addPersistedTask(newTask)
    setProcessingMode(mode)
  }

  /** AP: block OCR intake until receipt + table are chosen; modal lists missing facets. */
  const blockApUntilComposerOptionsChosen = (): boolean => {
    if (processingMode !== 'AP') return false
    if (hasFullApComposerOptions(apReceiptSignalRef.current, apTablePresetRef.current)) return false
    setApComposerDialog('incomplete_upload')
    return true
  }

  const stashApQueuedFollowUpIfTyped = (taskId: string, uploadBatchKey: string) => {
    const typed = composerTrimRef.current
    if (!typed) return
    if (!hasFullApComposerOptions(apReceiptSignalRef.current, apTablePresetRef.current)) return
    pendingApFollowUpChatByBatchKeyRef.current.set(`${taskId}\u003a\u003a${uploadBatchKey}`, {
      slashSnapshot: formatApComposerNotice(
        apReceiptSignalRef.current,
        apTablePresetRef.current,
      ),
      text: typed,
    })
    setInput('')
  }

  const buildZhUploadCaptionPrefixedAp = (
    zhCaption: string,
    modeHint: ProcessingMode,
  ): string => {
    if (modeHint !== 'AP') return zhCaption
    const slash = formatApComposerNotice(apReceiptSignalRef.current, apTablePresetRef.current)
    return slash ? `${slash}\n\n${zhCaption}` : zhCaption
  }

  // ─── File upload: creates a NEW task ──────────────────────────────────────
  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    console.log('[UPLOAD] handleFileUpload called, files:', files?.length, 'mode:', processingMode, 'tasks:', tasks.length)
    if (!files || files.length === 0) return
    const fileArray = Array.from(files)
    console.log('[UPLOAD] file names:', fileArray.map(f => f.name))

    if (processingMode === 'RECON') {
      addMessageBeforeSpreadsheet({
        id: `recon-upload-blocked-${Date.now()}`, role: 'assistant',
        content: 'RECON mode does not accept file uploads. Switch to BANK to upload statements, or start reconciliation directly.'
      })
      if (fileInputRef.current) fileInputRef.current.value = ''
      return
    }

    if (processingMode === 'REPORT') {
      setMessages(prev => [...prev, {
        id: `report-upload-blocked-${Date.now()}`, role: 'assistant',
        content: 'REPORT mode does not accept file uploads. Exit REPORT mode first.',
      }])
      if (fileInputRef.current) fileInputRef.current.value = ''
      return
    }

    if (processingMode === 'AP' && uploadNeedsApComposer(fileArray)) {
      if (blockApUntilComposerOptionsChosen()) {
        if (fileInputRef.current) fileInputRef.current.value = ''
        return
      }
    }

    const ocrUploadBatchId = makeChatRecordId()
    const newQueue: QueuedFile[] = fileArray.map((file, index) => {
      const previewUrl = URL.createObjectURL(file)
      previewUrlsRef.current.add(previewUrl)
      return { id: `f-${Date.now()}-${index}`, file, status: 'pending', previewUrl, processingMode, uploadBatchId: ocrUploadBatchId }
    })
    const queueHasCsv = fileArray.some(f => isCsvFileName(f.name))
    const queueMessage: Message = {
      id: `q-${Date.now()}`, role: 'assistant',
      content: queueHasCsv && (processingMode === 'AP' || processingMode === 'AR')
        ? `Queued ${fileArray.length} file(s). Starting CSV import / OCR...`
        : `Queued ${fileArray.length} file(s). Starting OCR...`,
    }
    const userMessage: Message = {
      id: `u-${Date.now()}`, role: 'user',
      content: buildZhUploadCaptionPrefixedAp(`Uploaded ${fileArray.length} file(s)`, processingMode),
      uploadedFiles: newQueue,
    }

    if (acceptsCsvUpload(processingMode)) {
      const csvErr = multiCsvGuardMessage(fileArray)
      if (csvErr) {
        const errorMsg: Message = {
          id: `csv-error-${Date.now()}`, role: 'assistant', content: csvErr,
        }
        const now = new Date()
        const newTask: ChatTask = {
          id: makeChatRecordId(),
          title: `${fileArray.length} file(s) - ${now.toLocaleDateString('en-GB', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}`,
          createdAt: now.toISOString(),
          status: 'failed',
          processingMode,
          messages: [userMessage, errorMsg],
          fileQueue: newQueue.map(f => ({ ...f, status: 'failed' as const })),
          fileCount: fileArray.length,
          pageCount: 0,
          hasSpreadsheet: false,
        }
        await addPersistedTask(newTask)
        if (fileInputRef.current) fileInputRef.current.value = ''
        return
      }
    }

    // ── Same-day append: if a task already exists today for this mode, append to it ──
    if (processingMode !== 'BANK') {
      const todayStr = new Date().toDateString()
      const serverKnown = lastSuccessfulServerTaskIdsRef.current
      const todayTask = tasks.find(t =>
        t.processingMode === processingMode &&
        new Date(t.createdAt).toDateString() === todayStr &&
        t.status !== 'failed' &&
        (serverKnown?.has(t.id) || pendingLocalTaskIdsRef.current.has(t.id))
      )
      if (todayTask) {
        // Switch to today's task and append files to its queue
        const appendBatchId = makeChatRecordId()
        const appendedQueue: QueuedFile[] = fileArray.map((file, index) => {
          const previewUrl = URL.createObjectURL(file)
          previewUrlsRef.current.add(previewUrl)
          return { id: `f-${Date.now()}-${index}`, file, status: 'pending' as const, previewUrl, processingMode, uploadBatchId: appendBatchId }
        })
        const userMsg: Message = {
          id: `u-${Date.now()}`, role: 'user',
          content: buildZhUploadCaptionPrefixedAp(`Added ${fileArray.length} file(s)`, processingMode),
          uploadedFiles: appendedQueue,
        }
        const qMsg: Message = {
          id: `q-${Date.now()}`, role: 'assistant',
          content: `Queued ${fileArray.length} file(s). Starting OCR...`,
        }
        const prevProcessing = tasks.filter(t => t.status === 'processing').length
        const willQueueToday = prevProcessing >= MAX_CONCURRENT_TASKS
        const overloadToday = ocrScanOverloadInfo(todayTask.fileQueue, fileArray.length, willQueueToday, totalOcrProcessingNonBankTasks(tasks))
        if (overloadToday.open) setOcrOverloadModal({ ocr: overloadToday.ocr, task: overloadToday.task })
        const newStatus: TaskStatus = prevProcessing < MAX_CONCURRENT_TASKS ? 'processing' : 'queued'
        setTasks(prev => prev.map(t => {
          if (t.id !== todayTask.id) return t
          return {
            ...t,
            status: newStatus,
            fileCount: t.fileCount + fileArray.length,
            fileQueue: [...t.fileQueue, ...appendedQueue],
            messages: [...t.messages, userMsg, qMsg],
          }
        }))
        assignActiveTaskId(todayTask.id)
        patchTaskMetadataFireAndForget(todayTask.id, { file_count: todayTask.fileCount + fileArray.length, status: newStatus }, activeCompany?.id)
        stashApQueuedFollowUpIfTyped(todayTask.id, appendBatchId)
        if (fileInputRef.current) fileInputRef.current.value = ''
        return
      }
    }

    if (processingMode === 'BANK') {
      // L1: Check for duplicate file names across all existing BANK tasks
      // Check BOTH completed bankFilename AND pending/processing file queue names
      const allBankFiles: { taskTitle: string; fileName: string }[] = []
      for (const t of tasks) {
        if (t.processingMode !== 'BANK') continue
        const seen = new Set<string>()
        for (const m of t.messages) {
          if (m.bankFilename && !seen.has(m.bankFilename)) {
            seen.add(m.bankFilename)
            allBankFiles.push({ taskTitle: t.title, fileName: m.bankFilename })
          }
        }
        for (const f of t.fileQueue) {
          if (f.file?.name && !seen.has(f.file.name)) {
            seen.add(f.file.name)
            allBankFiles.push({ taskTitle: t.title, fileName: f.file.name })
          }
        }
      }
      console.log('[UPLOAD-DUP] existing bank files:', allBankFiles.map(bf => bf.fileName), 'uploading:', fileArray.map(f => f.name))
      const dupFiles = fileArray.filter(f => allBankFiles.some(bf => bf.fileName === f.name))
      console.log('[UPLOAD-DUP] dupFiles found:', dupFiles.length, dupFiles.map(f => f.name))

      if (dupFiles.length > 0) {
        const dupNames = dupFiles.map(f => f.name).join(', ')
        const matchedTasks = [...new Set(dupFiles.flatMap(f =>
          allBankFiles.filter(bf => bf.fileName === f.name).map(bf => bf.taskTitle)
        ))]
        const confirmId = `dup-confirm-${Date.now()}`
        pendingDupUploadRef.current = { confirmId, files: fileArray, source: 'new' }
        const warningMsg: Message = {
          id: `dup-warn-${Date.now()}`, role: 'assistant',
          content: `[Duplicate file] ${dupNames} already exists in: ${matchedTasks.join(', ')}.\nConfirm if you still want to upload.`,
          dupAlertType: 'warn',
          dupConfirmPending: true,
          dupConfirmId: confirmId,
          dupFileNames: dupNames,
        }
        const now = new Date()
        const newTask: ChatTask = {
          id: makeChatRecordId(),
          title: `${fileArray.length} file(s) - ${now.toLocaleDateString('en-GB', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}`,
          createdAt: now.toISOString(),
          status: 'completed',
          processingMode,
          messages: [{ ...userMessage, uploadedFiles: undefined }, warningMsg],
          fileQueue: [],
          fileCount: fileArray.length,
          pageCount: 0,
          hasSpreadsheet: false,
          dupWarning: `Duplicate file detected: ${dupNames}`,
        }
        await addPersistedTask(newTask)
        if (fileInputRef.current) fileInputRef.current.value = ''
        return
      }

      {
        const processingCountUp = tasksRef.current.filter(t => t.status === 'processing').length
        const willQueueUp = processingCountUp >= MAX_CONCURRENT_TASKS
        const overloadUp = ocrScanOverloadInfo([], newQueue.length, willQueueUp, totalOcrProcessingNonBankTasks(tasksRef.current))
        if (overloadUp.open) setOcrOverloadModal({ ocr: overloadUp.ocr, task: overloadUp.task })
        const newTask = createNewTask(processingMode, [userMessage, queueMessage], newQueue, fileArray.length)
        await addPersistedTask(newTask)
      }
      if (fileInputRef.current) fileInputRef.current.value = ''
      return
    }

    {
      const processingCountUp = tasksRef.current.filter(t => t.status === 'processing').length
      const willQueueUp = processingCountUp >= MAX_CONCURRENT_TASKS
      const overloadUp = ocrScanOverloadInfo([], newQueue.length, willQueueUp, totalOcrProcessingNonBankTasks(tasksRef.current))
      if (overloadUp.open) setOcrOverloadModal({ ocr: overloadUp.ocr, task: overloadUp.task })
      const newTask = createNewTask(processingMode, [userMessage, queueMessage], newQueue, fileArray.length)
      const persisted = await addPersistedTask(newTask)
      if (persisted && processingMode === 'AP') {
        stashApQueuedFollowUpIfTyped(newTask.id, ocrUploadBatchId)
      }
    }
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  // ─── Attach file: adds to the ACTIVE task ─────────────────────────────────
  const handleAttachFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    console.log('[ATTACH] handleAttachFile called, activeTaskId:', activeTaskId, 'mode:', processingMode)
    const files = e.target.files
    if (!files || files.length === 0) return
    const fileArray = Array.from(files)

    if (!activeTaskId) {
      // No active task — behave like a new upload
      handleFileUpload(e)
      return
    }

    const activeTaskIsServerBacked =
      lastSuccessfulServerTaskIdsRef.current?.has(activeTaskId) ||
      pendingLocalTaskIdsRef.current.has(activeTaskId)
    if (!activeTaskIsServerBacked) {
      assignActiveTaskId(null)
      handleFileUpload(e)
      return
    }

    if (processingMode === 'RECON') {
      addMessageBeforeSpreadsheet({
        id: `recon-upload-blocked-${Date.now()}`, role: 'assistant',
        content: 'RECON mode does not accept file uploads.'
      })
      if (attachFileInputRef.current) attachFileInputRef.current.value = ''
      return
    }

    if (processingMode === 'REPORT') {
      setMessages(prev => [...prev, {
        id: `report-upload-blocked-${Date.now()}`, role: 'assistant',
        content: 'REPORT mode does not accept file uploads. Exit REPORT mode first.',
      }])
      if (attachFileInputRef.current) attachFileInputRef.current.value = ''
      return
    }

    if (processingMode === 'AP' && uploadNeedsApComposer(fileArray)) {
      if (blockApUntilComposerOptionsChosen()) {
        if (attachFileInputRef.current) attachFileInputRef.current.value = ''
        return
      }
    }

    if (acceptsCsvUpload(processingMode)) {
      const csvErr = multiCsvGuardMessage(fileArray)
      if (csvErr) {
        addMessageBeforeSpreadsheet({
          id: `csv-error-${Date.now()}`, role: 'assistant', content: csvErr,
        })
        if (attachFileInputRef.current) attachFileInputRef.current.value = ''
        return
      }
    }

    // L1: Check for duplicate file names in BANK mode
    // Check BOTH completed bankFilename AND pending/processing file queue names
    if (processingMode === 'BANK') {
      const allBankFiles: { taskTitle: string; fileName: string }[] = []
      for (const t of tasks) {
        if (t.processingMode !== 'BANK') continue
        const seen = new Set<string>()
        for (const m of t.messages) {
          if (m.bankFilename && !seen.has(m.bankFilename)) {
            seen.add(m.bankFilename)
            allBankFiles.push({ taskTitle: t.title, fileName: m.bankFilename })
          }
        }
        for (const f of t.fileQueue) {
          if (f.file?.name && !seen.has(f.file.name)) {
            seen.add(f.file.name)
            allBankFiles.push({ taskTitle: t.title, fileName: f.file.name })
          }
        }
      }
      const dupFiles = fileArray.filter(f => allBankFiles.some(bf => bf.fileName === f.name))
      if (dupFiles.length > 0) {
        const dupNames = dupFiles.map(f => f.name).join(', ')
        const matchedTasks = [...new Set(dupFiles.flatMap(f =>
          allBankFiles.filter(bf => bf.fileName === f.name).map(bf => bf.taskTitle)
        ))]
        const confirmId = `dup-confirm-${Date.now()}`
        pendingDupUploadRef.current = { confirmId, files: fileArray, source: 'attach', taskId: activeTaskId }
        const userMsg: Message = {
          id: `u-${Date.now()}`, role: 'user',
          content: `Selected ${fileArray.length} file(s) (pending confirmation)`,
        }
        const warningMsg: Message = {
          id: `dup-warn-${Date.now()}`, role: 'assistant',
          content: `[Duplicate file] ${dupNames} already exists in: ${matchedTasks.join(', ')}.\nConfirm if you still want to upload.`,
          dupAlertType: 'warn',
          dupConfirmPending: true,
          dupConfirmId: confirmId,
          dupFileNames: dupNames,
        }
        updateTask(activeTaskId, t => ({
          ...t,
          messages: [...t.messages, userMsg, warningMsg],
          dupWarning: `Duplicate file detected: ${dupNames}`,
        }))
        if (attachFileInputRef.current) attachFileInputRef.current.value = ''
        return
      }
    }

    const attachBatchId = makeChatRecordId()
    const newQueue: QueuedFile[] = fileArray.map((file, index) => {
      const previewUrl = URL.createObjectURL(file)
      previewUrlsRef.current.add(previewUrl)
      return { id: `f-${Date.now()}-${index}`, file, status: 'pending', previewUrl, processingMode, uploadBatchId: attachBatchId }
    })

    // Determine the correct status respecting the concurrency limit
    const currentProcessingCount = tasks.filter(t => t.status === 'processing').length
    const isActiveTaskAlreadyProcessing = tasks.find(t => t.id === activeTaskId)?.status === 'processing'
    const needsSlot = !isActiveTaskAlreadyProcessing
    const willQueue = needsSlot && currentProcessingCount >= MAX_CONCURRENT_TASKS

    const userMessage: Message = {
      id: `u-${Date.now()}`, role: 'user',
      content: buildZhUploadCaptionPrefixedAp(`Uploaded ${fileArray.length} file(s) (continued)`, processingMode),
      uploadedFiles: newQueue,
    }
    const baseQueueContent = willQueue
      ? `Queued ${fileArray.length} file(s). ${currentProcessingCount} job(s) are already running; this task will wait.`
      : `Queued ${fileArray.length} file(s). Starting OCR...`
    const queueMsg: Message = {
      id: `q-${Date.now()}`, role: 'assistant',
      content: baseQueueContent,
    }
    const queueNoticeMsg: Message | null = willQueue
      ? { id: `queue-notice-${Date.now()}`, role: 'assistant', content: '__QUEUE_NOTICE__' }
      : null

    const taskSnapAttach = activeTaskId ? tasksRef.current.find(t => t.id === activeTaskId) : null
    const overloadAttach = ocrScanOverloadInfo(taskSnapAttach?.fileQueue ?? [], newQueue.length, willQueue, totalOcrProcessingNonBankTasks(tasksRef.current))
    if (overloadAttach.open) setOcrOverloadModal({ ocr: overloadAttach.ocr, task: overloadAttach.task })

    // Reset inactivity timer and reminder flag on each new upload
    lastOcrUploadTimeRef.current = Date.now()
    ocrStageReminderSentRef.current = false

    updateTask(activeTaskId, t => {
      const newStatus = (t.status === 'idle' || t.status === 'completed' || t.status === 'failed')
        ? (willQueue ? 'queued' as TaskStatus : 'processing' as TaskStatus)
        : t.status
      const baseMsgs = [...t.messages, userMessage, queueMsg]
      const msgs = queueNoticeMsg ? [...baseMsgs, queueNoticeMsg] : baseMsgs
      const updatedMode = (t.status === 'idle' || t.status === 'completed' || t.status === 'failed')
        ? processingMode
        : t.processingMode
      const newFileCount = t.fileCount + fileArray.length
      const dateStr = new Date(t.createdAt).toLocaleDateString('zh-TW', { month: '2-digit', day: '2-digit' })
      const updatedTitle = `${newFileCount} file(s).(${newFileCount}).${updatedMode}.${dateStr}`
      return {
        ...t,
        processingMode: updatedMode,
        fileQueue: [...t.fileQueue, ...newQueue],
        fileCount: newFileCount,
        title: updatedTitle,
        messages: msgs,
        status: newStatus,
      }
    })

    if (activeTaskId && processingMode === 'AP') {
      stashApQueuedFollowUpIfTyped(activeTaskId, attachBatchId)
    }

    if (attachFileInputRef.current) attachFileInputRef.current.value = ''
  }

  /** OS file drop on composer — same path as paperclip / attach file input */
  const handleComposerDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }

  const handleComposerDrop = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    const { files } = e.dataTransfer
    if (!files || files.length === 0) return
    const synthetic = { target: { files } } as unknown as React.ChangeEvent<HTMLInputElement>
    void handleAttachFile(synthetic)
  }

  /** Apply OCR result after a background job completes (e.g. user refreshed mid-OCR). */
  const commitOcrResultToTask = (
    taskId: string,
    queuedFileId: string,
    fileName: string,
    result: any,
    progressMessageId?: string,
  ) => {
    const mapUploadedFiles = (msgs: Message[]) =>
      msgs.map(msg =>
        msg.uploadedFiles
          ? {
              ...msg,
              uploadedFiles: msg.uploadedFiles.map(f =>
                f.id === queuedFileId ? { ...f, status: 'completed' as const, result } : f,
              ),
            }
          : msg,
      )

    updateTask(taskId, t => {
      let messages = t.messages
      if (progressMessageId) {
        messages = messages.filter(m => m.id !== progressMessageId)
      }

      if (result?.gate_result && result.gate_result !== 'TRANSACTIONAL') {
        const gateResult = result.gate_result as string
        const gateMsg = result.gate_message as string
        const docSubtype = result.gate_document_subtype as string | undefined
        const ocrText = result.ocr_text as string | undefined
        const gateCardId = `gate-${Date.now()}`
        const placeholderFile = new File([], fileName, { type: 'application/octet-stream' })
        const gateMessage: Message = {
          id: gateCardId,
          role: 'assistant',
          content: '',
          gateCard: {
            gateResult,
            gateMessage: gateMsg,
            fileName,
            documentSubtype: docSubtype || 'loan',
            ocrText: ocrText || '',
            sourceTaskId: taskId,
            processingMode: t.processingMode || 'AR',
            originalFile: placeholderFile,
          },
        }
        messages = [...messages, gateMessage]
        messages = mapUploadedFiles(messages)
        return { ...t, messages, fileQueue: t.fileQueue }
      }

  const isMultiPage = result.document_type === 'multi_page_pdf' && result.pages && result.pages.length > 0
  let ocrContent = `--- ${fileName} ---\nOCR complete\n\n`
  const rawOcr = result.raw_ocr || result
  if (rawOcr.text) {
    ocrContent += `Raw OCR text (${rawOcr.lines?.length || result.lines?.length || 0} line(s)):\n\`\`\`\n`
    ocrContent += rawOcr.text.substring(0, 1000)
    if (rawOcr.text.length > 1000) ocrContent += `\n... (${rawOcr.text.length} characters)`
    ocrContent += '\n```\n\n'
  }
  const ocrMessage: Message = { id: makeChatRecordId(), role: 'assistant', content: ocrContent, ocrResult: result }

  let aiContent = `AI processing complete\n\n`
  const pageData = isMultiPage ? result.pages?.[0] : result
  const fields = pageData?.ai_enhanced || pageData?.extracted_fields || {}
  const hasFields = Object.keys(fields).length > 0 && !fields.error
  const analysisSummary = fields?.analysis_summary || ''
  const contextMeta = fields?.context_meta || {}
  if (isMultiPage) {
    const outcome = (result as { ocr_job_outcome?: string }).ocr_job_outcome
    const errRows = Array.isArray(result.pages)
      ? result.pages.filter((p: { status?: string }) => p?.status === 'error').length
      : 0
    aiContent += `Pages: ${result.total_pages}\n`
    if (outcome === 'partial') {
      aiContent += `Some pages or blocks failed (${errRows} error(s)). See [OCR failed] rows; successful items are in the table.\n`
    } else if (outcome === 'failed') {
      aiContent += `Every page or block in this file failed. Check document quality or retry later.\n`
    } else {
      aiContent += `All pages processed. Details are in the table.\n`
    }
  } else if (hasFields) {
        aiContent += `AI extracted structured data into the table.\n`
        const confRaw = pageData?.field_confidence ?? fields.confidence
        if (confRaw !== undefined && confRaw !== null && String(confRaw).trim() !== '') {
          aiContent += `AI confidence: ${formatConfidenceDisplay(confRaw)}\n`
        }
        if (analysisSummary) aiContent += `\nAI analysis summary:\n${analysisSummary}\n`
        if (contextMeta?.company_context_used) {
          aiContent += `\nContext injected: company=${contextMeta.company_id || 'N/A'}, rules=${contextMeta.rule_count || 0}\n`
          if (Array.isArray(contextMeta.rule_names) && contextMeta.rule_names.length > 0) {
            aiContent += `Rule candidates: ${contextMeta.rule_names.slice(0, 3).join(', ')}\n`
          }
        }
        if (fields.warnings && fields.warnings.length > 0) {
          aiContent += `\nWarnings:\n`
          fields.warnings.forEach((warning: string) => { aiContent += `  - ${warning}\n` })
        }
      } else {
        aiContent += `AI could not extract structured fields. Check the raw OCR text or edit the table.`
      }

      const qfRow = t.fileQueue.find(f => f.id === queuedFileId)
      const pmCommit = String(qfRow?.processingMode || t.processingMode || 'AR')
      const multiFileSameUpload = arapUploadBatchPeerCount(t, queuedFileId) > 1
      const arapExtrasCommit = arapAttachmentsFromOcrCompletion({
        queuedFileId,
        fileName,
        result,
        processingMode: pmCommit,
        ocrBackgroundJobId: qfRow?.ocrJobId ?? null,
        apVlmTablePreset: pmCommit === 'AP' ? apTablePresetRef.current : undefined,
      })
      if (multiFileSameUpload && arapExtrasCommit && qfRow) {
        const batchId = qfRow.uploadBatchId ?? qfRow.id
        messages = upsertLocalBatchOcrSnapshotInMessages(
          messages,
          t,
          batchId,
          queuedFileId,
          arapExtrasCommit,
          pmCommit,
          false,
        )
      }
      const aiMessage: Message = {
        id: makeChatRecordId(),
        role: 'assistant',
        content: aiContent,
        ocrResult: result,
        ...(multiFileSameUpload ? {} : (arapExtrasCommit ?? {})),
      }
      messages = [...messages, ocrMessage, aiMessage]
      messages = mapUploadedFiles(messages)
      return {
        ...t,
        messages,
        fileQueue: t.fileQueue.map(f =>
          f.id === queuedFileId ? { ...f, status: 'completed' as const, result } : f,
        ),
      }
    })
  }

  const commitOcrResultToTaskRef = useRef(commitOcrResultToTask)
  commitOcrResultToTaskRef.current = commitOcrResultToTask

  // Resume background OCR / AI chat after full page reload (localStorage holds job id + meta).
  useEffect(() => {
    if (!user) return
    let cancelled = false

    const syncAiChatFromServer = async (taskId: string, companyId?: string | null) => {
      let serverMsgs: Awaited<ReturnType<typeof taskApi.getMessages>>
      try {
        serverMsgs = await taskApi.getMessages(taskId, companyId)
      } catch (err) {
        if ((err as { status?: number }).status === 404) {
          taskMessageSync404Ref.current.add(taskId)
          return
        }
        throw err
      }
      let metaFromServer: ChatTask | null = null
      try {
        const row = await taskApi.get(taskId, companyId)
        metaFromServer = serverTaskToFrontend(row)
      } catch {
        metaFromServer = null
      }
      if (!serverMsgs.length && !metaFromServer) return

      if (!serverMsgs.length) {
        setTasks(prev => {
          const next = prev.map(t => {
            if (t.id !== taskId) return t
            if (!metaFromServer) return t
            return {
              ...t,
              status: metaFromServer.status,
              fileCount: metaFromServer.fileCount,
              pageCount: metaFromServer.pageCount,
              hasSpreadsheet: metaFromServer.hasSpreadsheet,
              bankBatchIds: metaFromServer.bankBatchIds,
              ledgerBatchIds: metaFromServer.ledgerBatchIds,
              dupWarning: metaFromServer.dupWarning,
              titleGenerated: metaFromServer.titleGenerated,
              ...(metaFromServer.title ? { title: metaFromServer.title } : {}),
              processingMode: metaFromServer.processingMode,
            }
          })
          _writeLocalCache(next)
          return next
        })
        return
      }

      const mapped: Message[] = mapServerTaskMessagesToClient(serverMsgs)
      const prevRow = tasksRef.current.find(x => x.id === taskId)
      const reconciledChatPm = prevRow
        ? processingModeReconciledWithArapSnapshot(prevRow.processingMode, mapped)
        : null
      const needsChatFolderPatch = Boolean(
        prevRow && reconciledChatPm && reconciledChatPm !== prevRow.processingMode,
      )
      setTasks(prev => {
        const next = prev.map(t => {
          if (t.id !== taskId) return t
          const merged = mergeTaskMessagesFromServer(t.messages, mapped)
          let updated: ChatTask = {
            ...t,
            messages: merged,
          }
          if (needsChatFolderPatch && reconciledChatPm) {
            updated = { ...updated, processingMode: reconciledChatPm }
          }
          if (metaFromServer) {
            updated = {
              ...updated,
              status: metaFromServer.status,
              fileCount: metaFromServer.fileCount,
              pageCount: metaFromServer.pageCount,
              hasSpreadsheet: metaFromServer.hasSpreadsheet,
              bankBatchIds: metaFromServer.bankBatchIds,
              ledgerBatchIds: metaFromServer.ledgerBatchIds,
              dupWarning: metaFromServer.dupWarning,
              titleGenerated: metaFromServer.titleGenerated,
              ...(metaFromServer.title ? { title: metaFromServer.title } : {}),
            }
            if (!needsChatFolderPatch) {
              updated = { ...updated, processingMode: metaFromServer.processingMode }
            }
          }
          return updated
        })
        _writeLocalCache(next)
        return next
      })
      if (needsChatFolderPatch && reconciledChatPm) {
        patchTaskMetadataFireAndForget(taskId, { processing_mode: reconciledChatPm }, companyId)
      }
    }
    syncWorkspaceTaskFromServerRef.current = syncAiChatFromServer

    const pollUntilDone = async (storageKey: string, jobId: string, meta: Record<string, unknown>) => {
      if (pollingBackgroundJobIdsRef.current.has(jobId)) return
      pollingBackgroundJobIdsRef.current.add(jobId)
      try {
        const companyId = typeof meta.companyId === 'string' ? meta.companyId : undefined
        const result = await api.waitForBackgroundJob(jobId, { isCancelled: () => cancelled, companyId })
        if (cancelled) return
        if (meta.kind === 'ocr') {
          if (!tasksRef.current.some(t => t.id === meta.taskId)) {
            return
          }
          localStorage.removeItem(storageKey)
          commitOcrResultToTaskRef.current?.(
            meta.taskId as string,
            meta.queuedFileId as string,
            meta.fileName as string,
            result,
            meta.progressMessageId as string | undefined,
          )
        } else if (meta.kind === 'ai_chat') {
          if (!tasksRef.current.some(t => t.id === meta.taskId)) return
          localStorage.removeItem(storageKey)
          await syncAiChatFromServer(meta.taskId as string, companyId)
          setReconAiChatThinking(false)
        }
      } catch (e) {
        if (!cancelled) {
          localStorage.removeItem(storageKey)
          console.warn('[BgJob] resume failed', e)
          if (meta.kind === 'ai_chat') setReconAiChatThinking(false)
        }
      } finally {
        pollingBackgroundJobIdsRef.current.delete(jobId)
      }
    }

    const keys: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (k?.startsWith(BG_JOB_STORAGE_PREFIX)) keys.push(k)
    }

    for (const key of keys) {
      const jobId = key.slice(BG_JOB_STORAGE_PREFIX.length)
      const raw = localStorage.getItem(key)
      if (!raw) continue
      let meta: Record<string, unknown>
      try {
        meta = JSON.parse(raw) as Record<string, unknown>
      } catch {
        continue
      }
      const companyId = typeof meta.companyId === 'string' ? meta.companyId : undefined
      void api.getBackgroundJob(jobId, companyId).then(st => {
        if (cancelled) return
        if (st.status === 'failed') {
          localStorage.removeItem(key)
          if (meta.kind === 'ai_chat') setReconAiChatThinking(false)
          return
        }
        if (st.status === 'completed' && st.result_json) {
          if (meta.kind === 'ocr') {
            if (!tasksRef.current.some(t => t.id === meta.taskId)) {
              return
            }
            localStorage.removeItem(key)
            commitOcrResultToTaskRef.current?.(
              meta.taskId as string,
              meta.queuedFileId as string,
              meta.fileName as string,
              st.result_json,
              meta.progressMessageId as string | undefined,
            )
          } else if (meta.kind === 'ai_chat') {
            if (!tasksRef.current.some(t => t.id === meta.taskId)) return
            localStorage.removeItem(key)
            void syncAiChatFromServer(meta.taskId as string, companyId).finally(() => setReconAiChatThinking(false))
          }
          return
        }
        void pollUntilDone(key, jobId, meta)
      }).catch(() => {})
    }

    return () => {
      cancelled = true
      syncWorkspaceTaskFromServerRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- rescan bg job keys when workspace changes; updateTask/stable helpers via refs
  }, [user?.id, activeCompany?.id])

  // Cross-browser: poll company workspace activity and mirror remote progress into open tasks.
  useEffect(() => {
    if (!user) return
    let cancelled = false
    const tick = async () => {
      try {
        const act = await api.getWorkspaceActivity()
        if (cancelled) return
        const prevSnap = prevWorkspaceActivityKeysRef.current
        const current = new Map<string, string>()
        for (const b of act.bank_uploads) {
          current.set(`bank:${b.job_id}`, b.task_id)
        }
        for (const j of act.background_jobs) {
          if (j.task_id) current.set(`bg:${j.id}`, j.task_id)
        }
        for (const [k, tid] of prevSnap) {
          if (!current.has(k) && tid) {
            const jobId = k.startsWith('bank:') ? k.slice(5) : k.slice(3)
            const msgId = k.startsWith('bank:') ? `remote-bank-${jobId}` : `remote-bg-${jobId}`
            setTasks(prevTasks => prevTasks.map(t => {
              if (t.id !== tid) return t
              return { ...t, messages: t.messages.filter(m => m.id !== msgId) }
            }))
            const serverKnown = lastSuccessfulServerTaskIdsRef.current
            const maySyncMessages =
              !taskMessageSync404Ref.current.has(tid) &&
              (serverKnown === null ||
                serverKnown.has(tid) ||
                pendingLocalTaskIdsRef.current.has(tid))
            if (!maySyncMessages) continue
            void syncWorkspaceTaskFromServerRef.current?.(tid, activeCompany?.id).catch(err => {
              if ((err as { status?: number })?.status !== 404) {
                console.warn('[Tasks] Workspace activity sync failed:', err)
              }
            })
          }
        }
        prevWorkspaceActivityKeysRef.current = current

        const openTaskIds = new Set(tasksRef.current.map(t => t.id))
        for (const b of act.bank_uploads) {
          if (localBankUploadJobIdsRef.current.has(b.job_id)) continue
          if (!b.task_id || !openTaskIds.has(b.task_id)) continue
          const msgId = `remote-bank-${b.job_id}`
          const pct = Math.max(0, Math.min(100, Math.round(b.progress_percent)))
          const label = (b.label && b.label.trim()) ? b.label : 'BANK processing'
          const meta: Message['progressMeta'] = {
            fileIndex: 1,
            totalFiles: 1,
            processingFiles: 1,
            pageCurrent: Math.max(1, b.page_current || 1),
            pageTotal: Math.max(1, b.page_total || 1),
            ...(Object.keys(b.page_verification || {}).length > 0 ? { pageVerification: b.page_verification } : {}),
          }
          const msg: Message = {
            id: msgId,
            role: 'assistant',
            content: `Processing: ${b.filename} (${pct}%)`,
            progressPercent: pct,
            progressLabel: label,
            progressMeta: meta,
          }
          setTasks(prevTasks => prevTasks.map(t => {
            if (t.id !== b.task_id) return t
            const idx = t.messages.findIndex(m => m.id === msgId)
            if (idx >= 0) {
              const nm = [...t.messages]
              nm[idx] = { ...nm[idx], ...msg }
              return { ...t, messages: nm }
            }
            return { ...t, messages: [...t.messages, msg] }
          }))
        }
        for (const j of act.background_jobs) {
          if (!j.task_id || localBackgroundJobIdsRef.current.has(j.id)) continue
          if (!openTaskIds.has(j.task_id)) continue
          const msgId = `remote-bg-${j.id}`
          const name = (j.original_filename && j.original_filename.trim())
            ? j.original_filename
            : (j.job_type === 'ai_chat' ? 'AI chat' : 'OCR')
          const pctRaw = j.progress_percent
          const pct = typeof pctRaw === 'number' && Number.isFinite(pctRaw)
            ? Math.max(0, Math.min(100, Math.round(pctRaw)))
            : (j.status === 'queued' ? 8 : 45)
          const label = (j.progress_label && j.progress_label.trim()) ? j.progress_label : 'Processing'
          const meta: Message['progressMeta'] = {
            fileIndex: 1,
            totalFiles: 1,
            processingFiles: 1,
            pageCurrent: 1,
            pageTotal: 1,
          }
          const msg: Message = {
            id: msgId,
            role: 'assistant',
            content: `Processing: ${name} (${pct}%)`,
            progressPercent: pct,
            progressLabel: label,
            progressMeta: meta,
          }
          setTasks(prevTasks => prevTasks.map(t => {
            if (t.id !== j.task_id) return t
            let nextTask = t
            // Progressive AP/AR UX: if running OCR already has partial result_json,
            // surface it into the review table before the job completes.
            if (j.job_type === 'ocr' && j.result_json && typeof j.result_json === 'object') {
              const targetFile = t.fileQueue.find(f => f.ocrJobId === j.id)
              if (targetFile) {
                const pm = (targetFile.processingMode || t.processingMode || 'AR') as string
                const partialExtras = arapAttachmentsFromOcrCompletion({
                  queuedFileId: targetFile.id,
                  fileName: targetFile.file.name,
                  result: j.result_json,
                  processingMode: pm,
                  ocrBackgroundJobId: targetFile.ocrJobId ?? null,
                  apVlmTablePreset: pm === 'AP' ? apTablePresetRef.current : undefined,
                })
                const updatedQueue = t.fileQueue.map(f =>
                  f.ocrJobId === j.id && f.status === 'processing'
                    ? { ...f, result: j.result_json as Record<string, unknown> }
                    : f,
                )
                let nextMessages = nextTask.messages
                if (partialExtras?.spreadsheetData?.length) {
                  const multiRemote = arapUploadBatchPeerCount(t, targetFile.id) > 1
                  if (multiRemote) {
                    const batchId = targetFile.uploadBatchId ?? targetFile.id
                    nextMessages = upsertLocalBatchOcrSnapshotInMessages(
                      nextMessages,
                      { ...t, fileQueue: updatedQueue },
                      batchId,
                      targetFile.id,
                      partialExtras,
                      pm,
                      false,
                    )
                  } else {
                    const partialArap = spreadsheetRowsToArapTransactions(
                      partialExtras.spreadsheetData,
                      pm,
                    )
                    const apPresetSlice =
                      pm === 'AP'
                        ? { apVlmTablePreset: (apTablePresetRef.current ?? 'default') as ApVlmTablePreset }
                        : {}
                    nextMessages = nextMessages.map(m => {
                      const isLocalProgress = m.progressJob?.kind === 'ocr' && m.progressJob?.jobId === j.id
                      if (!isLocalProgress) return m
                      return {
                        ...m,
                        arapTransactions: partialArap,
                        arapFilename: targetFile.file.name,
                        ...apPresetSlice,
                      }
                    })
                  }
                }
                nextTask = {
                  ...nextTask,
                  fileQueue: updatedQueue,
                  messages: nextMessages,
                }
              }
            }
            const idx = nextTask.messages.findIndex(m => m.id === msgId)
            if (idx >= 0) {
              const nm = [...nextTask.messages]
              nm[idx] = { ...nm[idx], ...msg }
              return { ...nextTask, messages: nm }
            }
            return { ...nextTask, messages: [...nextTask.messages, msg] }
          }))
        }
      } catch {
        /* ignore transient poll errors */
      }
    }
    /** One outstanding activity poll — overlapping intervals exhaust browser connection slots and stall task create. */
    let timeoutId: ReturnType<typeof window.setTimeout> | undefined
    const loop = async () => {
      if (cancelled) return
      await tick()
      if (!cancelled) timeoutId = window.setTimeout(loop, 2000)
    }
    void loop()
    return () => {
      cancelled = true
      if (timeoutId !== undefined) window.clearTimeout(timeoutId)
    }
  }, [user?.id])

  // ─── Process a single file (task-specific) ────────────────────────────────
  const processFile = async (
    taskId: string,
    queuedFile: QueuedFile
  ) => {
    // Local shims targeting this specific task
    const setMsgsInTask = (updater: Message[] | ((prev: Message[]) => Message[])) => {
      setTasks(prev => prev.map(t => {
        if (t.id !== taskId) return t
        const newMsgs = typeof updater === 'function' ? updater(t.messages) : updater
        return { ...t, messages: newMsgs }
      }))
    }
    const setFqInTask = (updater: QueuedFile[] | ((prev: QueuedFile[]) => QueuedFile[])) => {
      setTasks(prev => prev.map(t => {
        if (t.id !== taskId) return t
        const newFq = typeof updater === 'function' ? updater(t.fileQueue) : updater
        return { ...t, fileQueue: newFq }
      }))
    }
    const addMsgBeforeSpreadsheet = (msg: Message) => {
      setMsgsInTask(prev => [...prev, msg])
    }
    const upsertProgressMsg = (
      messageId: string, fileName: string, percent: number,
      label: string, progressMeta?: Message['progressMeta'] | (() => Message['progressMeta'])
    ) => {
      const safePercent = Math.max(0, Math.min(100, Math.round(percent)))
      const baseMeta = typeof progressMeta === 'function' ? progressMeta() : progressMeta
      const resolvedMeta = baseMeta && typeof baseMeta.pageTotal === 'number'
        ? { ...baseMeta, pageCurrent: baseMeta.pageCurrent ?? Math.max(1, Math.min(baseMeta.pageTotal, Math.round((safePercent / 100) * baseMeta.pageTotal))) }
        : baseMeta
      setMsgsInTask(prev => {
        const nextMessage: Message = {
          id: messageId, role: 'assistant',
          content: `Processing: ${fileName} (${safePercent}%)`,
          progressPercent: safePercent, progressLabel: label, progressMeta: resolvedMeta
        }
        // Update in-place if the progress message already exists (keeps its position)
        const existingIndex = prev.findIndex(m => m.id === messageId)
        if (existingIndex !== -1) {
          const updated = [...prev]; updated[existingIndex] = { ...updated[existingIndex], ...nextMessage }; return updated
        }
        return [...prev, nextMessage]
      })
    }
    const startProgressTkr = (
      messageId: string, fileName: string, startPercent: number,
      targetPercent: number, label: string, step: number = 10,
      intervalMs: number = 650,
      progressMeta?: Message['progressMeta'] | (() => Message['progressMeta'])
    ) => {
      let current = Math.max(0, Math.min(100, Math.round(startPercent)))
      const target = Math.max(current, Math.min(100, Math.round(targetPercent)))
      const firstMeta = typeof progressMeta === 'function' ? progressMeta() : progressMeta
      let pageCurrent = firstMeta && typeof firstMeta.pageTotal === 'number' ? (firstMeta.pageCurrent ?? 1) : undefined
      upsertProgressMsg(messageId, fileName, current, label, firstMeta)
      const timer = window.setInterval(() => {
        current = Math.min(target, current + step)
        const tickMeta = typeof progressMeta === 'function' ? progressMeta() : progressMeta
        let nextMeta = tickMeta
        if (tickMeta && typeof tickMeta.pageTotal === 'number') {
          const safeTotal = Math.max(1, tickMeta.pageTotal)
          pageCurrent = Math.min(safeTotal, (pageCurrent ?? 1) + 1)
          nextMeta = { ...tickMeta, pageCurrent }
        }
        upsertProgressMsg(messageId, fileName, current, label, nextMeta)
        if (current >= target) window.clearInterval(timer)
      }, intervalMs)
      return () => window.clearInterval(timer)
    }

    const progressMessageId = `progress-${queuedFile.id}`
    // Get queue snapshot at start
    const taskSnapshot = tasks.find(t => t.id === taskId)
    const queueIndex = Math.max(0, taskSnapshot?.fileQueue.findIndex((f) => f.id === queuedFile.id) ?? 0)
    const taskFileQueueLength = taskSnapshot?.fileQueue.length ?? 1
    const estimatedPages = await estimateFilePageCount(queuedFile.file)
    const progressBaseMeta = (pageCurrent?: number, pageTotalOverride?: number) =>
      buildProgressMeta(queueIndex + 1, Math.max(taskFileQueueLength, 1), pageCurrent, pageTotalOverride ?? estimatedPages)
    const taskCompanyId = queuedFile.companyId ?? activeCompany?.id

    let stopOcrTicker = () => {}
    let stopAiTicker = () => {}
    activeProcessingTasksRef.current.add(progressMessageId)

    try {
      setFqInTask((prev) => {
        const updated = prev.map((f) => f.id === queuedFile.id ? { ...f, status: 'processing' as const, companyId: taskCompanyId } : f)
        setMsgsInTask((msgs) => msgs.map((msg) =>
          msg.uploadedFiles
            ? { ...msg, uploadedFiles: msg.uploadedFiles.map((f) => f.id === queuedFile.id ? { ...f, status: 'processing' as const, companyId: taskCompanyId } : f) }
            : msg
        ))
        return updated
      })

      const processingMessage: Message = { id: `p-${Date.now()}`, role: 'assistant', content: `Processing: ${queuedFile.file.name}...` }
      setMsgsInTask((prev) => [...prev, processingMessage])

      upsertProgressMsg(progressMessageId, queuedFile.file.name, 5, 'Queued', () => progressBaseMeta())

      // Upload file to persistent backend storage concurrently with OCR (fast disk write).
      // The taskFileId is populated in the queue entry as soon as the upload completes —
      // finalizeTask reads it when building the snapshot after all files are done.
      taskApi.uploadFile(taskId, queuedFile.file, taskCompanyId)
        .then(uploaded => {
          setFqInTask(prev => prev.map(f => f.id === queuedFile.id ? { ...f, taskFileId: uploaded.id } : f))
        })
        .catch(err => console.warn('[Tasks] File storage upload failed:', err))

      // AP/AR CSV: parse locally into the same OCR result shape finalizeTask expects (no VLM).
      const fileMode = String(queuedFile.processingMode ?? processingModeRef.current ?? 'AR').toUpperCase()
      if (isCsvFileName(queuedFile.file.name) && (fileMode === 'AP' || fileMode === 'AR')) {
        upsertProgressMsg(progressMessageId, queuedFile.file.name, 40, 'Importing CSV', () => progressBaseMeta())
        const text = await queuedFile.file.text()
        const { result, rowCount } = parseArapCsvToOcrResult(
          text,
          fileMode as 'AP' | 'AR',
          queuedFile.file.name,
        )
        const arapExtrasFile = arapAttachmentsFromOcrCompletion({
          queuedFileId: queuedFile.id,
          fileName: queuedFile.file.name,
          result,
          processingMode: fileMode,
          ocrBackgroundJobId: null,
          apVlmTablePreset: fileMode === 'AP' ? apTablePresetRef.current : undefined,
        })
        const importMsg: Message = {
          id: makeChatRecordId(),
          role: 'assistant',
          content:
            `--- ${queuedFile.file.name} ---\nCSV import complete (no VLM)\nTransactions: ${rowCount}`,
          ...(arapExtrasFile ?? {}),
        }
        setMsgsInTask(prev => [...prev, importMsg])
        upsertProgressMsg(progressMessageId, queuedFile.file.name, 100, 'CSV import complete', () => progressBaseMeta(1, 1))
        setFqInTask(prev => {
          const updated = prev.map(f =>
            f.id === queuedFile.id ? { ...f, status: 'completed' as const, result } : f,
          )
          setMsgsInTask(msgs =>
            msgs.map(msg =>
              msg.uploadedFiles
                ? {
                    ...msg,
                    uploadedFiles: msg.uploadedFiles.map(f =>
                      f.id === queuedFile.id ? { ...f, status: 'completed' as const, result } : f,
                    ),
                  }
                : msg,
            ),
          )
          return updated
        })
        return
      }

      upsertProgressMsg(progressMessageId, queuedFile.file.name, 12, 'Uploading for OCR', () => progressBaseMeta())
      stopOcrTicker = startProgressTkr(progressMessageId, queuedFile.file.name, 12, 60, 'Uploading for OCR', 10, 650, () => progressBaseMeta())

      const runOcrJobTracked = async (file: File, mode: string | undefined, confirmed: boolean) => {
        const ocrMode = mode ?? processingModeRef.current
        let apOpts:
          | {
              apVlmReceiptSignal: ApVlmReceiptSignal
              apVlmTablePreset: ApVlmTablePreset
            }
          | undefined
        if (ocrMode === 'AP') {
          const rs = apReceiptSignalRef.current
          const tp = apTablePresetRef.current
          if (!hasFullApComposerOptions(rs, tp)) {
            console.warn('[AP OCR] Receipt layout and table style must both be selected before OCR can start.')
            throw new Error('AP OCR cancelled: incomplete composer options')
          }
          apOpts = {
            apVlmReceiptSignal: rs,
            apVlmTablePreset: tp,
          }
        }
        const { job_id } = await api.createOcrBackgroundJob(
          file,
          mode,
          confirmed,
          taskId,
          taskCompanyId,
          apOpts,
        )
        localBackgroundJobIdsRef.current.add(job_id)
        trackTabBackgroundJob(job_id)
        setFqInTask(prev => prev.map(f => f.id === queuedFile.id ? { ...f, ocrJobId: job_id } : f))
        setMsgsInTask(prev => prev.map(m =>
          m.id === progressMessageId
            ? { ...m, progressJob: { kind: 'ocr' as const, jobId: job_id, taskId, fileId: queuedFile.id } }
            : m
        ))
        const storageKey = BG_JOB_STORAGE_PREFIX + job_id
        localStorage.setItem(
          storageKey,
          JSON.stringify({
            kind: 'ocr',
            taskId,
            queuedFileId: queuedFile.id,
            fileName: queuedFile.file.name,
            progressMessageId,
            companyId: taskCompanyId,
            processingMode: queuedFile.processingMode ?? processingModeRef.current ?? 'AR',
          }),
        )
        const applyPartialResult = (st: BackgroundJobRecord) => {
          if (st.status !== 'running') return
          if (!st.result_json || typeof st.result_json !== 'object') return
          const pm = (queuedFile.processingMode || mode || processingModeRef.current || 'AR') as string
          const partialExtras = arapAttachmentsFromOcrCompletion({
            queuedFileId: queuedFile.id,
            fileName: queuedFile.file.name,
            result: st.result_json,
            processingMode: pm,
            ocrBackgroundJobId: job_id,
            apVlmTablePreset: pm === 'AP' ? apTablePresetRef.current : undefined,
          })
          if (!partialExtras?.spreadsheetData?.length) return
          const taskSnapPartial = tasksRef.current.find(tt => tt.id === taskId)
          const multiPartial =
            !!taskSnapPartial && arapUploadBatchPeerCount(taskSnapPartial, queuedFile.id) > 1
          setFqInTask(prev => prev.map(f =>
            f.id === queuedFile.id && f.status === 'processing'
              ? { ...f, result: st.result_json as Record<string, unknown> }
              : f,
          ))
          setMsgsInTask(prev => {
            if (multiPartial && taskSnapPartial) {
              const batchId = queuedFile.uploadBatchId ?? queuedFile.id
              return upsertLocalBatchOcrSnapshotInMessages(
                prev,
                taskSnapPartial,
                batchId,
                queuedFile.id,
                partialExtras,
                pm,
                false,
              )
            }
            const partialArap = spreadsheetRowsToArapTransactions(partialExtras.spreadsheetData, pm)
            return prev.map(m =>
              m.id === progressMessageId
                ? {
                    ...m,
                    arapTransactions: partialArap,
                    arapFilename: queuedFile.file.name,
                    ...(pm === 'AP'
                      ? { apVlmTablePreset: (apTablePresetRef.current ?? 'default') as ApVlmTablePreset }
                      : {}),
                  }
                : m,
            )
          })
        }
        try {
          return await api.waitForBackgroundJob(job_id, {
            companyId: taskCompanyId,
            onProgress: applyPartialResult,
          })
        } finally {
          if (tasksRef.current.some(t => t.id === taskId)) {
            localStorage.removeItem(storageKey)
          }
          localBackgroundJobIdsRef.current.delete(job_id)
          untrackTabBackgroundJob(job_id)
        }
      }

      const initialMultiConfirmed =
        (queuedFile.processingMode ?? processingModeRef.current) === 'AP' &&
        apReceiptSignalRef.current === 'multi_per_page'
      let result: any = await runOcrJobTracked(
        queuedFile.file,
        queuedFile.processingMode,
        initialMultiConfirmed,
      )
      // If the backend detected multiple receipts but OpenCV couldn't split them
      // automatically, re-submit with confirmed=true to trigger force-split.
      if (result?.needs_confirmation === true) {
        upsertProgressMsg(progressMessageId, queuedFile.file.name, 65, 'Multiple receipts detected, splitting', () => progressBaseMeta())
        result = await runOcrJobTracked(queuedFile.file, queuedFile.processingMode, true)
      }
      stopOcrTicker()

      // ── Document Gate: handle REFERENCE_FINANCIAL / NON_FINANCIAL / AMBIGUOUS ──
      if (result?.gate_result && result.gate_result !== 'TRANSACTIONAL') {
        const gateResult = result.gate_result as string
        const gateMsg = result.gate_message as string
        const docSubtype = result.gate_document_subtype as string | undefined
        const ocrText = result.ocr_text as string | undefined

        const gateCardId = `gate-${Date.now()}`
        const gateMessage: Message = {
          id: gateCardId,
          role: 'assistant',
          content: '',
          gateCard: {
            gateResult,
            gateMessage: gateMsg,
            fileName: queuedFile.file.name,
            documentSubtype: docSubtype || 'loan',
            ocrText: ocrText || '',
            sourceTaskId: taskId,
            processingMode: queuedFile.processingMode ?? processingModeRef.current,
            originalFile: queuedFile.file,
          },
        }
        setMsgsInTask(prev => [...prev, gateMessage])
        upsertProgressMsg(progressMessageId, queuedFile.file.name, 100, 'Document type pending', () => progressBaseMeta())
        return  // stop processing this file; user must respond to gate card
      }

      const totalPages = Number(result?.total_pages || result?.pages?.length || 1)
      upsertProgressMsg(progressMessageId, queuedFile.file.name, 68, 'OCR complete, AI analysing', () => progressBaseMeta(1, totalPages))
      stopAiTicker = startProgressTkr(progressMessageId, queuedFile.file.name, 68, 93, 'AI analysing', 10, 650, () => progressBaseMeta(1, totalPages))

      const isMultiPage = result.document_type === 'multi_page_pdf' && result.pages && result.pages.length > 0
      let ocrContent = `--- ${queuedFile.file.name} ---\nOCR complete\n\n`
      const rawOcr = result.raw_ocr || result
      if (rawOcr.text) {
        ocrContent += `Raw OCR text (${rawOcr.lines?.length || result.lines?.length || 0} line(s)):\n\`\`\`\n`
        ocrContent += rawOcr.text.substring(0, 1000)
        if (rawOcr.text.length > 1000) ocrContent += `\n... (${rawOcr.text.length} characters)`
        ocrContent += '\n```\n\n'
      }
      const ocrMessage: Message = { id: makeChatRecordId(), role: 'assistant', content: ocrContent, ocrResult: result }

      let aiContent = `AI processing complete\n\n`
      const pageData = isMultiPage ? result.pages?.[0] : result
      const fields = pageData?.ai_enhanced || pageData?.extracted_fields || {}
      const hasFields = Object.keys(fields).length > 0 && !fields.error
      const analysisSummary = fields?.analysis_summary || ''
      const contextMeta = fields?.context_meta || {}

      if (isMultiPage) {
        const outcome = (result as { ocr_job_outcome?: string }).ocr_job_outcome
        const errRows = Array.isArray(result.pages)
          ? result.pages.filter((p: { status?: string }) => p?.status === 'error').length
          : 0
        aiContent += `Pages: ${result.total_pages}\n`
        if (outcome === 'partial') {
          aiContent += `Some pages or blocks failed (${errRows} error(s)). See [OCR failed] rows; successful items are in the table.\n`
        } else if (outcome === 'failed') {
          aiContent += `Every page or block in this file failed. Check document quality or retry later.\n`
        } else {
          aiContent += `All pages processed. Details are in the table.\n`
        }
      } else {
        if (hasFields) {
          aiContent += `AI extracted structured data into the table.\n`
          const confRaw = pageData?.field_confidence ?? fields.confidence
          if (confRaw !== undefined && confRaw !== null && String(confRaw).trim() !== '') {
            aiContent += `AI confidence: ${formatConfidenceDisplay(confRaw)}\n`
          }
          if (analysisSummary) aiContent += `\nAI analysis summary:\n${analysisSummary}\n`
          if (contextMeta?.company_context_used) {
            aiContent += `\nContext injected: company=${contextMeta.company_id || 'N/A'}, rules=${contextMeta.rule_count || 0}\n`
            if (Array.isArray(contextMeta.rule_names) && contextMeta.rule_names.length > 0) aiContent += `Rule candidates: ${contextMeta.rule_names.slice(0, 3).join(', ')}\n`
          }
          if (fields.warnings && fields.warnings.length > 0) {
            aiContent += `\nWarnings:\n`
            fields.warnings.forEach((warning: string) => { aiContent += `  - ${warning}\n` })
          }
        } else {
          aiContent += `AI could not extract structured fields. Check the raw OCR text or edit the table.`
        }
      }

      const pmFile = String(queuedFile.processingMode ?? processingModeRef.current ?? 'AR')
      const taskSnapPeer = tasksRef.current.find(tt => tt.id === taskId)
      const multiFileSameUpload =
        !!taskSnapPeer && arapUploadBatchPeerCount(taskSnapPeer, queuedFile.id) > 1
      const arapExtrasFile = arapAttachmentsFromOcrCompletion({
        queuedFileId: queuedFile.id,
        fileName: queuedFile.file.name,
        result,
        processingMode: pmFile,
        ocrBackgroundJobId: queuedFile.ocrJobId ?? null,
        apVlmTablePreset: pmFile === 'AP' ? apTablePresetRef.current : undefined,
      })
      const aiMessage: Message = {
        id: makeChatRecordId(),
        role: 'assistant',
        content: aiContent,
        ocrResult: result,
        ...(multiFileSameUpload ? {} : (arapExtrasFile ?? {})),
      }
      setMsgsInTask((prev) => {
        let msgs = [...prev, ocrMessage, aiMessage]
        if (multiFileSameUpload && taskSnapPeer && arapExtrasFile) {
          const batchId = queuedFile.uploadBatchId ?? queuedFile.id
          msgs = upsertLocalBatchOcrSnapshotInMessages(
            msgs,
            taskSnapPeer,
            batchId,
            queuedFile.id,
            arapExtrasFile,
            pmFile,
            false,
          )
        }
        return msgs
      })
      stopAiTicker()
      upsertProgressMsg(progressMessageId, queuedFile.file.name, 97, 'Preparing results', () => progressBaseMeta(totalPages, totalPages))
      upsertProgressMsg(progressMessageId, queuedFile.file.name, 100, 'OCR + AI complete', () => progressBaseMeta(totalPages, totalPages))

      setFqInTask((prev) => {
        const updated = prev.map((f) => f.id === queuedFile.id ? { ...f, status: 'completed' as const, result } : f)
        setMsgsInTask((msgs) => msgs.map((msg) =>
          msg.uploadedFiles
            ? { ...msg, uploadedFiles: msg.uploadedFiles.map((f) => f.id === queuedFile.id ? { ...f, status: 'completed' as const, result } : f) }
            : msg
        ))
        return updated
      })
    } catch (error) {
      stopOcrTicker(); stopAiTicker()
      if (error instanceof DOMException && error.name === 'AbortError') {
        markUploadCancelled(taskId, queuedFile.id, progressMessageId)
        return
      }
      const errText = error instanceof Error ? error.message : 'Unknown error'
      const errId = `e-${Date.now()}`
      const errorMessage: Message = {
        id: errId,
        role: 'assistant',
        ocrErrorForFileId: queuedFile.id,
        content: `Error: ${queuedFile.file.name}\n${errText}`,
      }
      const afterRetry: Message[] =
        (queuedFile.ocrRetryCount ?? 0) > 0
          ? [{ id: `retry-err-hint-${Date.now()}`, role: 'assistant', content: 'Retry error file, please rescan the file' }]
          : []
      setMsgsInTask((prev) => [...prev, errorMessage, ...afterRetry])
      upsertProgressMsg(progressMessageId, queuedFile.file.name, 100, 'Failed', () => progressBaseMeta())
      setFqInTask((prev) => {
        const updated = prev.map((f) => f.id === queuedFile.id ? { ...f, status: 'failed' as const } : f)
        setMsgsInTask((msgs) => msgs.map((msg) =>
          msg.uploadedFiles
            ? { ...msg, uploadedFiles: msg.uploadedFiles.map((f) => f.id === queuedFile.id ? { ...f, status: 'failed' as const } : f) }
            : msg
        ))
        return updated
      })
    } finally {
      activeProcessingTasksRef.current.delete(progressMessageId)
    }
  }

  const invokeWorkspaceModeAiChatRound = async (opts: {
    chatTaskId: string
    taskCompanyId: string | null
    mode: ProcessingMode
    combinedMessageBody: string
    userMessage: Message
  }) => {
    const {
      chatTaskId,
      taskCompanyId,
      mode,
      combinedMessageBody,
      userMessage,
    } = opts
    const isBankChat = mode === 'BANK'
    const currentTaskSnapshot = tasksRef.current.find(t => t.id === chatTaskId)
    const currentArapTxns: ARAPTransaction[] = isBankChat
      ? []
      : (currentTaskSnapshot?.messages ?? []).flatMap(m => m.arapTransactions ?? [])
    const currentBankTxns = isBankChat
      ? (currentTaskSnapshot?.messages ?? []).flatMap(m => m.bankTransactions ?? [])
      : []

    const thinkingId = `think-${Date.now()}`
    const thinkingMsg: Message = { id: thinkingId, role: 'assistant', content: AI_CHAT_THINKING_PLACEHOLDER }

    setTasks(prev => prev.map(t => {
      if (t.id !== chatTaskId) return t
      return { ...t, messages: [...t.messages, userMessage, thinkingMsg] }
    }))
    setAiThinkingTaskIds(prev => new Set(prev).add(chatTaskId))

    try {
      const chatPayload = {
        session_id: `${chatTaskId}_${mode}`,
        mode,
        message: combinedMessageBody,
        context: {
          transactions: (isBankChat ? currentBankTxns : currentArapTxns) as unknown as Record<string, unknown>[],
          coa: coaList.map(c => ({
            code: c.code,
            name_en: c.name_en,
            name_zh: c.name_zh,
            category_type: c.category_type,
          })),
        },
      }
      const { job_id } = await api.createAiChatBackgroundJob(chatPayload, taskCompanyId)
      localBackgroundJobIdsRef.current.add(job_id)
      trackTabBackgroundJob(job_id)
      const sk = BG_JOB_STORAGE_PREFIX + job_id
      localStorage.setItem(sk, JSON.stringify({ kind: 'ai_chat', taskId: chatTaskId, isRecon: false, companyId: taskCompanyId }))
      let result: any
      try {
        result = await api.waitForBackgroundJob(job_id, { companyId: taskCompanyId })
      } finally {
        localStorage.removeItem(sk)
        localBackgroundJobIdsRef.current.delete(job_id)
        untrackTabBackgroundJob(job_id)
      }

      const tablePatches =
        Array.isArray(result.table_patches) ? result.table_patches : []

      setTasks(prev => prev.map(t => {
        if (t.id !== chatTaskId) return t
        const postedB = glPostedBankLockKeysRef.current
        const postedL = glPostedLedgerLockKeysRef.current
        const patchedMessages = tablePatches.length > 0
          ? t.messages.map(m => {
              if (isBankChat) {
                if (!m.bankTransactions) return m
                const patched = applyTablePatchesToRows(
                  m.bankTransactions,
                  tablePatches,
                  row => isBankRowGlPosted(row, postedB),
                )
                return { ...m, bankTransactions: patched }
              }
              if (!m.arapTransactions) return m
              const patched = applyTablePatchesToRows(
                m.arapTransactions,
                tablePatches,
                row => isLedgerRowGlPosted(row, postedL),
              )
              return { ...m, arapTransactions: patched }
            })
          : t.messages
        const finalMessages = patchedMessages.map(m =>
          m.id === thinkingId ? {
            ...m,
            content: '',
            isTyping: true,
            typingFullContent: result.reply,
            saveRulePending: result.save_rule_pending || false,
            saveRuleProposal: result.save_rule_proposal || null,
            ruleSaved: result.rule_saved || false,
            ruleSavedMessage: result.rule_saved_message || '',
            reconRedirect: result.recon_redirect ?? undefined,
          } : m
        )
        if (tablePatches.length > 0 && chatTaskId) {
          if (isBankChat) {
            for (const m of patchedMessages) {
              if (!m.bankTransactions?.length) continue
              const merged = mergeBankMessagesForOcrSnapshot([m])
              const content = mergedBankOcrSnapshotContent(merged, m.content ?? '')
              debouncedSaveSnapshot(chatTaskId, m.id, content, {
                spreadsheetData: merged.spreadsheetData,
                bankTransactions: merged.bankTransactions,
                bankFilename: merged.bankFilename,
                fileRefs: merged.fileRefs,
              }, taskCompanyId)
            }
          } else {
            patchedMessages.forEach(m => {
              if (m.arapTransactions) {
                debouncedSaveSnapshot(chatTaskId, m.id, m.content, {
                  spreadsheetData: m.spreadsheetData,
                  arapTransactions: m.arapTransactions,
                  arapFilename: m.arapFilename,
                  fileRefs: m.fileRefs,
                }, taskCompanyId)
              }
            })
          }
        }
        return { ...t, messages: finalMessages }
      }))
      generateAndSetTitle(chatTaskId, [
        userMessage,
        { id: `title-assistant-${Date.now()}`, role: 'assistant', content: result.reply },
      ], taskCompanyId)
    } catch (err) {
      console.error('[AiChat] Failed:', err)
      const errMsg = err instanceof Error ? err.message : String(err)
      setTasks(prev => prev.map(t => {
        if (t.id !== chatTaskId) return t
        return {
          ...t,
          messages: t.messages.map(m =>
            m.id === thinkingId
              ? { ...m, content: `Sorry, something went wrong: ${errMsg}` }
              : m
          ),
        }
      }))
    } finally {
      setAiThinkingTaskIds(prev => {
        const s = new Set(prev)
        s.delete(chatTaskId)
        return s
      })
    }
  }

  // ─── Finalize a task once batches are ready (one table per upload batch) ─
  const finalizeTask = async (taskId: string, isAllDone = false) => {
    if (finalizingTasksRef.current.has(taskId)) {
      return
    }
    finalizingTasksRef.current.add(taskId)

    const queueBatchKey = (f: QueuedFile) => f.uploadBatchId ?? f.id

    try {
      let task = tasks.find(t => t.id === taskId)

      const taskCompanyId = task.fileQueue.find(f => f.companyId)?.companyId ?? activeCompany?.id
      const completedFiles = task.fileQueue.filter(f => f.status === 'completed')
      const pendingComplete = completedFiles.filter(f => !f.addedToSpreadsheet)

      if (pendingComplete.length === 0) {
        if (isAllDone) {
          updateTask(taskId, t => ({ ...t, status: 'completed' as TaskStatus }))
          patchTaskMetadataFireAndForget(taskId, { status: 'completed' }, taskCompanyId)
        }
        return
      }

      const byBatch = new Map<string, QueuedFile[]>()
      for (const f of pendingComplete) {
        const k = queueBatchKey(f)
        if (!byBatch.has(k)) byBatch.set(k, [])
        byBatch.get(k)!.push(f)
      }

      const readyBatches: QueuedFile[][] = []
      for (const files of byBatch.values()) {
        const k = queueBatchKey(files[0])
        const batchMembers = task.fileQueue.filter(f => queueBatchKey(f) === k)
        const allTerminal = batchMembers.every(
          f => f.status === 'completed' || f.status === 'failed' || f.status === 'cancelled',
        )
        if (allTerminal) readyBatches.push(files)
      }

      if (readyBatches.length === 0) {
        if (isAllDone) {
          updateTask(taskId, t => ({ ...t, status: 'completed' as TaskStatus }))
          patchTaskMetadataFireAndForget(taskId, { status: 'completed' }, taskCompanyId)
        }
        return
      }

      let runningRowCount = task.messages.reduce((acc, m) => acc + (m.spreadsheetData?.length ?? 0), 0)
      let spreadsheetSnapshotAppendFailed = false

      for (const newCompletedFiles of readyBatches) {
        const batchKeyOne = queueBatchKey(newCompletedFiles[0])
        const batchFailed = task.fileQueue.filter(
          f => queueBatchKey(f) === batchKeyOne && f.status === 'failed',
        )

        let batchSpreadsheetPersisted = false

        const spreadsheetData: SpreadsheetRow[] = []
        let rowIndex = runningRowCount + 1
        for (const file of newCompletedFiles) {
          const pm = (file.processingMode || task.processingMode || 'AR') as string
          const { spreadsheetData: chunk, nextRowIndex } = buildSpreadsheetRowsFromOcrResult({
            fileId: file.id,
            fileName: file.file.name,
            result: file.result,
            processingMode: pm,
            rowIndexStart: rowIndex,
            ocrBackgroundJobId: file.ocrJobId ?? null,
          })
          spreadsheetData.push(...chunk)
          rowIndex = nextRowIndex
        }
        runningRowCount += spreadsheetData.length

        let ledgerBatchId: string | undefined
        if (!isBankMode(task.processingMode) && spreadsheetData.length > 0) {
          try {
            const ledgerRows = spreadsheetData.map(row => ({
              voucher_no: row.voucher_no, transaction_type: row.transaction_type,
              amount: row.amount, currency: row.currency, date: row.date,
              payer: row.payer, payee: row.payee, bank: row.bank,
              category: row.category, memo: row.memo, client_row_id: row.id
            }))
            const importResult = await reconciliationApi.importLedgerTransactions(ledgerRows)
            ledgerBatchId = importResult?.import_batch_id
            if (importResult?.created_rows?.length) {
              const rowIdMap = new Map<string, string>()
              importResult.created_rows.forEach((createdRow: any) => {
                if (createdRow?.client_row_id && createdRow?.id) rowIdMap.set(createdRow.client_row_id, createdRow.id)
              })
              spreadsheetData.forEach((row) => {
                const ledgerId = rowIdMap.get(row.id)
                if (ledgerId) row.ledger_txn_id = ledgerId
              })
            }
          } catch (error) {
            console.error('[Ledger Import] Failed to store ledger rows:', error)
          }
        }

        const isArap = !isBankMode(task.processingMode)
        const arapTxns: ARAPTransaction[] =
          isArap && spreadsheetData.length > 0
            ? spreadsheetRowsToArapTransactions(spreadsheetData, task.processingMode)
            : []

        let summaryContent = `Batch complete: ${newCompletedFiles.length} file(s) succeeded${batchFailed.length > 0 ? `, ${batchFailed.length} failed` : ''}\nProcessed ${spreadsheetData.length} record(s)\n\nEditable summary table — double-click a cell to edit:`
        if (batchFailed.length > 0) {
          summaryContent += `\n\nFailed files:\n`
          batchFailed.forEach((file, index) => { summaryContent += `${index + 1}. ${file.file.name}\n` })
        }

        const newFileRefs = newCompletedFiles
          .filter((f): f is typeof f & { taskFileId: string } => Boolean(f.taskFileId))
          .map(f => ({ id: f.taskFileId, name: f.file.name }))

        if (spreadsheetData.length > 0) {
          try {
            const serverMsg = await taskApi.appendMessage(
              taskId,
              {
                role: 'assistant',
                content_text: summaryContent,
                content_type: 'ocr_snapshot',
                payload_json: {
                  spreadsheetData,
                  arapTransactions: arapTxns,
                  arapFilename: newCompletedFiles.length === 1 ? newCompletedFiles[0].file.name : undefined,
                  fileRefs: newFileRefs,
                  ocrUploadBatchId: batchKeyOne,
                  ...(String(task.processingMode || '').toUpperCase() === 'AP'
                    ? { apVlmTablePreset: (apTablePresetRef.current ?? 'default') as ApVlmTablePreset }
                    : {}),
                },
              },
              taskCompanyId,
            )
            const [mappedMsg] = mapServerTaskMessagesToClient([serverMsg])
            updateTask(taskId, t => ({
              ...t,
              fileQueue: t.fileQueue.map(f =>
                newCompletedFiles.some(nf => nf.id === f.id) ? { ...f, addedToSpreadsheet: true as const } : f,
              ),
              messages: [
                ...removeLocalBatchOcrSnapshotMessages(t.messages, batchKeyOne),
                mappedMsg,
              ],
              spreadsheetData: [...(t.spreadsheetData || []), ...spreadsheetData],
              pageCount: runningRowCount,
              hasSpreadsheet: true,
              ledgerBatchIds: ledgerBatchId
                ? Array.from(new Set([...(t.ledgerBatchIds || []), ledgerBatchId]))
                : t.ledgerBatchIds,
            }))
            batchSpreadsheetPersisted = true
            patchTaskMetadataFireAndForget(taskId, { page_count: runningRowCount, has_spreadsheet: true }, taskCompanyId)
          } catch (err) {
            const errMsg = err instanceof Error ? err.message : String(err)
            console.warn('[Tasks] AR/AP OCR snapshot append failed:', err)
            spreadsheetSnapshotAppendFailed = true
            const failMsg: Message = {
              id: `ocr-snapshot-fail-${Date.now()}`,
              role: 'assistant',
              content:
                `Could not save the summary table to the server. Please try again.\n`
                + `Could not save consolidated table to server:\n${errMsg}`,
            }
            updateTask(taskId, t => ({
              ...t,
              fileQueue: t.fileQueue.map(f =>
                newCompletedFiles.some(nf => nf.id === f.id) ? { ...f, addedToSpreadsheet: true as const } : f,
              ),
              messages: [...t.messages, failMsg],
            }))
          }
        } else {
          updateTask(taskId, t => ({
            ...t,
            fileQueue: t.fileQueue.map(f =>
              newCompletedFiles.some(nf => nf.id === f.id) ? { ...f, addedToSpreadsheet: true as const } : f,
            ),
          }))
          batchSpreadsheetPersisted = true
          if (batchFailed.length > 0) {
            patchTaskMetadataFireAndForget(taskId, { page_count: runningRowCount, has_spreadsheet: task.hasSpreadsheet }, taskCompanyId)
          }
        }

        task = tasksRef.current.find(e => e.id === taskId) ?? task

        const taskAfterBatch = tasksRef.current.find(tt => tt.id === taskId)
        if (taskAfterBatch?.processingMode === 'AP') {
          const followMapKey = `${taskId}::${batchKeyOne}`
          const follow = pendingApFollowUpChatByBatchKeyRef.current.get(followMapKey)
          if (follow && batchSpreadsheetPersisted) {
            pendingApFollowUpChatByBatchKeyRef.current.delete(followMapKey)
            const body = `${follow.slashSnapshot}\n\n${follow.text}`.trim()
            void invokeWorkspaceModeAiChatRound({
              chatTaskId: taskId,
              taskCompanyId,
              mode: 'AP',
              combinedMessageBody: body,
              userMessage: { id: `u-follow-${Date.now()}`, role: 'user', content: body },
            })
          }
        }
      }

      if (isAllDone && !spreadsheetSnapshotAppendFailed) {
        updateTask(taskId, t => ({ ...t, status: 'completed' as TaskStatus }))
        patchTaskMetadataFireAndForget(taskId, { status: 'completed', has_spreadsheet: true }, taskCompanyId)
      }
    } finally {
      finalizingTasksRef.current.delete(taskId)
    }
  }

  // ─── Concurrency manager (replaces old single-file queue effect) ──────────
  useEffect(() => {
    // Step 1: Promote queued tasks to processing if slots are available
    // pendingLocalTaskIdsRef is list-merge bookkeeping only — do not block BANK/OCR after awaited persist (see persistTaskToServer).
    const processingTasks = tasks.filter(t => t.status === 'processing')
    const queuedTasks = tasks.filter(t => t.status === 'queued')
    const slots = MAX_CONCURRENT_TASKS - processingTasks.length

    if (slots > 0 && queuedTasks.length > 0) {
      const toStart = queuedTasks.slice(0, slots)
      toStart.forEach(task => {
        updateTask(task.id, t => ({
          ...t,
          status: 'processing' as TaskStatus,
          // Remove queue notice message
          messages: t.messages.filter(m => m.content !== '__QUEUE_NOTICE__')
        }))
      })
      return
    }

    // Step 2: For BANK mode tasks that just became processing, start bank upload
    processingTasks.forEach(task => {
      if (task.processingMode === 'BANK') {
        const pendingFiles = task.fileQueue.filter(f => f.status === 'pending' && !submittedFileIdsRef.current.has(f.id))
        if (pendingFiles.length > 0) {
          pendingFiles.forEach(f => submittedFileIdsRef.current.add(f.id))
          // Mark them as processing
          updateTask(task.id, t => ({
            ...t,
            fileQueue: t.fileQueue.map(f => pendingFiles.find(pf => pf.id === f.id) ? { ...f, status: 'processing' as const } : f)
          }))
          void handleBankStatementUpload(task.id, pendingFiles)
        }
      }
    })

    // Step 3: OCR tasks — per-task cap and workspace-wide cap (avoids 3 tasks × 4 files overloading the API).
    const ocrProcessingTasks = processingTasks
      .filter(t => t.processingMode !== 'BANK')
      .sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime())
    const totalOcrProcessing = ocrProcessingTasks.reduce(
      (s, t) => s + t.fileQueue.filter(f => f.status === 'processing').length,
      0,
    )
    let globalOcrBudget = Math.max(0, MAX_GLOBAL_CONCURRENT_OCR_FILES - totalOcrProcessing)
    ocrProcessingTasks.forEach(task => {
      const activeOcrCount = task.fileQueue.filter(f => f.status === 'processing').length
      const localAvailable = Math.max(0, MAX_CONCURRENT_OCR_FILES - activeOcrCount)
      const availableSlots = Math.min(localAvailable, globalOcrBudget)
      if (availableSlots > 0) {
        const pendingFiles = task.fileQueue.filter(
          f => f.status === 'pending' && !submittedFileIdsRef.current.has(f.id)
        )
        const toStart = pendingFiles.slice(0, availableSlots)
        toStart.forEach(f => {
          submittedFileIdsRef.current.add(f.id)
          void processFile(task.id, f)
        })
        globalOcrBudget -= toStart.length
      }

      const hasNewCompleted = task.fileQueue.some(f => f.status === 'completed' && !f.addedToSpreadsheet)
      const allDone = task.fileQueue.length > 0 && task.fileQueue.every(f => f.status === 'completed' || f.status === 'failed' || f.status === 'cancelled')
      if (!finalizingTasksRef.current.has(task.id)) {
        if (allDone) {
          void finalizeTask(task.id, true)
        } else if (hasNewCompleted) {
          void finalizeTask(task.id, false)
        }
      }
    })
  }, [tasks])

  // ─── Cleanup preview URLs on unmount ─────────────────────────────────────
  useEffect(() => {
    return () => {
      previewUrlsRef.current.forEach((url) => URL.revokeObjectURL(url))
      previewUrlsRef.current.clear()
    }
  }, [])

  // ─── Auto-scroll: only pin to bottom if user is already near the bottom ─────
  // Progress polls update `messages` in place; always jumping scroll broke review
  // of the bank/AR table while another file was still processing.
  useLayoutEffect(() => {
    if (suppressScrollRef.current) {
      suppressScrollRef.current = false
      return
    }
    const el = messageListRef.current
    if (!el) return
    const NEAR_BOTTOM_PX = 120
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight
    if (dist <= NEAR_BOTTOM_PX) {
      el.scrollTop = el.scrollHeight
    }
  }, [messages])

  // ─── Task completion notification ─────────────────────────────────────────
  useEffect(() => {
    const prev = prevTaskStatusesRef2.current
    tasks.forEach(task => {
      const prevStatus = prev.get(task.id)
      if (prevStatus === 'processing' && task.status === 'completed') {
        playCompletionSound()
        const notifId = `notif-${task.id}`
        setTaskNotifications(ns => [...ns.filter(n => n.id !== notifId), { id: notifId, title: task.title }])
        setTimeout(() => setTaskNotifications(ns => ns.filter(n => n.id !== notifId)), 5000)
      }
      prev.set(task.id, task.status)
    })
  }, [tasks])

  // ─── Close dropdown when clicking outside ────────────────────────────────
  useEffect(() => {
    if (!openMenuId) return
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      if (!target.closest('.record-menu')) setOpenMenuId(null)
    }
    const timeoutId = setTimeout(() => document.addEventListener('click', handleClickOutside), 0)
    return () => { clearTimeout(timeoutId); document.removeEventListener('click', handleClickOutside) }
  }, [openMenuId])

  // ─── Resize panel drag ────────────────────────────────────────────────────
  const handleSidebarResizeStart = (e: React.MouseEvent) => { e.preventDefault(); setIsSidebarResizing(true) }
  useResizeDrag(isSidebarResizing, setIsSidebarResizing, (x, w) => {
    setSidebarWidth(Math.min(Math.max(w - x, 200), 600))
  })

  const handleLeftPanelResizeStart = (e: React.MouseEvent) => { e.preventDefault(); setIsLeftPanelResizing(true) }
  useResizeDrag(isLeftPanelResizing, setIsLeftPanelResizing, (x) => {
    setLeftPanelWidth(Math.min(Math.max(x, 160), 480))
  })

  const handleRightPanelResizeStart = (e: React.MouseEvent) => { e.preventDefault(); setIsRightPanelResizing(true) }
  useResizeDrag(isRightPanelResizing, setIsRightPanelResizing, (x, w) => {
    setRightPanelWidth(Math.min(Math.max(w - x, 220), 600))
  })

  const handleComposerInputChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const next = event.target.value
    if (processingMode !== 'AP') {
      setInput(next)
      void ensureComposerTaskIfTyping(next)
      return
    }
    if (!hasFullApComposerOptions(apReceiptSignal, apTablePreset)) {
      setInput(next)
      void ensureComposerTaskIfTyping(next)
      return
    }
    const tid = activeTaskIdRef.current
    const trow = tid ? tasksRef.current.find(t => t.id === tid) : null
    const ocrBusy =
      trow?.fileQueue.some(f => f.status === 'pending' || f.status === 'processing') ?? false
    if (next.trim() && !ocrBusy) {
      void (async () => {
        apTypingBlockedPendingTextRef.current = next.trim()
        await ensureComposerTaskIfTyping(next)
        setApComposerDialog('typing_blocked')
      })().catch(() => {})
      return
    }
    setInput(next)
    void ensureComposerTaskIfTyping(next)
  }

  // ─── Chat send ────────────────────────────────────────────────────────────
  const handleSend = async () => {
    const trimmedTyping = input.trim()
    const apNotice =
      processingMode === 'AP' ? formatApComposerNotice(apReceiptSignal, apTablePreset) : ''

    if (processingMode === 'AP') {
      if (hasFullApComposerOptions(apReceiptSignal, apTablePreset)) {
        const tid = activeTaskIdRef.current
        const trow = tid ? tasksRef.current.find(t => t.id === tid) : null
        const ocrBusy =
          trow?.fileQueue.some(f => f.status === 'pending' || f.status === 'processing') ?? false
        if (!ocrBusy) {
          const combinedPreview = [apNotice, trimmedTyping].filter(Boolean).join('\n\n').trim()
          if (combinedPreview) {
            setApComposerDialog('typing_blocked')
            apTypingBlockedPendingTextRef.current = trimmedTyping
            setInput('')
            return
          }
        }
      }
    }

    const combinedMessageBody = [apNotice, trimmedTyping].filter(Boolean).join('\n\n').trim()
    if (!combinedMessageBody) return

    const userMessage: Message = { id: `u-${Date.now()}`, role: 'user', content: combinedMessageBody }

    // REPORT mode: handle "generate" / "regenerate" commands
    if (processingMode === 'REPORT') {
      const lower = trimmedTyping.toLowerCase()
      const isGenerateCmd = lower === 'generate' || lower === 'regenerate' || lower === 'generate report' ||
        trimmedTyping === '生成報表' || trimmedTyping === '生成' || trimmedTyping === '重新生成'

      if (isGenerateCmd) {
        // Find the latest setup card opts
        const setupMsg = [...(activeTask?.messages ?? [])].reverse().find(m => m.reportSetupCard)
        if (setupMsg?.reportSetupCard) {
          setMessages((prev) => [...prev, userMessage])
          setInput('')
          void handleGenerateReport({
            dateFrom: setupMsg.reportSetupCard!.defaultDateFrom,
            dateTo: setupMsg.reportSetupCard!.defaultDateTo,
            suspenseCode: setupMsg.reportSetupCard!.defaultSuspenseCode,
            arControlCode: setupMsg.reportSetupCard!.defaultArControlCode,
            apControlCode: setupMsg.reportSetupCard!.defaultApControlCode,
            bankCode: setupMsg.reportSetupCard!.defaultBankCode,
          })
          return
        }
      }

      if (!activeTaskId) {
        const assistantMessage: Message = {
          id: `a-${Date.now()}`, role: 'assistant',
          content: `Type "generate report" or click Generate report on the setup card. Type "Exit REPORT" or use the top button to leave report mode.`,
        }
        setMessages((prev) => [...prev, userMessage, assistantMessage])
        setInput('')
        return
      }

      const setupCardMsg = [...(activeTask?.messages ?? [])].reverse().find(m => m.reportSetupCard)
      const card = setupCardMsg?.reportSetupCard
      const reportCtx = {
        generated: !!(activeTask?.messages ?? []).some(m => m.financialReportData),
        options: card
          ? {
              dateFrom: card.defaultDateFrom,
              dateTo: card.defaultDateTo,
              suspenseCode: card.defaultSuspenseCode,
              arControlCode: card.defaultArControlCode,
              apControlCode: card.defaultApControlCode,
              bankCode: card.defaultBankCode,
              isGenerated: card.isGenerated ?? false,
            }
          : null,
        summary: (() => {
          const msgs = activeTask?.messages ?? []
          for (let i = msgs.length - 1; i >= 0; i--) {
            const fd = msgs[i].financialReportData
            if (fd?.summary) return fd.summary
          }
          return null
        })(),
        row_counts: (() => {
          const msgs = activeTask?.messages ?? []
          for (let i = msgs.length - 1; i >= 0; i--) {
            const fd = msgs[i].financialReportData
            if (fd) {
              return {
                trial_accounts: fd.trialBalanceRows.length,
                income_lines: fd.incomeRows.length,
                balance_lines: fd.balanceRows.length,
              }
            }
          }
          return null
        })(),
      }

      const reportChatTaskId = activeTaskId
      const taskCompanyId = activeCompany?.id ?? null
      const thinkingId = `think-report-${Date.now()}`
      const thinkingMsg: Message = { id: thinkingId, role: 'assistant', content: AI_CHAT_THINKING_PLACEHOLDER }
      setMessages((prev) => [...prev, userMessage, thinkingMsg])
      if (reportChatTaskId) setAiThinkingTaskIds(prev => new Set(prev).add(reportChatTaskId))
      setInput('')

      try {
        const chatPayload = {
          session_id: `${reportChatTaskId}_REPORT`,
          mode: 'REPORT',
          message: combinedMessageBody,
          context: {
            transactions: [],
            coa: coaList.map(c => ({
              code: c.code,
              name_en: c.name_en,
              name_zh: c.name_zh,
              category_type: c.category_type,
            })),
            report: reportCtx,
          },
        }
        const { job_id } = await api.createAiChatBackgroundJob(chatPayload, taskCompanyId)
        localBackgroundJobIdsRef.current.add(job_id)
        trackTabBackgroundJob(job_id)
        const sk = BG_JOB_STORAGE_PREFIX + job_id
        localStorage.setItem(sk, JSON.stringify({ kind: 'ai_chat', taskId: reportChatTaskId, isRecon: false, companyId: taskCompanyId }))
        let result: any
        try {
          result = await api.waitForBackgroundJob(job_id, { companyId: taskCompanyId })
        } finally {
          localStorage.removeItem(sk)
          localBackgroundJobIdsRef.current.delete(job_id)
          untrackTabBackgroundJob(job_id)
        }

        setTasks(prev => prev.map(t => {
          if (t.id !== reportChatTaskId) return t
          const finalMessages = t.messages.map(m =>
            m.id === thinkingId ? {
              ...m,
              content: '',
              isTyping: true,
              typingFullContent: result.reply,
              saveRulePending: result.save_rule_pending || false,
              saveRuleProposal: result.save_rule_proposal || null,
              ruleSaved: result.rule_saved || false,
              ruleSavedMessage: result.rule_saved_message || '',
            } : m
          )
          return { ...t, messages: finalMessages }
        }))
        generateAndSetTitle(reportChatTaskId, [userMessage, { id: `title-assistant-${Date.now()}`, role: 'assistant', content: result.reply }], taskCompanyId)
      } catch (err) {
        console.error('[AiChat REPORT] Failed:', err)
        const errMsg = err instanceof Error ? err.message : String(err)
        setTasks(prev => prev.map(t => {
          if (t.id !== reportChatTaskId) return t
          return {
            ...t,
            messages: t.messages.map(m =>
              m.id === thinkingId
                ? { ...m, content: `Sorry, something went wrong: ${errMsg}` }
                : m
            ),
          }
        }))
      } finally {
        if (reportChatTaskId) {
          setAiThinkingTaskIds(prev => { const s = new Set(prev); s.delete(reportChatTaskId); return s })
        }
      }
      return
    }

    // ── RECON: AI chat with structured match/unmatch proposals ───────────────
    if (processingMode === 'RECON') {
      const reconTaskId = await ensureReconTask()
      const taskCompanyId = activeCompany?.id ?? null
      const thinkingId = `think-recon-${Date.now()}`
      const thinkingMsg: Message = {
        id: thinkingId,
        role: 'assistant',
        content: AI_CHAT_THINKING_PLACEHOLDER,
        isReconResult: true,
        recon_chat: true,
      }
      setReconMessages(prev => [...prev, userMessage, thinkingMsg])
      updateTask(reconTaskId, t => ({ ...t, messages: [...t.messages, userMessage, thinkingMsg] }))
      setReconAiChatThinking(true)
      setInput('')

      try {
        const chatPayload = {
          session_id: `${reconTaskId}_RECON`,
          mode: 'RECON',
          message: combinedMessageBody,
          context: {
            transactions: [],
            coa: coaList.map(c => ({
              code: c.code,
              name_en: c.name_en,
              name_zh: c.name_zh,
              category_type: c.category_type,
            })),
            recon: buildReconAiContext(),
          },
        }
        const { job_id } = await api.createAiChatBackgroundJob(chatPayload, taskCompanyId)
        localBackgroundJobIdsRef.current.add(job_id)
        trackTabBackgroundJob(job_id)
        const sk = BG_JOB_STORAGE_PREFIX + job_id
        localStorage.setItem(sk, JSON.stringify({ kind: 'ai_chat', taskId: reconTaskId, isRecon: true, companyId: taskCompanyId }))
        let result: any
        try {
          result = await api.waitForBackgroundJob(job_id, { companyId: taskCompanyId })
        } finally {
          localStorage.removeItem(sk)
          localBackgroundJobIdsRef.current.delete(job_id)
          untrackTabBackgroundJob(job_id)
        }
        const ra = result.recon_actions ?? []
        const rt = result.redirect_tasks ?? []
        const finalAssistant: Message = {
          id: thinkingId,
          role: 'assistant',
          content: '',
          isTyping: true,
          typingFullContent: result.reply,
          saveRulePending: result.save_rule_pending || false,
          saveRuleProposal: result.save_rule_proposal || null,
          ruleSaved: result.rule_saved || false,
          ruleSavedMessage: result.rule_saved_message || '',
          reconActions: ra,
          reconActionsPending: ra.length > 0,
          redirectTasks: rt,
          isReconResult: true,
          recon_chat: true,
        }
        setReconMessages(prev => prev.map(m => (m.id === thinkingId ? finalAssistant : m)))
        updateTask(reconTaskId, t => ({
          ...t,
          messages: t.messages.map(m => (m.id === thinkingId ? finalAssistant : m)),
        }))
        generateAndSetTitle(reconTaskId, [userMessage, { id: `title-assistant-${Date.now()}`, role: 'assistant', content: result.reply }], taskCompanyId)
      } catch (err) {
        console.error('[AiChat RECON] Failed:', err)
        const errMsg = err instanceof Error ? err.message : String(err)
        const errAssistant: Message = {
          id: thinkingId,
          role: 'assistant',
          content: `Sorry, something went wrong: ${errMsg}`,
          isReconResult: true,
          recon_chat: true,
        }
        setReconMessages(prev => prev.map(m => (m.id === thinkingId ? errAssistant : m)))
        updateTask(reconTaskId, t => ({
          ...t,
          messages: t.messages.map(m => (m.id === thinkingId ? errAssistant : m)),
        }))
      } finally {
        setReconAiChatThinking(false)
      }
      return
    }

    if (processingMode === 'BANK' && trimmedTyping.toLowerCase() === 'create cash table') {
      const assistantMessage: Message = {
        id: `a-${Date.now()}`,
        role: 'assistant',
        content: 'Created a blank cash table. You can enter cash in/out records manually.',
        bankTransactions: [],
        bankFilename: 'Cash Table',
        isCashTable: true,
      }
      const now = new Date()
      const newTask: ChatTask = {
        id: makeChatRecordId(),
        title: `Cash table - ${now.toLocaleDateString('en-GB', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}`,
        createdAt: now.toISOString(),
        status: 'completed',
        processingMode: 'BANK',
        messages: [userMessage, assistantMessage],
        fileQueue: [],
        fileCount: 0,
        pageCount: 0,
        hasSpreadsheet: false,
      }
      await addPersistedTask(newTask)
      setInput('')
      return
    }

    // ── OTHER: LLM chat; server applies <PATCHES> to records; refetch table ─
    if (processingMode === 'OTHER') {
      const chatTaskId = await ensureTaskSaved()
      const taskCompanyId = activeCompany?.id ?? null
      const thinkingId = `think-alia-${Date.now()}`
      const thinkingMsg: Message = { id: thinkingId, role: 'assistant', content: AI_CHAT_THINKING_PLACEHOLDER }

      setTasks(prev => prev.map(t => {
        if (t.id !== chatTaskId) return t
        return { ...t, messages: [...t.messages, userMessage, thinkingMsg] }
      }))
      setAiThinkingTaskIds(prev => new Set(prev).add(chatTaskId))
      setInput('')

      try {
        const chatPayload = {
          session_id: `${chatTaskId}_OTHER`,
          mode: 'OTHER',
          message: combinedMessageBody,
          context: {
            transactions: [],
            coa: coaList.map(c => ({
              code: c.code,
              name_en: c.name_en,
              name_zh: c.name_zh,
              category_type: c.category_type,
            })),
          },
        }
        const { job_id } = await api.createAiChatBackgroundJob(chatPayload, taskCompanyId)
        localBackgroundJobIdsRef.current.add(job_id)
        trackTabBackgroundJob(job_id)
        const sk = BG_JOB_STORAGE_PREFIX + job_id
        localStorage.setItem(sk, JSON.stringify({ kind: 'ai_chat', taskId: chatTaskId, isRecon: false, companyId: taskCompanyId }))
        let result: any
        try {
          result = await api.waitForBackgroundJob(job_id, { companyId: taskCompanyId })
        } finally {
          localStorage.removeItem(sk)
          localBackgroundJobIdsRef.current.delete(job_id)
          untrackTabBackgroundJob(job_id)
        }

        setTasks(prev => prev.map(t => {
          if (t.id !== chatTaskId) return t
          const finalMessages = t.messages.map(m =>
            m.id === thinkingId ? {
              ...m,
              content: '',
              isTyping: true,
              typingFullContent: result.reply,
              saveRulePending: result.save_rule_pending || false,
              saveRuleProposal: result.save_rule_proposal || null,
              ruleSaved: result.rule_saved || false,
              ruleSavedMessage: result.rule_saved_message || '',
            } : m
          )
          return { ...t, messages: finalMessages }
        }))

        try {
          const { records } = await api.getOtherRecords(chatTaskId, taskCompanyId)
          setOtherRecords(records.map(r => ({
            id: r.id,
            record_type: r.record_type as 'loan' | 'fixed_asset',
            ...r.payload_json,
          })))
        } catch {
          /* keep existing table on refetch failure */
        }

        generateAndSetTitle(chatTaskId, [userMessage, { id: `title-assistant-${Date.now()}`, role: 'assistant', content: result.reply }], taskCompanyId)
      } catch (err) {
        console.error('[AiChat OTHER] Failed:', err)
        const errMsg = err instanceof Error ? err.message : String(err)
        setTasks(prev => prev.map(t => {
          if (t.id !== chatTaskId) return t
          return {
            ...t,
            messages: t.messages.map(m =>
              m.id === thinkingId
                ? { ...m, content: `Sorry, something went wrong: ${errMsg}` }
                : m
            ),
          }
        }))
      } finally {
        setAiThinkingTaskIds(prev => { const s = new Set(prev); s.delete(chatTaskId); return s })
      }
      return
    }

    // ── AR / AP / BANK mode: real LLM chat with context injection ───────────
    if (processingMode === 'AR' || processingMode === 'AP' || processingMode === 'BANK') {
      let chatTaskId: string

      if (processingMode === 'BANK') {
        // ── BANK task routing ──────────────────────────────────────────────
        const serverKnown = lastSuccessfulServerTaskIdsRef.current
        const pendingLocal = pendingLocalTaskIdsRef.current
        const bankTasksAll = tasks.filter(t => t.processingMode === 'BANK')
        const bankTasks =
          serverKnown === null
            ? bankTasksAll.filter(t => pendingLocal.has(t.id))
            : bankTasksAll.filter(t => serverKnown.has(t.id) || pendingLocal.has(t.id))

        // Guard: no BANK tasks at all
        if (bankTasks.length === 0) {
          const guardMsg: Message = {
            id: `a-${Date.now()}`,
            role: 'assistant',
            content: 'Upload a bank statement first, or type "Create cash table", then start chatting.',
          }
          setMessages(prev => [...prev, userMessage, guardMsg])
          setInput('')
          return
        }

        // Auto-resolve when only one BANK task exists
        if (bankTasks.length === 1 && !bankChatTargetTaskId) {
          setBankChatTargetTaskId(bankTasks[0].id)
        }

        // Task picker: user has multiple BANK tasks and hasn't picked one yet
        if (!bankChatTargetTaskId || !bankTasks.find(t => t.id === bankChatTargetTaskId)) {
          // Check if user typed a number to select from a previous picker message
          const pickerChoice = parseInt(trimmedTyping, 10)
          if (!isNaN(pickerChoice) && pickerChoice >= 1 && pickerChoice <= bankTasks.length) {
            const chosen = bankTasks[pickerChoice - 1]
            setBankChatTargetTaskId(chosen.id)
            const confirmMsg: Message = {
              id: `a-${Date.now()}`,
              role: 'assistant',
              content: `Selected "${chosen.title}" as the chat target. Continue with your question.`,
            }
            setMessages(prev => [...prev, userMessage, confirmMsg])
            setInput('')
            return
          }
          // Show picker list
          const listLines = bankTasks
            .map((t, i) => `${i + 1}. ${t.title}`)
            .join('\n')
          const pickerMsg: Message = {
            id: `a-${Date.now()}`,
            role: 'assistant',
            content: `You have multiple bank statement tasks. Type a number to choose which one to discuss:\n\n${listLines}`,
          }
          setMessages(prev => [...prev, userMessage, pickerMsg])
          setInput('')
          return
        }

        chatTaskId = bankChatTargetTaskId
      } else {
        // ── AR / AP: anchor to the active/new task ─────────────────────────
        chatTaskId = await ensureTaskSaved()
      }

      const currentTaskResolved = tasks.find(t => t.id === chatTaskId)
      const taskCompanyId =
        currentTaskResolved?.fileQueue.find(f => f.companyId)?.companyId ??
        activeCompany?.id ??
        null

      await invokeWorkspaceModeAiChatRound({
        chatTaskId,
        taskCompanyId,
        mode: processingMode,
        combinedMessageBody,
        userMessage,
      })
      return
    }

    // ── Fallback for RECON / REPORT and other modes ──────────────────────────
    const assistantMessage: Message = { id: `a-${Date.now()}`, role: 'assistant', content: 'Chat functionality coming soon!' }
    setMessages((prev) => [...prev, userMessage, assistantMessage])
    setInput('')
  }

  // ─── RECON helpers ────────────────────────────────────────────────────────
  const parseReconAmount = (value: unknown): number => {
    const text = String(value ?? '').replace(/[, ]/g, '').trim()
    if (!text) return 0
    const parsed = Number(text)
    return Number.isFinite(parsed) ? parsed : 0
  }

  const formatReconAmount = (value: number, currency?: string): string => {
    const absolute = Math.abs(value)
    const signed = value < 0 ? -absolute : absolute
    return `${signed.toFixed(2)} ${currency || 'HKD'}`
  }

  const buildReconRecords = () => tasks.filter(t => t.processingMode && t.processingMode !== 'RECON')

  const detectBankDuplicates = (): DuplicateAlert[] => {
    const alerts: DuplicateAlert[] = []
    const bankTasks = tasks.filter(t => t.processingMode === 'BANK')

    for (const task of bankTasks) {
      type TxnRef = { msgId: string; msgIdx: number; txnIndex: number; txn: BankTransaction; fileName: string }
      const allTxns: TxnRef[] = []
      task.messages.forEach((m, mIdx) => {
        if (!m.bankTransactions) return
        m.bankTransactions.forEach((txn, tIdx) => {
          allTxns.push({ msgId: m.id, msgIdx: mIdx, txnIndex: tIdx, txn, fileName: m.bankFilename || '' })
        })
      })

      const fileNameFirstSeen = new Map<string, number>()
      task.messages.forEach((m, mIdx) => {
        if (!m.bankFilename) return
        if (!fileNameFirstSeen.has(m.bankFilename)) fileNameFirstSeen.set(m.bankFilename, mIdx)
      })

      const dupFileMessages = new Set<number>()
      task.messages.forEach((m, mIdx) => {
        if (!m.bankFilename) return
        if (fileNameFirstSeen.get(m.bankFilename) !== mIdx) dupFileMessages.add(mIdx)
      })

      const amtKey = (txn: BankTransaction) => `${txn.deposit ?? 0}|${txn.withdrawal ?? 0}`

      for (let i = 0; i < allTxns.length; i++) {
        const later = allTxns[i]
        if (later.txn._duplicateConfirmed) continue

        let bestLevel: 1 | 2 | 3 | 4 | null = null
        let bestOriginal: TxnRef | null = null
        const laterDate = later.txn.date || later.txn.transaction_date || ''
        const laterAmt = amtKey(later.txn)
        const laterAcct = (later.txn.account_number || '').trim()
        const laterInDupFile = dupFileMessages.has(later.msgIdx)

        for (let j = 0; j < i; j++) {
          const earlier = allTxns[j]
          if (earlier.msgIdx === later.msgIdx) continue
          const earlierDate = earlier.txn.date || earlier.txn.transaction_date || ''
          const earlierAmt = amtKey(earlier.txn)
          const earlierAcct = (earlier.txn.account_number || '').trim()
          const sameFile = earlier.fileName === later.fileName && earlier.fileName !== ''
          const sameDate = laterDate !== '' && laterDate === earlierDate
          const sameAmt = laterAmt === earlierAmt
          const sameAcct = laterAcct !== '' && laterAcct === earlierAcct

          // L4: account + date + Dr/Cr (highest priority)
          if (sameAcct && sameDate && sameAmt) {
            if (!bestLevel || 4 > bestLevel) { bestLevel = 4; bestOriginal = earlier }
          }
          // L2: same file name + date + Dr/Cr
          if (sameFile && sameDate && sameAmt) {
            if (!bestLevel || (2 > bestLevel && bestLevel !== 4)) { bestLevel = 2; bestOriginal = earlier }
          }
          // L3: date + Dr/Cr across different files
          if (!sameFile && sameDate && sameAmt) {
            if (!bestLevel || (bestLevel < 3 && bestLevel !== 4 && bestLevel !== 2)) { bestLevel = 3; bestOriginal = earlier }
          }
        }

        // L1: file name duplicate (only if no higher level matched)
        if (!bestLevel && laterInDupFile) {
          bestLevel = 1
        }

        if (bestLevel) {
          const levelLabels: Record<number, string> = {
            1: `Duplicate filename — "${later.fileName}" was uploaded before; transactions may overlap`,
            2: `Confirmed duplicate — same filename, date, and amount (${later.txn.date}, ${later.fileName})`,
            3: `Possible overlap — different files, same date and amount (${later.txn.date})`,
            4: `Confirmed duplicate — same account, date, and amount (${laterAcct}, ${later.txn.date})`,
          }
          const existingAlert = alerts.find(a => a.level === bestLevel && a.taskId === task.id)
          if (existingAlert) {
            existingAlert.txnIds.push({ msgId: later.msgId, txnIndex: later.txnIndex, idNumber: later.txn.id_number || '' })
          } else {
            alerts.push({
              id: `dup-${task.id}-L${bestLevel}-${Date.now()}-${i}`,
              level: bestLevel,
              taskId: task.id,
              message: levelLabels[bestLevel],
              txnIds: [{ msgId: later.msgId, txnIndex: later.txnIndex, idNumber: later.txn.id_number || '' }],
            })
          }
        }
      }
    }
    return alerts
  }

  const buildReconTransactionRows = (records: ChatTask[]): ReconTransactionItem[] => {
    const rows: ReconTransactionItem[] = []

    // Smart side assignment:
    // - Mixed BANK + AR/AP → BANK = bank side, AR/AP = source side (standard)
    // - All-BANK tasks → split by task order: even index = bank, odd index = source
    // - All-source tasks (AR/AP) → even index = source, odd index = bank (first AR/AP task
    //   stays under "Source Records"; second task goes to the other pool for same-mode match)
    // This enables same-mode reconciliation (BANK vs BANK, AR vs AR, AR vs AP).
    const bankTasks   = records.filter(r => r.processingMode === 'BANK')
    const sourceTasks = records.filter(r => r.processingMode !== 'BANK')
    const hasMixedModes = bankTasks.length > 0 && sourceTasks.length > 0

    // Parity must ignore tasks with zero spreadsheet rows; otherwise an empty AR/AP task
    // before a real one shifts odd/even and puts all rows in pool B (user sees only "B").
    const spreadsheetRowsByTaskId = new Map<string, SpreadsheetRow[]>()
    for (const record of records) {
      let dataRows: SpreadsheetRow[] = []
      record.messages.forEach(m => {
        if (m.spreadsheetData) dataRows.push(...m.spreadsheetData)
      })
      if (dataRows.length === 0) dataRows = record.spreadsheetData || []
      spreadsheetRowsByTaskId.set(record.id, dataRows)
    }
    const sourceTasksWithData = sourceTasks.filter(
      r => (spreadsheetRowsByTaskId.get(r.id)?.length ?? 0) > 0,
    )
    const bankTasksWithData = bankTasks.filter(
      r => (spreadsheetRowsByTaskId.get(r.id)?.length ?? 0) > 0,
    )

    records.forEach((record) => {
      const mode = record.processingMode || 'AR'
      const isBank = mode === 'BANK'

      // Determine which pool (bank side / source side) this task's transactions go into
      let kind: 'source' | 'bank'
      if (hasMixedModes) {
        kind = isBank ? 'bank' : 'source'
      } else if (isBank) {
        const taskIndex = bankTasksWithData.indexOf(record)
        kind = taskIndex % 2 === 0 ? 'bank' : 'source'
      } else {
        const taskIndex = sourceTasksWithData.indexOf(record)
        kind = taskIndex % 2 === 0 ? 'source' : 'bank'
      }

      const dataRows = spreadsheetRowsByTaskId.get(record.id) ?? []

      dataRows.forEach((row, idx) => {
        // Field extraction always follows processingMode (not pool-side kind) so
        // AR records classified as 'bank' side still extract ledger_txn_id correctly.
        const txnId = String(isBank ? (row.bank_txn_id || '') : (row.ledger_txn_id || '')).trim()
        const voucherNo = String(isBank ? (row['憑證號'] || row.reference || '') : (row.voucher_no || row.reference || '')).trim()
        const date = String(isBank ? (row['日期'] || row.date || '') : (row.date || row['日期'] || '')).trim()
        const bank = String(isBank ? (row['銀行'] || row.bank || '') : (row.bank || row['銀行'] || '')).trim()
        const currency = String(isBank ? (row['幣別'] || row.currency || 'HKD') : (row.currency || row['幣別'] || 'HKD')).trim()
        const memo = String(isBank ? (row['備註'] || row.memo || row.description || '') : (row.memo || row.reference || row['備註'] || '')).trim()
        const amount = isBank ? parseReconAmount(row['存入']) - parseReconAmount(row['提取']) : parseReconAmount(row.amount)
        const uidCore = txnId || String(row.id || `${record.id}-${idx}`)
        rows.push({
          uid: `${kind}:${record.id}:${uidCore}`, kind, txnId, voucherNo, date, amount,
          amountText: formatReconAmount(amount, currency), memo, bank,
          currency: currency || 'HKD', recordTitle: record.title, recordMode: mode,
          matchable: Boolean(txnId), row,
        })
      })
    })
    return rows
  }

  const getReconPools = (): ReconPools => {
    const allRecords = buildReconRecords()
    const allRows = buildReconTransactionRows(allRecords)
    const sourceAll = allRows.filter((row) => row.kind === 'source')
    const bankAll = allRows.filter((row) => row.kind === 'bank')
    const sourceSelectedSet = new Set(reconSelectedSourceTxnIds)
    const bankSelectedSet = new Set(reconSelectedBankTxnIds)
    return {
      sourceAll, bankAll,
      sourcePending: sourceAll.filter((row) => !sourceSelectedSet.has(row.uid)),
      bankPending: bankAll.filter((row) => !bankSelectedSet.has(row.uid)),
      selectedSource: sourceAll.filter((row) => sourceSelectedSet.has(row.uid)),
      selectedBank: bankAll.filter((row) => bankSelectedSet.has(row.uid)),
    }
  }

  const buildReconAiContext = (): Record<string, unknown> => {
    const pools = getReconPools()
    const cap = 24
    const sampleTxn = (t: any) => ({
      id: t.id,
      reference: t.reference,
      amount: t.amount,
      currency: t.currency,
      bank_date: t.bank_date,
      recordMode: t.recordMode,
    })
    const bankIds = new Set<string>()
    const ledgerIds = new Set<string>()
    reconUnmatchedTxns.bank.forEach((t: any) => { if (t?.id) bankIds.add(String(t.id)) })
    reconUnmatchedTxns.ledger.forEach((t: any) => { if (t?.id) ledgerIds.add(String(t.id)) })
    reconMatchedGroups.forEach(g => {
      normalizeReconTxnIdList(g.bank_txn_ids).forEach(id => { if (id) bankIds.add(id) })
      normalizeReconTxnIdList(g.ledger_txn_ids).forEach(id => { if (id) ledgerIds.add(id) })
    })
    pools.selectedSource.forEach(r => { if (r.txnId) ledgerIds.add(String(r.txnId)) })
    pools.selectedBank.forEach(r => { if (r.txnId) bankIds.add(String(r.txnId)) })
    return {
      summary: {
        unmatched_bank_count: reconUnmatchedTxns.bank.length,
        unmatched_ledger_count: reconUnmatchedTxns.ledger.length,
        matched_groups_count: reconMatchedGroups.length,
        selected_source_count: pools.selectedSource.length,
        selected_bank_count: pools.selectedBank.length,
      },
      selected: {
        source: pools.selectedSource.map(r => ({
          uid: r.uid,
          txn_id: r.txnId,
          voucher: r.voucherNo,
          amount: r.amountText,
          record_mode: r.recordMode,
        })),
        bank: pools.selectedBank.map(r => ({
          uid: r.uid,
          txn_id: r.txnId,
          voucher: r.voucherNo,
          amount: r.amountText,
          record_mode: r.recordMode,
        })),
      },
      unmatched_samples: {
        bank: reconUnmatchedTxns.bank.slice(0, cap).map(sampleTxn),
        ledger: reconUnmatchedTxns.ledger.slice(0, cap).map(sampleTxn),
      },
      matched_groups_summary: reconMatchedGroups.map(g => ({
        group_id: g.id,
        bank_txn_count: normalizeReconTxnIdList(g.bank_txn_ids).length,
        ledger_txn_count: normalizeReconTxnIdList(g.ledger_txn_ids).length,
        bank_total: g.bank_total,
        ledger_total: g.ledger_total,
        difference: g.difference,
      })),
      matched_gl_summary: reconMatchedGroups.map(g => {
        const meta = glJournalMetaByGroupId[g.id]
        const vn = (meta?.voucher_no || glVoucherNoByGroupId[g.id] || '').trim()
        const st = meta?.status || glStatusByGroupId[g.id] || ''
        return {
          group_id: g.id,
          voucher_no: vn,
          status: st,
          journal_id: meta?.journal_id ?? null,
          draft_lines:
            meta?.lines?.map(l => ({
              line_id: l.id,
              line_no: l.line_no,
              account_code: l.account_code,
              memo: l.memo ?? null,
              debit: l.debit,
              credit: l.credit,
            })) ?? [],
        }
      }),
      allowed_bank_txn_ids: Array.from(bankIds),
      allowed_ledger_txn_ids: Array.from(ledgerIds),
      allowed_group_ids: reconMatchedGroups.map(g => g.id),
    }
  }

  const handleReconAllDrag = () => {
    const pools = getReconPools()
    const matchedBankSet   = new Set(reconMatchedBankUids)
    const matchedSourceSet = new Set(reconMatchedSourceUids)
    setReconSelectedSourceTxnIds(pools.sourceAll.filter(r => !matchedSourceSet.has(r.uid)).map(r => r.uid))
    setReconSelectedBankTxnIds(pools.bankAll.filter(r => !matchedBankSet.has(r.uid)).map(r => r.uid))
  }
  const handleReconClearContainer = () => {
    // Keep locked (matched) UIDs; only remove unmatched ones from the container
    setReconSelectedSourceTxnIds(reconMatchedSourceUids)
    setReconSelectedBankTxnIds(reconMatchedBankUids)
  }
  const handleReconSelectSource = (txnUid: string) => setReconSelectedSourceTxnIds((prev) => prev.includes(txnUid) ? prev : [...prev, txnUid])
  const handleReconSelectBank = (txnUid: string) => setReconSelectedBankTxnIds((prev) => prev.includes(txnUid) ? prev : [...prev, txnUid])
  const handleReconRemoveSource = (txnUid: string) => setReconSelectedSourceTxnIds((prev) => prev.filter((id) => id !== txnUid))
  const handleReconRemoveBank = (txnUid: string) => setReconSelectedBankTxnIds((prev) => prev.filter((id) => id !== txnUid))

  // ── (C) Unlock: clear both sides of a matched pair ────────────────────────
  const refreshReconUnmatched = (override?: ReconState) => {
    const currentReconState = override !== undefined ? override : reconStateRef.current
    const allRecords = buildReconRecords()
    const allRows = buildReconTransactionRows(allRecords)
    const unmatchedBank = allRows
      .filter(r => r.kind === 'bank' && currentReconState[r.voucherNo || r.txnId]?.status !== 'matched')
      .map(r => r.row)
    const unmatchedLedger = allRows
      .filter(r => r.kind === 'source' && currentReconState[r.voucherNo || r.txnId]?.status !== 'matched')
      .map(r => r.row)
    setReconUnmatchedRows({ bank: unmatchedBank, ledger: unmatchedLedger })
  }

  refreshReconUnmatchedFnRef.current = refreshReconUnmatched

  const handleReconUnlock = (id_number: string) => {
    setReconState(prev => {
      const counterpartId = prev[id_number]?.matched_id ?? ''
      const next = { ...prev }
      delete next[id_number]
      if (counterpartId) delete next[counterpartId]

      // Clear matched_id on both transactions across all task messages
      setTasks(tasksPrev => tasksPrev.map(task => {
        const updatedMessages = task.messages.map(msg => {
          let changed = false
          let nextBank = msg.bankTransactions
          let nextArap = msg.arapTransactions
          if (msg.bankTransactions) {
            nextBank = msg.bankTransactions.map(t => {
              if ((t.id_number === id_number || t.id_number === counterpartId) && t.matched_id) {
                changed = true
                return { ...t, matched_id: '' }
              }
              return t
            })
          }
          if (msg.arapTransactions) {
            nextArap = msg.arapTransactions.map(t => {
              const idKey = t.id_number ?? ''
              const baseKey = idKey.replace(/^(AR|AP)-/, '')
              const matches = (key: string) => key === id_number || key === counterpartId
              if ((matches(idKey) || matches(baseKey)) && t.matched_id) {
                changed = true
                return { ...t, matched_id: '' }
              }
              return t
            })
          }
          if (!changed) return msg
          return { ...msg, bankTransactions: nextBank, arapTransactions: nextArap }
        })
        return { ...task, messages: updatedMessages }
      }))

      // Re-derive unmatched panel with the new state (next)
      refreshReconUnmatched(next)

      return next
    })
  }
  // ────────────────────────────────────────────────────────────────────────────

  const handleDuplicateResolve = (alertId: string, action: 'continue' | 'cancel') => {
    setDuplicateAlerts(prev => prev.map(a => a.id === alertId ? { ...a, resolved: action } : a))
    if (action === 'cancel') {
      const alert = duplicateAlerts.find(a => a.id === alertId)
      if (!alert) return
      setTasks(prev => prev.map(task => {
        if (task.id !== alert.taskId) return task
        const updatedMessages = task.messages.map(msg => {
          const matchingTxns = alert.txnIds.filter(t => t.msgId === msg.id)
          if (matchingTxns.length === 0 || !msg.bankTransactions) return msg
          const indices = new Set(matchingTxns.map(t => t.txnIndex))
          const updatedBank = msg.bankTransactions.map((txn, idx) => {
            if (!indices.has(idx)) return txn
            const original = matchingTxns.find(t => t.txnIndex === idx)
            return { ...txn, _duplicateLevel: alert.level, _duplicateConfirmed: true, _duplicateOf: original?.idNumber }
          })
          return { ...msg, bankTransactions: updatedBank }
        })
        return { ...task, messages: updatedMessages }
      }))
    }
  }

  // Called by RightPanel after a multi-manual RECON match.
  // Applies a txnId → groupId map to all task messages in-memory.
  // Called on browser refresh restore to re-hydrate matched_id without a network round-trip.
  const _applyReconMatchedIdMapToTasks = (idMap: Record<string, string>) => {
    if (!Object.keys(idMap).length) return
    setTasks(prev => prev.map(task => {
      const updatedMessages = task.messages.map(msg => {
        let changed = false
        const nextBank = msg.bankTransactions?.map(t => {
          const key = (t as any).bank_txn_id || t.id_number || ''
          const gid = idMap[key]
          if (gid && t.matched_id !== gid) { changed = true; return { ...t, matched_id: gid } }
          return t
        }) ?? msg.bankTransactions
        const nextArap = msg.arapTransactions?.map(t => {
          const key = (t as any).ledger_txn_id || t.id_number || ''
          const baseKey = key.replace(/^(AR|AP)-/, '')
          const gid = idMap[key] || idMap[baseKey]
          if (gid && t.matched_id !== gid) { changed = true; return { ...t, matched_id: gid } }
          return t
        }) ?? msg.arapTransactions
        if (!changed) return msg
        return { ...msg, bankTransactions: nextBank, arapTransactions: nextArap }
      })
      if (updatedMessages === task.messages) return task
      return { ...task, messages: updatedMessages }
    }))
  }

  // Writes the RECON group_id into matched_id on each individual bankTransaction /
  // arapTransaction so the "配對ID" column in BANK/AR/AP mode tables shows the
  // reconciliation group reference.
  const handleReconMatchedIdsUpdate = (
    bankTxnIds: string[],
    ledgerTxnIds: string[],
    groupId: string,
  ) => {
    // Combine both sets into one universal set — for same-mode reconciliation
    // (AR vs AR, BANK vs BANK) the "bank" side IDs may actually be ledger or bank
    // transaction IDs from the fallback lookup, so we search both arrays for all IDs.
    const allMatchedIds = new Set([...bankTxnIds, ...ledgerTxnIds])

    // Persist txnId → groupId in the map so matched_id survives a browser refresh
    setReconMatchedIdMap(prev => {
      const next = { ...prev }
      allMatchedIds.forEach(id => { if (id) next[id] = groupId })
      return next
    })

    setTasks(prev => prev.map(task => {
      const updatedMessages = task.messages.map(msg => {
        let changed = false
        const nextBank = msg.bankTransactions?.map(t => {
          const key = (t as any).bank_txn_id || t.id_number || ''
          if (allMatchedIds.has(key) && t.matched_id !== groupId) {
            changed = true
            return { ...t, matched_id: groupId }
          }
          return t
        }) ?? msg.bankTransactions
        const nextArap = msg.arapTransactions?.map(t => {
          const key = (t as any).ledger_txn_id || t.id_number || ''
          const baseKey = key.replace(/^(AR|AP)-/, '')
          if ((allMatchedIds.has(key) || allMatchedIds.has(baseKey)) && t.matched_id !== groupId) {
            changed = true
            return { ...t, matched_id: groupId }
          }
          return t
        }) ?? msg.arapTransactions
        if (!changed) return msg
        return { ...msg, bankTransactions: nextBank, arapTransactions: nextArap }
      })
      if (updatedMessages === task.messages) return task
      return { ...task, messages: updatedMessages }
    }))

    // Lock the right-panel draggable chips for these manually matched transactions so
    // they disappear from the pending pool immediately AND survive a browser refresh.
    // getReconPools() re-derives all rows from task data; we find matching UIDs by txnId.
    const pools = getReconPools()
    const bankUidsToLock   = pools.bankAll.filter(r => r.txnId && allMatchedIds.has(r.txnId)).map(r => r.uid)
    const sourceUidsToLock = pools.sourceAll.filter(r => r.txnId && allMatchedIds.has(r.txnId)).map(r => r.uid)
    if (bankUidsToLock.length > 0)
      setReconMatchedBankUids(prev => Array.from(new Set([...prev, ...bankUidsToLock])))
    if (sourceUidsToLock.length > 0)
      setReconMatchedSourceUids(prev => Array.from(new Set([...prev, ...sourceUidsToLock])))
  }

  const refreshReconMatchedGroupsFromApi = useCallback(async () => {
    try {
      const { groups } = await reconciliationApi.fetchGroups()
      const mapped = groups.length ? mapApiReconciliationGroupsToMatched(groups) : []
      setReconMatchedGroups(mapped.length ? filterSubsumedLedgerPendingGroups(mapped) : [])
    } catch (e) {
      console.warn('[RECON] refresh groups failed:', e)
    }
  }, [])

  const restoreReconMemberToUnmatched = useCallback(
    (grp: MatchedGroupRow, txnId: string, txnType: 'bank' | 'ledger') => {
      if (txnType === 'bank') {
        const i = grp.bank_txn_ids.indexOf(txnId)
        if (i < 0) return
        const t = grp.bank_txn_snapshots?.[i] as any
        if (!t) return
        setReconUnmatchedTxns(prev => {
          if (prev.bank.some((x: any) => x.id === t.id)) return prev
          return { ...prev, bank: [...prev.bank, t] }
        })
        if (t.row) {
          setReconUnmatchedRows(prev => {
            if (prev.bank.some((r: any) => r.txn_id === t.id)) return prev
            return { ...prev, bank: [...prev.bank, { ...t.row, txn_id: t.id }] }
          })
        }
      } else {
        const i = grp.ledger_txn_ids.indexOf(txnId)
        if (i < 0) return
        const t = grp.ledger_txn_snapshots?.[i] as any
        if (!t) return
        setReconUnmatchedTxns(prev => {
          if (prev.ledger.some((x: any) => x.id === t.id)) return prev
          return { ...prev, ledger: [...prev.ledger, t] }
        })
        if (t.row) {
          setReconUnmatchedRows(prev => {
            if (prev.ledger.some((r: any) => r.txn_id === t.id)) return prev
            return { ...prev, ledger: [...prev.ledger, { ...t.row, txn_id: t.id }] }
          })
        }
      }
    },
    [],
  )

  const handleReconGroupUnmatched = (
    bankTxnIds: string[],
    ledgerTxnIds: string[],
    groupId: string,
  ) => {
    const allUnmatchedIds = new Set([...bankTxnIds, ...ledgerTxnIds])

    // Remove from the persistent matched-ID map
    setReconMatchedIdMap(prev => {
      const next = { ...prev }
      allUnmatchedIds.forEach(id => { if (id) delete next[id] })
      return next
    })

    // Clear matched_id from individual transactions in task messages
    setTasks(prev => prev.map(task => {
      const updatedMessages = task.messages.map(msg => {
        let changed = false
        const nextBank = msg.bankTransactions?.map(t => {
          const key = (t as any).bank_txn_id || t.id_number || ''
          if (allUnmatchedIds.has(key) && t.matched_id === groupId) {
            changed = true
            return { ...t, matched_id: undefined }
          }
          return t
        }) ?? msg.bankTransactions
        const nextArap = msg.arapTransactions?.map(t => {
          const key = (t as any).ledger_txn_id || t.id_number || ''
          const baseKey = key.replace(/^(AR|AP)-/, '')
          if ((allUnmatchedIds.has(key) || allUnmatchedIds.has(baseKey)) && t.matched_id === groupId) {
            changed = true
            return { ...t, matched_id: undefined }
          }
          return t
        }) ?? msg.arapTransactions
        if (!changed) return msg
        return { ...msg, bankTransactions: nextBank, arapTransactions: nextArap }
      })
      if (updatedMessages === task.messages) return task
      return { ...task, messages: updatedMessages }
    }))

    // Unlock the chips in the RECON container — remove UIDs that contain any of
    // the unmatched transaction IDs from the locked-UID arrays.
    setReconMatchedBankUids(prev =>
      prev.filter(uid => !Array.from(allUnmatchedIds).some(id => uid.includes(id)))
    )
    setReconMatchedSourceUids(prev =>
      prev.filter(uid => !Array.from(allUnmatchedIds).some(id => uid.includes(id)))
    )
  }

  /** Same state updates as ReconciliationTable manual match (lifted from RightPanel for AI Apply). */
  const applyReconMultiManualMatchResult = (
    matchedBankIds: string[],
    matchedLedgerIds: string[],
    result: any,
    fallbackSnapshots?: { bank: any[]; ledger: any[] },
  ) => {
    const bankSet = new Set(matchedBankIds.map(String))
    const ledgerSet = new Set(matchedLedgerIds.map(String))
    let snapshotBank = reconUnmatchedTxns.bank.filter((t: any) => bankSet.has(String(t.id)))
    let snapshotLedger = reconUnmatchedTxns.ledger.filter((t: any) => ledgerSet.has(String(t.id)))

    if (snapshotLedger.length === 0 && ledgerSet.size > 0) {
      snapshotLedger = reconUnmatchedTxns.bank.filter((t: any) => ledgerSet.has(String(t.id)))
    }
    if (snapshotBank.length === 0 && bankSet.size > 0) {
      snapshotBank = reconUnmatchedTxns.ledger.filter((t: any) => bankSet.has(String(t.id)))
    }
    if (fallbackSnapshots) {
      if (snapshotBank.length === 0 && bankSet.size > 0) {
        const fb = fallbackSnapshots.bank.filter((t: any) => bankSet.has(String(t.id)))
        if (fb.length) snapshotBank = fb
      }
      if (snapshotLedger.length === 0 && ledgerSet.size > 0) {
        const fb = fallbackSnapshots.ledger.filter((t: any) => ledgerSet.has(String(t.id)))
        if (fb.length) snapshotLedger = fb
      }
    }

    const allMatchedIds = new Set([...bankSet, ...ledgerSet])
    setReconUnmatchedTxns(prev => ({
      bank: prev.bank.filter((t: any) => !allMatchedIds.has(t.id)),
      ledger: prev.ledger.filter((t: any) => !allMatchedIds.has(t.id)),
    }))
    setReconUnmatchedRows(prev => ({
      bank: prev.bank.filter((r: any) => !allMatchedIds.has(r.txn_id)),
      ledger: prev.ledger.filter((r: any) => !allMatchedIds.has(r.txn_id)),
    }))

    if (result) {
      const normaliseMode = (raw: string | undefined) => {
        if (!raw) return undefined
        if (raw === 'BANK') return 'Bank'
        if (raw === 'AR' || raw === 'AP') return 'AR/AP'
        return raw
      }

      const ledgerPendingOnly = matchedBankIds.length === 0 && matchedLedgerIds.length > 0

      if (ledgerPendingOnly) {
        const t0 = snapshotLedger[0] as any
        const newGroup: MatchedGroupRow = {
          id: result.group_id,
          match_cardinality: result.match_cardinality,
          bank_vouchers: [],
          ledger_vouchers: snapshotLedger.map((t: any) => t.reference || t.id),
          bank_txn_ids: [],
          ledger_txn_ids: matchedLedgerIds,
          bank_total: result.total_bank_amount,
          ledger_total: result.total_ledger_amount,
          difference: result.difference,
          confidence: null,
          rule_hit: 'manual',
          is_legacy: false,
          currency: t0?.currency,
          bank_txn_snapshots: [],
          ledger_txn_snapshots: snapshotLedger,
          is_same_mode: false,
        }
        setReconMatchedGroups(prev => filterSubsumedLedgerPendingGroups([...prev, newGroup]))
      } else {
        const sideAMode = normaliseMode((snapshotBank[0] as any)?.recordMode) || 'Bank'
        const sideBMode = normaliseMode((snapshotLedger[0] as any)?.recordMode) || 'AR/AP'
        const isSameMode = sideAMode === sideBMode

        const newGroup: MatchedGroupRow = {
          id: result.group_id,
          match_cardinality: result.match_cardinality,
          bank_vouchers: snapshotBank.map((t: any) => t.reference || t.id),
          ledger_vouchers: snapshotLedger.map((t: any) => t.reference || t.id),
          bank_txn_ids: matchedBankIds,
          ledger_txn_ids: matchedLedgerIds,
          bank_total: result.total_bank_amount,
          ledger_total: result.total_ledger_amount,
          difference: result.difference,
          confidence: null,
          rule_hit: 'manual',
          is_legacy: false,
          currency: snapshotBank[0]?.currency,
          bank_txn_snapshots: snapshotBank,
          ledger_txn_snapshots: snapshotLedger,
          is_same_mode: isSameMode,
        }
        setReconMatchedGroups(prev => filterSubsumedLedgerPendingGroups([...prev, newGroup]))
      }
    }

    if (result) {
      handleReconMatchedIdsUpdate(matchedBankIds, matchedLedgerIds, result.group_id)
      if (result.group_id) {
        reconciliationApi.glEnsureDraft(result.group_id).catch(err =>
          console.warn('[RECON GL] auto-draft failed:', err)
        )
      }
    }

    reconciliationApi.getPartialTransactions()
      .then(res => setReconPartialTxns(res.partial_transactions))
      .catch(err => console.error('Failed to refresh partial transactions', err))

    const label =
      matchedBankIds.length === 0 && matchedLedgerIds.length > 0
        ? `Pending bank: ${matchedLedgerIds.length} AR/AP item(s) matched to suspense. Confirm the GL draft.`
        : matchedLedgerIds.length > 0
          ? `Multi-match complete: ${matchedBankIds.length} bank + ${matchedLedgerIds.length} AR/AP matched.`
          : `Cleared: ${matchedBankIds.length} bank transaction(s) marked as settled.`
    setReconStatusText(label)
  }

  /** Restore unmatched pools + notify App for locked UIDs (same as ReconciliationTable group remove). */
  const removeReconMatchedGroupById = (groupId: string) => {
    reconciliationApi.glDeleteDraftByGroup(groupId).catch(() => { /* ignore */ })
    const removedGroup = reconMatchedGroups.find(g => g.id === groupId)
    setReconMatchedGroups(prev => prev.filter(g => g.id !== groupId))

    const bankSnaps: any[] = removedGroup?.bank_txn_snapshots ?? []
    const ledgerSnaps: any[] = removedGroup?.ledger_txn_snapshots ?? []

    if (bankSnaps.length > 0 || ledgerSnaps.length > 0) {
      setReconUnmatchedTxns(prev => {
        const existingBankIds = new Set(prev.bank.map((t: any) => t.id))
        const existingLedgerIds = new Set(prev.ledger.map((t: any) => t.id))
        return {
          bank: [
            ...prev.bank,
            ...bankSnaps.filter((t: any) => !existingBankIds.has(t.id)),
          ],
          ledger: [
            ...prev.ledger,
            ...ledgerSnaps.filter((t: any) => !existingLedgerIds.has(t.id)),
          ],
        }
      })

      setReconUnmatchedRows(prev => {
        const existingBankTxnIds = new Set(prev.bank.map((r: any) => r.txn_id || r.bank_txn_id))
        const existingLedgerTxnIds = new Set(prev.ledger.map((r: any) => r.txn_id || r.ledger_txn_id))
        const restoredBankRows = bankSnaps
          .filter((t: any) => t.row && !existingBankTxnIds.has(t.id))
          .map((t: any) => ({ ...t.row, txn_id: t.id }))
        const restoredLedgerRows = ledgerSnaps
          .filter((t: any) => t.row && !existingLedgerTxnIds.has(t.id))
          .map((t: any) => ({ ...t.row, txn_id: t.id }))
        return {
          bank: [...prev.bank, ...restoredBankRows],
          ledger: [...prev.ledger, ...restoredLedgerRows],
        }
      })
    } else {
      Promise.all([
        reconciliationApi.getBankTransactions(),
        reconciliationApi.getLedgerTransactions(),
      ]).then(([bankData, ledgerData]) => {
        setReconUnmatchedTxns(prev => ({
          bank: [
            ...prev.bank,
            ...bankData.filter((t: any) =>
              t.status === 'unreconciled' && !prev.bank.some((p: any) => p.id === t.id)
            ),
          ],
          ledger: [
            ...prev.ledger,
            ...ledgerData.filter((t: any) =>
              t.status === 'unreconciled' && !prev.ledger.some((p: any) => p.id === t.id)
            ),
          ],
        }))
      }).catch(err => console.error('Failed to reload unmatched after unmatch', err))
    }

    if (removedGroup) {
      handleReconGroupUnmatched(
        removedGroup.bank_txn_ids,
        removedGroup.ledger_txn_ids,
        groupId,
      )
    }
  }

  const handleApplyReconAiActions = async (
    messageId: string,
    options?: { skipAccountCodeConfirm?: boolean },
  ) => {
    const msg = reconMessages.find(m => m.id === messageId)
    if (!msg?.reconActions?.length) return

    if (!options?.skipAccountCodeConfirm) {
      const changes: { groupId: string; lineId: string; oldCode: string; newCode: string }[] = []
      for (const act of msg.reconActions) {
        if ((act.op || '').toLowerCase() !== 'gl_draft_patch' || !act.group_id) continue
        const meta = glJournalMetaByGroupId[act.group_id]
        if (!meta?.lines?.length) continue
        const byId = new Map(meta.lines.map(l => [l.id, l]))
        for (const ln of act.gl_lines ?? []) {
          const lid = String(ln.line_id ?? (ln as { id?: string }).id ?? '').trim()
          const newCode =
            ln.account_code != null && String(ln.account_code).trim() !== ''
              ? String(ln.account_code).trim()
              : ''
          if (!lid || !newCode) continue
          const cur = String(byId.get(lid)?.account_code ?? '').trim()
          if (cur && newCode !== cur) {
            changes.push({ groupId: act.group_id, lineId: lid, oldCode: cur, newCode })
          }
        }
      }
      if (changes.length) {
        setReconAiAccountCodeConfirm({ messageId, changes })
        return
      }
    }

    const reconTaskId = await ensureReconTask()
    try {
      const glRefetchGroupIds: string[] = []
      const glSeedByGroupId: Record<string, GlJournalPayload> = {}
      for (const act of msg.reconActions) {
        const op = (act.op || '').toLowerCase()
        if (op === 'match' && act.bank_txn_ids?.length && act.ledger_txn_ids?.length) {
          const res = await reconciliationApi.multiManualMatch({
            bank_txn_ids: act.bank_txn_ids,
            ledger_txn_ids: act.ledger_txn_ids,
          })
          applyReconMultiManualMatchResult(act.bank_txn_ids, act.ledger_txn_ids, res)
        } else if (op === 'ledger_pending' && act.ledger_txn_ids?.length) {
          const res = await reconciliationApi.ledgerPendingMatch({ ledger_txn_ids: act.ledger_txn_ids })
          applyReconMultiManualMatchResult([], act.ledger_txn_ids, res)
        } else if (op === 'unmatch' && act.group_id) {
          removeReconMatchedGroupById(act.group_id)
        } else if (op === 'gl_draft_patch' && act.group_id) {
          const groupId = act.group_id
          const rawLines = act.gl_lines ?? []
          const delIds = [...(act.deleted_line_ids ?? [])]
            .map(x => String(x).trim())
            .filter(Boolean)
          if (!rawLines.length && !delIds.length) continue
          let journalId = act.journal_id?.trim() || ''
          if (!journalId) {
            const meta = glJournalMetaByGroupId[groupId]
            journalId = meta?.journal_id ?? ''
            if (!journalId) {
              const jr = await reconciliationApi.glGetByGroup(groupId)
              journalId = jr.journal?.id ?? ''
            }
          }
          if (!journalId) {
            throw new Error(
              'No journal found for this reconciliation group. Refresh RECON and try again.\n\n'
              + 'No journal for this reconciliation group; refresh RECON and try again.',
            )
          }
          const lines = rawLines.map(ln => {
            const o: {
              id?: string
              account_code?: string
              debit?: number
              credit?: number
              memo?: string
            } = {}
            const lid = (ln.line_id ?? (ln as { id?: string }).id ?? '').trim()
            if (lid) o.id = lid
            if (ln.account_code != null && String(ln.account_code).trim() !== '') {
              o.account_code = String(ln.account_code).trim()
            }
            if (ln.memo != null) o.memo = String(ln.memo)
            if (ln.debit != null) o.debit = Number(ln.debit)
            if (ln.credit != null) o.credit = Number(ln.credit)
            return o
          })
          const patchBody: {
            lines?: typeof lines
            deleted_line_ids?: string[]
          } = {}
          if (lines.length) patchBody.lines = lines
          if (delIds.length) patchBody.deleted_line_ids = delIds
          if (!patchBody.lines && !patchBody.deleted_line_ids) continue
          const patched = await reconciliationApi.glPatchJournal(journalId, patchBody)
          setGlJournalMetaByGroupId(prev => ({
            ...prev,
            [groupId]: {
              journal_id: patched.id,
              voucher_no: (patched.voucher_no || '').trim(),
              status: String(patched.status ?? ''),
              lines: Array.isArray(patched.lines) ? patched.lines : [],
            },
          }))
          glRefetchGroupIds.push(groupId)
          glSeedByGroupId[groupId] = patched
        }
      }
      if (glRefetchGroupIds.length > 0) {
        const unique = [...new Set(glRefetchGroupIds)]
        let nextNonce = 0
        setGlJournalRefetchSignal(prev => {
          nextNonce = (prev?.nonce ?? 0) + 1
          return { nonce: nextNonce, groupIds: unique }
        })
        if (Object.keys(glSeedByGroupId).length > 0) {
          setGlApplyPatchSeeds(prev => ({
            nonce: (prev?.nonce ?? 0) + 1,
            byGroupId: glSeedByGroupId,
          }))
        }
      }
      const doneMsg: Message = {
        id: `a-recon-applied-${Date.now()}`,
        role: 'assistant',
        content: 'Applied the AI suggested match / unmatch actions.',
        isReconResult: true,
        recon_chat: true,
      }
      setReconMessages(prev => {
        const cleared = prev.map(m =>
          m.id === messageId
            ? { ...m, reconActionsPending: false, reconActions: undefined }
            : m
        )
        return [...cleared, doneMsg]
      })
      updateTask(reconTaskId, t => {
        const cleared = t.messages.map(m =>
          m.id === messageId
            ? { ...m, reconActionsPending: false, reconActions: undefined }
            : m
        )
        return { ...t, messages: [...cleared, doneMsg] }
      })
      void taskApi.appendMessage(reconTaskId, {
        role: 'assistant',
        content_text: doneMsg.content,
        payload_json: { kind: 'recon_chat', recon_chat: true, isReconResult: true },
      }).catch(() => { /* duplicate ok if backend already synced */ })
    } catch (e) {
      const err = e instanceof Error ? e.message : String(e)
      const errLine: Message = {
        id: `a-recon-err-${Date.now()}`,
        role: 'assistant',
        content: `Apply failed: ${err}`,
        isReconResult: true,
        recon_chat: true,
      }
      setReconMessages(prev => [...prev, errLine])
    }
  }

  const openRedirectTaskFromRecon = (taskId: string, modeRaw: string | undefined) => {
    const m = (modeRaw || '').toUpperCase()
    const allowed: ProcessingMode[] = ['AR', 'AP', 'BANK', 'OTHER']
    if (!allowed.includes(m as ProcessingMode)) return
    setProcessingMode(m as ProcessingMode)
    assignActiveTaskId(taskId)
  }

  const runDuplicateScan = () => {
    const alerts = detectBankDuplicates()
    setDuplicateAlerts(alerts)
    return alerts
  }

  const startReconMode = () => {
    try {
      const navGid = sessionStorage.getItem('recon_nav_group_id')?.trim()
      const navGl = sessionStorage.getItem('recon_nav_gl_display')?.trim()
      sessionStorage.removeItem('recon_nav_group_id')
      sessionStorage.removeItem('recon_nav_gl_display')
      if (navGid) {
        setReconScrollTargetGroupId(navGid)
        setReconScrollPendingGlDisplay(null)
      } else if (navGl) {
        setReconScrollPendingGlDisplay(navGl)
        setReconScrollTargetGroupId(null)
      }
    } catch {
      /* ignore */
    }
    setProcessingMode('BANK')
    const list = tasksRef.current
    const pick =
      list.find(t => t.processingMode === 'BANK' && (t.hasSpreadsheet || (t.fileCount ?? 0) > 0))?.id
      ?? list.find(t => t.processingMode === 'BANK')?.id
      ?? activeTaskIdRef.current
    if (pick) {
      assignActiveTaskId(pick)
      setBankChatTargetTaskId(pick)
    }
  }

  const exitReconMode = () => {
    setProcessingMode('AR')
    // Restore whichever OCR task was active before RECON
    assignActiveTaskId(preReconActiveTaskIdRef.current)
  }

  // ─── REPORT mode entry / exit / generation ───────────────────────────────
  const startReportMode = async () => {
    reconStageReminderSentRef.current = true  // user manually moved to REPORT; no further RECON reminder
    // Save context so we can restore it on exit.
    // Never save a REPORT task as the restore point — always restore to null (clean OCR state).
    const prevTask = tasks.find(t => t.id === activeTaskId)
    preReportActiveTaskIdRef.current = (prevTask?.processingMode !== 'REPORT') ? activeTaskId : null
    preReportModeRef.current = processingMode !== 'REPORT'
      ? processingMode
      : 'AR'

    // Count all session transactions (exclude confirmed duplicates from bank)
    const allArap = tasks.flatMap(t => t.messages.flatMap(m => m.arapTransactions ?? []))
    const allBank = tasks.flatMap(t => t.messages.flatMap(m =>
      (m.bankTransactions ?? []).filter(txn => !txn._duplicateConfirmed)
    ))
    const codedArap = allArap.filter(t => (t.account_code ?? '').trim()).length
    const codedBank = allBank.filter(t => (t.account_code ?? '').trim()).length
    const totalCount = allArap.length + allBank.length
    const codedCount = codedArap + codedBank
    const uncodedCount = totalCount - codedCount

    // Infer date range from transactions
    const allDates = [
      ...allArap.map(t => t.date).filter(Boolean),
      ...allBank.map(t => t.transaction_date ?? t.date).filter(Boolean),
    ] as string[]
    allDates.sort()
    const defaultDateFrom = allDates[0]?.slice(0, 10) ?? ''
    const defaultDateTo = allDates[allDates.length - 1]?.slice(0, 10) ?? ''

    // Find suspense account candidates (never fall back to a real CoA account)
    const suspenseOptions = coaList
      .filter(c => /suspense|暫記|temp|雜項/i.test(c.name_en + c.name_zh))
      .map(c => ({ code: c.code, name: c.name_zh || c.name_en }))
    const defaultSuspenseCode = suspenseOptions[0]?.code ?? '9999'

    // Auto-detect control account defaults from CoA
    const controlAccountOptions = coaList.map(c => ({ code: c.code, name: c.name_zh || c.name_en }))
    const findControl = (pattern: RegExp, fallback: string) =>
      coaList.find(c => pattern.test(c.name_en + ' ' + c.name_zh))?.code ?? fallback
    const defaultArControlCode = findControl(/receivable|應收/i, '1100')
    const defaultApControlCode = findControl(/payable|應付/i, '2100')
    const defaultBankCode      = findControl(/^bank|^cash|銀行|現金/i, '1000')

    // Create a new REPORT task to hold the dialogue
    const now = new Date()
    const reportTaskId = makeChatRecordId()
    const step1: Message = {
      id: `report-step1-${Date.now()}`,
      role: 'assistant',
      content: `Switched to REPORT mode.\n\nGathering this session's transactions to prepare the trial balance, P&L, and balance sheet.`,
    }
    const step2: Message = {
      id: `report-step2-${Date.now() + 1}`,
      role: 'assistant',
      content: `Transaction scan complete:\n• AR/AP: ${allArap.length}\n• Bank: ${allBank.length}\n• Coded: ${codedCount}\n• Uncoded: ${uncodedCount} (will go to suspense)\n• RECON unmatched: ${reconUnmatchedRows.bank.length + reconUnmatchedRows.ledger.length} (will go to suspense)\n\nSet report options, then click Generate report:`,
    }
    const step3: Message = {
      id: `report-step3-${Date.now() + 2}`,
      role: 'assistant',
      content: '',
      reportSetupCard: {
        defaultDateFrom,
        defaultDateTo,
        defaultSuspenseCode,
        suspenseOptions,
        defaultArControlCode,
        defaultApControlCode,
        defaultBankCode,
        controlAccountOptions,
        isGenerated: false,
      },
    }

    const reportTask: ChatTask = {
      id: reportTaskId,
      title: `Report - ${now.toLocaleDateString('en-GB', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}`,
      createdAt: now.toISOString(),
      status: 'idle',
      processingMode: 'REPORT',
      messages: [step1, step2, step3],
      fileQueue: [],
      fileCount: 0,
      pageCount: 0,
      hasSpreadsheet: false,
      titleGenerated: false,
    }

    if (await addPersistedTask(reportTask)) {
      setProcessingMode('REPORT')
    }
  }

  const exitReportMode = () => {
    setProcessingMode(preReportModeRef.current)
    assignActiveTaskId(preReportActiveTaskIdRef.current)
  }

  const handleGenerateReport = async (opts: {
    dateFrom: string
    dateTo: string
    suspenseCode: string
    arControlCode: string
    apControlCode: string
    bankCode: string
    glDraftPicks?: Record<string, string>
  }) => {
    const { glDraftPicks, ...baseOpts } = opts
    const picks = glDraftPicks ?? {}

    const nonReportTasks = tasks.filter(t => t.processingMode !== 'REPORT')
    const allArap = nonReportTasks.flatMap(t => t.messages.flatMap(m => m.arapTransactions ?? []))
    const allBank = nonReportTasks.flatMap(t => t.messages.flatMap(m =>
      (m.bankTransactions ?? []).filter(txn => !txn._duplicateConfirmed)
    ))

    const unmatchedBankIds = new Set(
      reconUnmatchedRows.bank
        .map(r => String(r.id_number ?? r.bank_txn_id ?? ''))
        .filter(Boolean)
    )
    const unmatchedLedgerIds = new Set(
      reconUnmatchedRows.ledger
        .map(r => String(r.id_number ?? r.ledger_txn_id ?? r.voucher_no ?? ''))
        .filter(Boolean)
    )

    const runOcrOnly = (): FinancialReportData =>
      computeReportData(allArap, allBank, coaList, baseOpts, unmatchedBankIds, unmatchedLedgerIds)

    let reportData: FinancialReportData
    let usedGl = false

    try {
      const { journals } = await reconciliationApi.glListForReport(baseOpts.dateFrom, baseOpts.dateTo, 1000)
      const merged = mergeGlJournalsForReport(journals, picks)
      if (merged.conflicts.length > 0) {
        const conflictMsg: Message = {
          id: `report-gl-conflict-${Date.now()}`,
          role: 'assistant',
          content:
            'Multiple draft vouchers exist for the same match group. Pick which one the trial balance should use.\n\n' +
            'Multiple draft vouchers for the same reconciliation group. Choose one per group below.',
          reportGlDraftConflict: {
            conflicts: merged.conflicts,
            baseOpts,
            accumulatedPicks: picks,
          },
        }
        setTasks(prev => prev.map(t => {
          if (t.id !== activeTaskId) return t
          return { ...t, status: 'idle' as const, messages: [...t.messages, conflictMsg] }
        }))
        return
      }
      if (merged.activeJournals.length > 0) {
        usedGl = true
        // Strict GL: TB movements = journal lines only (no OCR reconstruction layer).
        reportData = buildReportFromGlJournalsOnly(
          merged.activeJournals,
          merged.supersededDrafts,
          coaList,
          baseOpts,
        )
      } else {
        reportData = runOcrOnly()
      }
    } catch (err) {
      console.warn('[Report] GL fetch failed; falling back to OCR reconstruction:', err)
      reportData = runOcrOnly()
    }

    let content =
      `Report generated (${reportData.trialBalanceRows.length} account(s), ${reportData.trialBalanceRows.reduce((s, r) => s + r.transactions.length, 0)} transaction(s)).`
    const prov = reportData.glProvenance
    if (usedGl && prov) {
      if (prov.source === 'gl') {
        content += `\n\nTB from GL journal lines only (HKD; no OCR posting layer).`
      }
      if (prov.includesDraftJournals) {
        content += `\n\n⚠ Draft journals included (not posted).`
      }
    }

    const resultMsg: Message = {
      id: `report-result-${Date.now()}`,
      role: 'assistant',
      content,
      financialReportData: reportData,
    }

    setTasks(prev => prev.map(t => {
      if (t.id !== activeTaskId) return t
      const updatedMessages = t.messages.map(m =>
        m.reportSetupCard ? { ...m, reportSetupCard: { ...m.reportSetupCard, isGenerated: true } } : m
      )
      return { ...t, status: 'idle' as const, messages: [...updatedMessages, resultMsg] }
    }))

    if (activeTaskId) {
      taskApi.saveState(activeTaskId, 'report_data', { reportData, opts: baseOpts }).catch(
        err => console.warn('[Tasks] Failed to save report snapshot:', err)
      )
    }
  }

  // ─── RECON state persistence (localStorage) ────────────────────────────────
  // Save RECON workspace state when the matched/unmatched rows change while in RECON mode
  useEffect(() => {
    if (!user || processingMode !== 'RECON') return
    const key = `recon_v1_${user.id}_default`
    try {
      localStorage.setItem(key, JSON.stringify({
        reconMatchedRows,
        reconMatchedColumns,
        reconUnmatchedRows,
        reconMatchedSourceUids,
        reconMatchedBankUids,
        reconMatchedIdMap,
        reconMatchedGroups,
      }))
    } catch { /* storage full */ }
  }, [
    processingMode,
    reconMatchedRows,
    reconMatchedColumns,
    reconUnmatchedRows,
    reconMatchedSourceUids,
    reconMatchedBankUids,
    reconMatchedIdMap,
    reconMatchedGroups,
    user?.id,
  ])

  // ── Persist RECON unmatched pool to backend whenever it changes in RECON mode ──
  // Watches both reconUnmatchedTxns (raw objects for FAB multi-match) and
  // reconUnmatchedRows (display rows with OCR column data) together so the saved
  // entry always contains both.  Match by txn_id, not positional index, so the
  // pairing is correct even when the two arrays are updated in separate renders.
  // Covers all call sites: Match run, FAB match, group delete/restore, AI-match.
  useEffect(() => {
    if (!user || processingMode !== 'RECON') return

    const bankRowMap   = new Map((reconUnmatchedRows.bank   as any[]).map(r => [r.txn_id, r]))
    const ledgerRowMap = new Map((reconUnmatchedRows.ledger as any[]).map(r => [r.txn_id, r]))

    const bankEntries = (reconUnmatchedTxns.bank as any[]).map(t => ({
      txn_id: t.id,
      txn_type: 'bank' as const,
      raw_txn_data: t,
      display_row: bankRowMap.get(t.id) ?? null,
    }))
    const ledgerEntries = (reconUnmatchedTxns.ledger as any[]).map(t => ({
      txn_id: t.id,
      txn_type: 'ledger' as const,
      raw_txn_data: t,
      display_row: ledgerRowMap.get(t.id) ?? null,
    }))
    reconciliationApi.saveSession({ entries: [...bankEntries, ...ledgerEntries] })
      .catch(err => console.warn('[RECON] Failed to persist session:', err))
  }, [reconUnmatchedTxns, reconUnmatchedRows, processingMode, user?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Keep reconMatchedIdMap and reconMatchedGroups persisted even when not in RECON mode
  // (e.g. user is in BANK mode — the matched_id columns still need the map on next reload).
  useEffect(() => {
    if (!user || Object.keys(reconMatchedIdMap).length === 0) return
    const key = `recon_v1_${user.id}_default`
    try {
      const existing = JSON.parse(localStorage.getItem(key) || '{}')
      localStorage.setItem(key, JSON.stringify({
        ...existing,
        reconMatchedIdMap,
        reconMatchedGroups,
      }))
    } catch { /* storage full */ }
  }, [reconMatchedIdMap, reconMatchedGroups, user?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // ─── Task management ──────────────────────────────────────────────────────
  const handleSaveRecordName = (taskId: string, newTitle: string) => {
    const trimmed = newTitle.trim()
    updateTask(taskId, t => ({ ...t, title: trimmed || t.title }))
    setEditingRecordId(null)
    if (trimmed) patchTaskMetadataFireAndForget(taskId, { title: trimmed })
  }

  // Wipe all RECON workspace state — called when a task whose transactions contributed
  // to the RECON pool is deleted.
  const _clearReconWorkspace = () => {
    setReconMatchedGroups([])
    setReconMatchedRows([])
    setReconMatchedColumns([])
    setReconUnmatchedRows({ bank: [], ledger: [] })
    setReconUnmatchedTxns({ bank: [], ledger: [] })
    setReconMatchedBankUids([])
    setReconMatchedSourceUids([])
    setReconMatchedIdMap({})
    reconMatchedIdMapRef.current = {}
    setReconMessages([...reconSeedMessages])
    reconTaskIdRef.current = null
    // Clear chip selection bar (drives chat field dragged chips)
    setReconSelectedSourceTxnIds([])
    setReconSelectedBankTxnIds([])
    // Clear chip lock visual state and other derived display state
    setReconState({})
    setReconPartialTxns([])
    setReconStatusText('')
    // Clear localStorage cache
    if (user) {
      try { localStorage.removeItem(`recon_v1_${user.id}_default`) } catch { /* ignore */ }
    }
    // Hard-reset ALL reconciliation data in the backend DB (groups, matches, session, txn statuses).
    // Flag prevents startReconMode from re-fetching this just-deleted data if the user switches
    // to RECON mode before the DELETE request completes.
    reconJustResetRef.current = true
    reconciliationApi.resetRecon()
      .catch(err => console.warn('[RECON] Failed to reset backend on delete:', err))
      .finally(() => { reconJustResetRef.current = false })
  }

  const handleDeleteTask = (taskId: string) => {
    const task = tasksRef.current.find(t => t.id === taskId)
    const isReconTask = task?.processingMode === 'RECON'
    // Determine whether this task has active RECON matches so we can warn the user
    const hasReconState = isReconTask || (
      reconMatchedGroups.length > 0 || reconMatchedRows.length > 0 ||
      reconUnmatchedRows.bank.length > 0 || reconUnmatchedRows.ledger.length > 0
    )
    setDeleteConfirm({
      taskId,
      taskTitle: task?.title || 'this record',
      hasReconState,
    })
  }

  const _commitDeleteTask = (taskId: string) => {
    setDeleteConfirm(null)
    const task = tasksRef.current.find(t => t.id === taskId)
    const isReconTask = task?.processingMode === 'RECON'
    const hasReconState = isReconTask || (
      reconMatchedGroups.length > 0 || reconMatchedRows.length > 0 ||
      reconUnmatchedRows.bank.length > 0 || reconUnmatchedRows.ledger.length > 0
    )

    // Mark as deleted so the server taskApi.list() merge won't re-add it if it races
    deletedTaskIdsRef.current.add(taskId)
    pendingLocalTaskIdsRef.current.delete(taskId)
    lastSuccessfulServerTaskIdsRef.current?.delete(taskId)
    setTasks(prev => {
      const next = prev.filter(t => t.id !== taskId)
      _writeLocalCache(next)
      return next
    })
    if (activeTaskId === taskId) assignActiveTaskId(null)

    // Clear reconTaskIdRef always, and wipe RECON workspace if applicable
    if (reconTaskIdRef.current === taskId) reconTaskIdRef.current = null
    if (hasReconState) _clearReconWorkspace()

    // BANK: clear stale chat-target ref and duplicate alerts for the deleted task
    if (bankChatTargetTaskId === taskId) setBankChatTargetTaskId(null)
    setDuplicateAlerts(prev => prev.filter(a => a.taskId !== taskId))

    // OTHER: clear the records table so it doesn't linger after the task is gone
    if (task?.processingMode === 'OTHER') setOtherRecords([])

    taskApi.remove(taskId).catch(err => console.warn('[Tasks] DELETE failed:', err))
  }

  const [otherRecords, setOtherRecords] = useState<OtherRow[]>([])

  const handleLoadTask = (task: ChatTask) => {
    // If loading a REPORT task from OCR mode, save OCR context so exitReportMode can restore properly
    if (task.processingMode === 'REPORT' && processingMode !== 'REPORT') {
      const prevTask = tasks.find(t => t.id === activeTaskId)
      preReportActiveTaskIdRef.current = (prevTask?.processingMode !== 'REPORT') ? activeTaskId : null
      preReportModeRef.current = (['AR', 'AP', 'BANK'] as ProcessingMode[]).includes(processingMode)
        ? processingMode
        : 'AR'
    }
    // Legacy RECON tasks are shown under BANK; opening uses normal task flow.
    assignActiveTaskId(task.id)
    // Sync processingMode to the task's mode
    if (task.processingMode) {
      setProcessingMode(normalizeClientProcessingMode(task.processingMode))
    }
    // BANK: AI chat uses bankChatTargetTaskId — keep it aligned with the sidebar selection
    if (task.processingMode === 'BANK') {
      setBankChatTargetTaskId(task.id)
    }
    // For OTHER tasks, fetch records from backend
    if (task.processingMode === 'OTHER') {
      api.getOtherRecords(task.id).then(({ records }) => {
        setOtherRecords(records.map(r => ({
          id: r.id,
          record_type: r.record_type as 'loan' | 'fixed_asset',
          ...r.payload_json,
        })))
      }).catch(() => setOtherRecords([]))
    } else {
      setOtherRecords([])
    }
  }

  const reconLedgerOnlyPools = useMemo(() => {
    const rec = tasks.filter(t => t.processingMode && t.processingMode !== 'RECON')
    return rec.length > 0 && rec.every(t => t.processingMode !== 'BANK')
  }, [tasks])

  const reconPools = getReconPools()
  const sourcePoolTransactions = reconPools.sourceAll
  const bankPoolTransactions = reconPools.bankAll

  // ─── Build sidebar tasks and MD content ────────────────────────────────────
  const sidebarTasks: SidebarTask[] = useMemo(() => tasks.map((t) => {
    const st = bankStaleStatusShouldBeCompleted(t) ? 'completed' : t.status
    const idleVisible =
      st === 'idle' &&
      (t.id === activeTaskId ||
        t.messages.some((m) => m.role === 'user'))
    return {
      id: t.id,
      title: t.title,
      createdAt: t.createdAt,
      status: st,
      processingMode: t.processingMode,
      fileCount: t.fileCount,
      hasSpreadsheet: t.hasSpreadsheet ?? false,
      hasFinancialReport: t.messages.some(m => m.financialReportData),
      idleVisible,
    }
  }), [tasks, activeTaskId])

  useEffect(() => {
    if (!user?.id) return
    for (const t of tasks) {
      if (!bankStaleStatusShouldBeCompleted(t)) continue
      const companyId =
        t.fileQueue.find(f => f.companyId)?.companyId ?? activeCompany?.id ?? null
      if (!companyId) continue
      updateTask(t.id, tt =>
        tt.status === 'completed' ? tt : { ...tt, status: 'completed' as TaskStatus },
      )
      patchTaskMetadataFireAndForget(t.id, { status: 'completed' }, companyId)
    }
  }, [tasks, user?.id, activeCompany?.id, updateTask])

  const latestFinancialReportData: FinancialReportData | null = useMemo(() => {
    if (!activeTask) return null
    for (let i = activeTask.messages.length - 1; i >= 0; i--) {
      if (activeTask.messages[i].financialReportData) return activeTask.messages[i].financialReportData!
    }
    return null
  }, [activeTask])

  const extractionDisplayFileCount = useMemo(() => {
    if (!activeTask) return 0
    const uploadAttached = activeTask.messages.reduce(
      (n, m) => n + (m.uploadedFiles?.length ?? 0),
      0,
    )
    return Math.max(
      activeTask.fileCount ?? 0,
      activeTask.fileQueue?.length ?? 0,
      uploadAttached,
    )
  }, [activeTask])

  const mdContent: string = useMemo(() => {
    if (!activeTask || processingMode === 'REPORT') return ''
    return buildTaskMD({
      title: activeTask.title,
      processingMode: activeTask.processingMode,
      createdAt: activeTask.createdAt,
      fileCount: activeTask.fileCount,
      displayFileCount: extractionDisplayFileCount,
      messages: activeTask.messages.map(m => ({
        role: m.role,
        content: m.content,
        contentType: m.contentType,
        bankTransactions: m.bankTransactions as Array<Record<string, unknown>> | undefined,
        arapTransactions: m.arapTransactions as Array<Record<string, unknown>> | undefined,
        spreadsheetData: m.spreadsheetData as Array<Record<string, unknown>> | undefined,
        progressPercent: m.progressPercent,
        progressLabel: m.progressLabel,
        progressMeta: m.progressMeta,
        uploadedFileCount: m.uploadedFiles?.length,
      })),
    })
  }, [activeTask, processingMode, extractionDisplayFileCount])

  const extractionTask: ExtractionSummaryTask | null = useMemo(() => {
    if (!activeTask || processingMode === 'REPORT') return null
    return {
      title: activeTask.title,
      processingMode: activeTask.processingMode,
      createdAt: activeTask.createdAt,
      fileCount: activeTask.fileCount,
      displayFileCount: extractionDisplayFileCount,
      messages: activeTask.messages.map(m => ({
        id: m.id,
        role: m.role,
        content: m.content,
        bankFilename: m.bankFilename,
        arapFilename: m.arapFilename,
        fileRefs: m.fileRefs,
        bankTransactions: m.bankTransactions as Array<Record<string, unknown>> | undefined,
        arapTransactions: m.arapTransactions as Array<Record<string, unknown>> | undefined,
        spreadsheetData: m.spreadsheetData as Array<Record<string, unknown>> | undefined,
        contentType: m.contentType,
        progressPercent: m.progressPercent,
        progressLabel: m.progressLabel,
        progressMeta: m.progressMeta,
        uploadedFileCount: m.uploadedFiles?.length,
      })),
    }
  }, [activeTask, processingMode, extractionDisplayFileCount])

  const handleOcrDestination = useCallback((p: OcrDestinationPayload) => {
    const domId = p.kind === 'bank' ? `ocr-bank-table-${p.messageId}` : `ocr-arap-table-${p.messageId}`
    const el = document.getElementById(domId)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      el.classList.add('ocr-table-highlight')
      window.setTimeout(() => { el.classList.remove('ocr-table-highlight') }, 1600)
    }
    if (!isDesktop) setWorkspaceOpen(false)
  }, [isDesktop])

  // Step index (1=OCR modes, 3=REPORT) — used when rendering workspace chrome
  const currentStep: 1 | 3 = processingMode === 'REPORT' ? 3 : 1

  const showWorkspaceWelcome =
    !activeTaskId &&
    processingMode !== 'REPORT'

  return (
    <div className="app-shell">
      <header className="top-bar top-bar--workspace">
        <div className="brand">
          <BookcometLogo variant="workspace" alt="" />
          <div>
            <div className="brand-title">Bookcomet</div>
          </div>
        </div>

        <div className="top-actions">
          {/* Hidden file inputs — kept for upload triggers */}
          <input
            ref={fileInputRef}
            type="file" accept={acceptsCsvUpload(processingMode) ? "image/*,.pdf,.csv" : "image/*,.pdf"} multiple
            style={{ display: 'none' }}
            onChange={handleFileUpload}
          />
          <input
            ref={attachFileInputRef}
            type="file" accept={acceptsCsvUpload(processingMode) ? "image/*,.pdf,.csv" : "image/*,.pdf"} multiple
            style={{ display: 'none' }}
            onChange={handleAttachFile}
          />

          <div className="new-tasks-wrapper" ref={newTasksMenuRef}>
            <button
              ref={newTasksTriggerRef}
              className="primary new-tasks-btn"
              type="button"
              aria-haspopup="menu"
              aria-expanded={newTasksMenuOpen}
              aria-label="New tasks"
              onClick={() => setNewTasksMenuOpen(o => !o)}
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden><path d="M8 1V15M1 8H15" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
              <span className="new-tasks-label" aria-hidden="true">New tasks</span>
              <svg className="top-actions-chevron" width="12" height="8" viewBox="0 0 12 8" fill="none" aria-hidden>
                <path d="M1 1.5L6 6.5L11 1.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
            {newTasksMenuOpen && (
              <div
                className={`mode-dropdown-menu mode-dropdown-menu--down${isMobile ? ' mode-dropdown-menu--mobile-fixed' : ''}`}
                style={isMobile && headerDownDropdownTop != null ? { top: headerDownDropdownTop } : undefined}
              >
                {DROPDOWN_MODES.map(mode => (
                  <div
                    key={mode}
                    className={`mode-option${processingMode === mode ? ' selected' : ''}`}
                    onClick={() => handleWelcomeNewTaskWithMode(mode)}
                  >
                    <div className="mode-option-content">
                      <div className="mode-option-label">{MODE_META[mode].label}</div>
                      <div className="mode-option-description">{MODE_META[mode].description}</div>
                    </div>
                    {processingMode === mode && (
                      <svg className="check-icon" width="16" height="16" viewBox="0 0 16 16" fill="none">
                        <path d="M13.5 4L6 11.5L2.5 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                      </svg>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {user && (
            <div className="account-menu-wrapper" ref={accountMenuRef}>
              <button
                ref={accountMenuTriggerRef}
                type="button"
                className="account-menu-trigger"
                aria-haspopup="menu"
                aria-expanded={accountMenuOpen}
                aria-label="Account menu"
                onClick={() => setAccountMenuOpen(o => !o)}
              >
                <span className="account-menu-avatar">{accountInitials}</span>
                <svg className="account-menu-chevron" width="12" height="8" viewBox="0 0 12 8" fill="none" aria-hidden>
                  <path d="M1 1.5L6 6.5L11 1.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </button>
              {accountMenuOpen && (
                <div
                  className={`mode-dropdown-menu mode-dropdown-menu--down account-menu-dropdown${isMobile ? ' mode-dropdown-menu--mobile-fixed' : ''}`}
                  style={isMobile && headerDownDropdownTop != null ? { top: headerDownDropdownTop } : undefined}
                  role="menu"
                >
                  <div className="account-menu-section">
                    <button
                      type="button"
                      className="account-menu-item"
                      role="menuitem"
                      onClick={() => {
                        setShowDashboard(true)
                        setAccountMenuOpen(false)
                      }}
                    >
                      Dashboard
                    </button>
                    <button
                      type="button"
                      className="account-menu-item"
                      role="menuitem"
                      onClick={() => {
                        setShowSettings(true)
                        setAccountMenuOpen(false)
                      }}
                    >
                      Company setting
                    </button>
                  </div>
                  <div className="account-menu-separator" role="separator" />
                  <div className="account-menu-section">
                    <button
                      type="button"
                      className="account-menu-item account-menu-item--logout"
                      role="menuitem"
                      onClick={() => {
                        setAccountMenuOpen(false)
                        void logout().then(() => navigate('/'))
                      }}
                    >
                      Log out
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </header>

      <main
        className="app-three-col"
        style={
          isDesktop
            ? {
                gridTemplateColumns: `${leftSidebarCollapsed ? LEFT_SIDEBAR_COLLAPSED_PX : leftPanelWidth}px ${
                  leftSidebarCollapsed ? 0 : 5
                }px minmax(0, 1fr) 5px ${rightPanelWidth}px`,
              }
            : undefined
        }
      >
        {/* Left Agent Sidebar — inline on desktop, drawer on mobile/tablet */}
        {!isDesktop && sidebarOpen && (
          <div className="drawer-backdrop" onClick={() => setSidebarOpen(false)} />
        )}
        <div className={`sidebar-drawer${!isDesktop ? (sidebarOpen ? ' sidebar-drawer--open' : ' sidebar-drawer--closed') : ''}`}>
          <LeftAgentSidebar
            tasks={sidebarTasks}
            activeTaskId={activeTaskId}
            activeMode={processingMode}
            isDrawer={!isDesktop}
            collapsed={isDesktop && leftSidebarCollapsed}
            onToggleCollapse={isDesktop ? toggleLeftSidebarCollapsed : undefined}
            onClose={() => setSidebarOpen(false)}
            onSelectTask={(taskId) => {
              const task = tasksRef.current.find(t => t.id === taskId)
              if (task) handleLoadTask(task)
              if (!isDesktop) setSidebarOpen(false)
            }}
            onRenameTask={(taskId, newTitle) => {
              updateTask(taskId, t => ({ ...t, title: newTitle }))
              patchTaskMetadataFireAndForget(taskId, { title: newTitle })
            }}
            onDeleteTask={handleDeleteTask}
            onUpload={() => fileInputRef.current?.click()}
            onStartReport={() => { void startReportMode(); if (!isDesktop) setSidebarOpen(false) }}
            onModeChange={(mode) => {
              if (processingMode === 'REPORT') exitReportMode()
              setProcessingMode(mode)
              setActiveTaskId(prev => {
                const task = tasksRef.current.find(t => t.id === prev)
                const next = task && task.processingMode === mode ? prev : null
                activeTaskIdRef.current = next
                return next
              })
              if (!isDesktop) setSidebarOpen(false)
            }}
            deployingTaskIds={deployingTaskIds}
            aiThinkingTaskIds={aiThinkingTaskIds}
            companies={companies}
            activeCompany={activeCompany}
            onSwitchCompany={handleSwitchCompany}
            onCreateWorkspace={handleCreateWorkspace}
          />
        </div>

        {/* Left resize handle — desktop only */}
        {isDesktop && (
          <div
            className={`panel-resize-handle${isLeftPanelResizing ? ' resizing' : ''}${
              leftSidebarCollapsed ? ' panel-resize-handle--collapsed' : ''
            }`}
            onMouseDown={leftSidebarCollapsed ? undefined : handleLeftPanelResizeStart}
          />
        )}

        {/* Chat Panel */}
        <section className="chat-panel">
          <div className="chat-header">
            <h1>{moduleGridView && showFlatModuleGrid ? (processingMode === 'AP' ? 'Payables grid' : 'Receivables grid') : 'Chat tasks'}</h1>
            {showFlatModuleGrid && (
              <div className="workspace-view-tabs">
                <button
                  type="button"
                  className={`workspace-view-tab${!moduleGridView ? ' active' : ''}`}
                  onClick={() => setModuleGridView(false)}
                >
                  Chat
                </button>
                <button
                  type="button"
                  className={`workspace-view-tab${moduleGridView ? ' active' : ''}`}
                  onClick={() => setModuleGridView(true)}
                >
                  {processingMode === 'AP' ? 'Payables grid' : 'Receivables grid'}
                </button>
              </div>
            )}
          </div>

          {activeTask?.dupWarning && (
            <div className="dup-warning-banner">
              <span className="dup-warning-icon">!</span>
              <span className="dup-warning-text">{activeTask.dupWarning}</span>
              <button className="dup-warning-dismiss" onClick={() => updateTask(activeTask.id, t => ({ ...t, dupWarning: undefined }))}>X</button>
            </div>
          )}

          {/* OTHER: persistent records table pinned above chat */}
          {processingMode === 'OTHER' && otherRecords.length > 0 && (
            <div className="other-records-panel">
              <OtherTable
                records={otherRecords}
                onRecordChange={async (recordId, updated) => {
                  try {
                    await api.updateOtherRecord(recordId, updated as Record<string, unknown>)
                    setOtherRecords(prev =>
                      prev.map(r => r.id === recordId ? updated : r)
                    )
                  } catch (err) {
                    console.error('Failed to update asset record:', err)
                  }
                }}
              />
            </div>
          )}

          {moduleGridView && showFlatModuleGrid ? (
            <WorkspaceModuleGridPanel moduleId={processingMode === 'AP' ? 'ap' : 'ar'} />
          ) : showWorkspaceWelcome ? (
            <div className="workspace-welcome-host">
              <WorkspaceWelcome
                onChooseDocumentMode={handleWelcomeNewTaskWithMode}
              />
            </div>
          ) : (
          <div className="message-list" ref={messageListRef}>
            {messages.map((message, index) => {
              const messagesWithSpreadsheet = messages.map((m, i) => m.spreadsheetData ? i : -1).filter(i => i !== -1)
              const lastSpreadsheetIndex = messagesWithSpreadsheet.length > 0 ? messagesWithSpreadsheet[messagesWithSpreadsheet.length - 1] : -1
              const isLastSpreadsheet = message.spreadsheetData && index === lastSpreadsheetIndex
              const arapTxnsForMessage = resolveArapTransactionsForMessage(
                message,
                activeTask?.processingMode ?? processingMode,
              )

              // Legacy OCR→RECON / RECON→REPORT stage nudges (no longer shown)
              if (
                message.stagePrompt?.fromStage === 'OCR'
                || message.stagePrompt?.fromStage === 'RECON'
              ) {
                return null
              }

              // Queue notice card
              if (message.content === '__QUEUE_NOTICE__') {
                return (
                  <div key={message.id} className="message-row assistant">
                    <div className="message-bubble">
                      <div className="queue-notice-card">
                        <span className="task-queued-icon" />
                        <div>
                          <strong>Task queued</strong>
                          <p>
                            {MAX_CONCURRENT_TASKS} task(s) are already processing; this task will start when a slot is free.
                            File status is shown next to each name in the messages above.
                          </p>
                          {activeTask && activeTask.fileQueue.length > 0 && (
                            <ul className="queue-notice-file-status-list">
                              {activeTask.fileQueue.map(f => (
                                <li key={f.id} className="queue-notice-file-status-row">
                                  <span className="queue-notice-file-name" title={f.file.name}>{f.file.name}</span>
                                  <span className={`queue-notice-file-status queue-notice-file-status--${f.status}`}>
                                    {ocrFileStatusEn(f.status)}
                                  </span>
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                )
              }

              return (
                <div key={message.id} className={`message-row ${message.role}`}>
                  <div className={`message-bubble${message.dupAlertType === 'warn' ? ' dup-warn-bubble' : ''}${message.dupAlertType === 'cancel' ? ' dup-cancel-bubble' : ''}`}>
                    {message.isTyping && message.typingFullContent ? (
                      <TypewriterText
                        text={message.typingFullContent}
                        onComplete={() => {
                          setTasks(prev => prev.map(t => ({
                            ...t,
                            messages: t.messages.map(m =>
                              m.id === message.id
                                ? { ...m, isTyping: false, content: m.typingFullContent ?? m.content }
                                : m
                            ),
                          })))
                        }}
                      />
                    ) : message.role === 'assistant' &&
                        message.content === AI_CHAT_THINKING_PLACEHOLDER &&
                        !message.typingFullContent ? (
                      <AiChatThinkingIndicator />
                    ) : (
                      message.content
                    )}

                    {message.dupConfirmPending && message.dupConfirmId && (
                      <div className="dup-confirm-card">
                        <div className="dup-confirm-header">
                          <span className="dup-confirm-icon">!</span>
                          <strong>Duplicate file</strong>
                        </div>
                        <p className="dup-confirm-body">
                          {message.dupFileNames || 'Selected file'} already exists. Continuing may create duplicate transactions.
                        </p>
                        <div className="dup-confirm-actions">
                          <button
                            type="button"
                            className="dup-confirm-btn continue"
                            onClick={() => handleDupConfirm(message.dupConfirmId!)}
                          >
                            Continue
                          </button>
                          <button
                            type="button"
                            className="dup-confirm-btn cancel"
                            onClick={() => handleDupCancel(message.dupConfirmId!)}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    )}

                    {/* Save Rule pending — show "Save Rule" button */}
                    {!message.isTyping && message.saveRulePending && message.saveRuleProposal && (
                      <div style={{ marginTop: 10, padding: '8px 12px', background: '#eff6ff', borderRadius: 8, border: '1px solid #bfdbfe' }}>
                        <div style={{ fontSize: 12, color: '#2563eb', fontWeight: 600, marginBottom: 6 }}>Save to rules memory?</div>
                        <div style={{ fontSize: 12, color: '#333', marginBottom: 8 }}>
                          <strong>{message.saveRuleProposal.vendor || '(keyword rule)'}</strong>
                          {' → '}
                          <strong>{message.saveRuleProposal.field}</strong>: {message.saveRuleProposal.value}
                        </div>
                        <button
                          type="button"
                          onClick={() => {
                            setInput('確認')
                            setTimeout(() => {
                              const sendBtn = document.querySelector<HTMLButtonElement>('.send-button, button[aria-label="send"], button[type="submit"]')
                              if (sendBtn) sendBtn.click()
                            }, 50)
                          }}
                          style={{ padding: '4px 14px', background: '#111827', color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 12, marginRight: 6 }}
                        >
                          ✓ Save rule
                        </button>
                        <span style={{ fontSize: 11, color: '#777' }}>Or reply "confirm" / "yes"</span>
                      </div>
                    )}

                    {/* Rule saved confirmation */}
                    {!message.isTyping && message.ruleSaved && message.ruleSavedMessage && (
                      <div style={{ marginTop: 8, padding: '8px 12px', background: '#f0fdf4', borderRadius: 6, border: '1px solid #bbf7d0', fontSize: 12, color: '#16a34a', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                        <span>✓ {message.ruleSavedMessage}</span>
                        <button
                          type="button"
                          onClick={() => { setShowSettings(true); setOpenSettingsToMemory(true) }}
                          style={{ fontSize: 11, padding: '3px 9px', background: '#fff', border: '1px solid #86efac', borderRadius: 5, cursor: 'pointer', color: '#16a34a', fontWeight: 600, whiteSpace: 'nowrap' }}
                        >
                          View in Rules Memory →
                        </button>
                      </div>
                    )}

                    {!message.isTyping && message.reconRedirect
                      && (processingMode === 'AR' || processingMode === 'AP' || processingMode === 'BANK') && (
                      <div style={{ marginTop: 10, padding: '8px 12px', background: '#f5f3ff', borderRadius: 8, border: '1px solid #c4b5fd' }}>
                        <div style={{ fontSize: 12, color: '#5b21b6', fontWeight: 600, marginBottom: 6 }}>
                          GL journal
                        </div>
                        <p style={{ fontSize: 13, color: '#111827', margin: '0 0 8px', whiteSpace: 'pre-wrap', fontWeight: 600, lineHeight: 1.45 }}>
                          {message.reconRedirect.reason_en || message.reconRedirect.reason_zh}
                        </p>
                        <button
                          type="button"
                          className="stage-prompt-btn primary"
                          style={{ fontSize: 12 }}
                          onClick={() => {
                            try {
                              const gld = message.reconRedirect?.gl_display?.trim()
                              if (gld) {
                                sessionStorage.setItem('recon_nav_gl_display', gld)
                                const refMap = glVoucherNoByGroupIdRef.current
                                const norm = (s: string) => s.toLowerCase().replace(/\s+/g, '')
                                const t = norm(gld)
                                let gid = ''
                                for (const [g, v] of Object.entries(refMap)) {
                                  if (v && norm(v) === t) {
                                    gid = g
                                    break
                                  }
                                }
                                if (!gid) {
                                  const m = gld.match(/GL-?\s*0*(\d+)/i)
                                  if (m) {
                                    const num = m[1].replace(/^0+/, '') || '0'
                                    for (const [g, v] of Object.entries(refMap)) {
                                      const vm = (v || '').match(/GL-?\s*0*(\d+)/i)
                                      if (vm && (vm[1].replace(/^0+/, '') || '0') === num) {
                                        gid = g
                                        break
                                      }
                                    }
                                  }
                                }
                                if (gid) sessionStorage.setItem('recon_nav_group_id', gid)
                              }
                            } catch { /* ignore */ }
                            startReconMode()
                          }}
                        >
                          Go to BANK workspace
                        </button>
                      </div>
                    )}

                    {message.reconActionsPending && message.reconActions && message.reconActions.length > 0 && (
                      <div className="recon-ai-actions-card">
                        <div className="recon-ai-actions-card__row">
                          <div style={{ fontSize: 12, color: '#92400e', fontWeight: 600 }}>
                            Suggested actions
                          </div>
                          <button
                            type="button"
                            className="recon-ai-actions-card__apply-btn"
                            onClick={() => { void handleApplyReconAiActions(message.id) }}
                          >
                            Apply
                          </button>
                        </div>
                        {message.isTyping ? (
                          <p className="recon-ai-actions-card__hint">
                            Reply still streaming — you can apply now.
                          </p>
                        ) : null}
                        <pre className="recon-ai-actions-card__json">
                          {JSON.stringify(message.reconActions, null, 2)}
                        </pre>
                      </div>
                    )}

                    {!message.isTyping && message.redirectTasks && message.redirectTasks.length > 0 && (
                      <div style={{ marginTop: 10, display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
                        <span style={{ fontSize: 12, color: '#4b5563' }}>Related tasks:</span>
                        {message.redirectTasks.map((rt) => (
                          <button
                            key={rt.task_id}
                            type="button"
                            className="stage-prompt-btn primary"
                            style={{ fontSize: 12 }}
                            onClick={() => openRedirectTaskFromRecon(rt.task_id, rt.mode)}
                            title={rt.reason || rt.title || rt.task_id}
                          >
                            Open task{rt.title ? `: ${rt.title}` : ''}
                          </button>
                        ))}
                      </div>
                    )}

                    {message.csvHint && (() => {
                      const sample = csvSampleForMode(
                        activeTask?.processingMode || processingMode,
                      )
                      if (!sample) return null
                      return (
                        <a
                          href={sample.href}
                          download={sample.download}
                          style={{ display: 'block', marginTop: 6 }}
                        >
                          (CSV template)
                        </a>
                      )
                    })()}
                    {typeof message.progressPercent === 'number' && (
                      <div className="chat-progress">
                        <div className="chat-progress-label">{message.progressLabel || 'Processing'}</div>
                        {message.progressMeta && (
                          <div className="chat-progress-label">
                            {`File ${message.progressMeta.fileIndex}/${message.progressMeta.totalFiles} · processing ${message.progressMeta.processingFiles}`}
                            {` · page ${message.progressMeta.pageCurrent || 1}/${message.progressMeta.pageTotal || 1}`}
                            {message.progressMeta.pageVerification && Object.keys(message.progressMeta.pageVerification).length > 0
                              ? (() => {
                                  const bad = Object.entries(message.progressMeta.pageVerification)
                                    .filter(([, v]) => v === 'needs_review')
                                    .map(([p]) => `P${p}`)
                                  return bad.length === 0
                                    ? ' · Dual model: all pages verified'
                                    : ` · Dual model needs review: ${bad.join(', ')}`
                                })()
                              : ''}
                          </div>
                        )}
                        <div className="chat-progress-track">
                          <div className="chat-progress-fill" style={{ width: `${message.progressPercent}%` }} />
                        </div>
                        {message.progressJob && message.progressPercent < 100 && (
                          <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                            <button
                              type="button"
                              className="stage-prompt-btn ghost"
                              style={{ fontSize: 12, padding: '4px 10px' }}
                              onClick={() => attachFileInputRef.current?.click()}
                            >
                              Add file
                            </button>
                            <button
                              type="button"
                              className="stage-prompt-btn ghost"
                              style={{ fontSize: 12, padding: '4px 10px', color: '#b91c1c', borderColor: '#fecaca' }}
                              onClick={() => { void handleCancelUploadJob(message) }}
                            >
                              Cancel
                            </button>
                          </div>
                        )}
                      </div>
                    )}

                    {isOcrSummaryMessage(message) && (message.ocrResult || message.fullOcrText) && (() => {
                      const fullOcrText = message.fullOcrText || extractFullOcrText(message.ocrResult)
                      if (!fullOcrText) return null
                      const isHtml = looksLikeHtmlTable(fullOcrText)
                      return (
                        <details className="ocr-expand-block">
                          <summary>Show full OCR ({fullOcrText.length} characters{isHtml ? ', includes HTML table' : ''})</summary>
                          <pre className="ocr-expand-content">{fullOcrText}</pre>
                        </details>
                      )
                    })()}

                    {message.uploadedFiles && message.uploadedFiles.length > 0 && (
                      <div className="message-file-list">
                        {message.uploadedFiles.map((file) => {
                          const showArapRetry =
                            (processingMode === 'AR' || processingMode === 'AP') &&
                            file.status === 'failed'
                          return (
                            <div key={file.id} className="message-file-row">
                              <button
                                type="button"
                                className="message-file-link"
                                onClick={() => previewQueuedFile(file)}
                              >
                                <span className="file-icon">FILE</span>
                                <span className="file-link-name">{file.file.name}</span>
                                <span className={`file-status file-status--${file.status}`}>
                                  {ocrFileStatusEn(file.status)}
                                </span>
                              </button>
                              {showArapRetry && (
                                file.file instanceof File ? (
                                  <button
                                    type="button"
                                    className="message-file-retry"
                                    onClick={(e) => {
                                      e.stopPropagation()
                                      handleRetryOcrFile(file.id)
                                    }}
                                  >
                                    Retry
                                  </button>
                                ) : (
                                  <span className="message-file-no-retry">Cannot retry, please upload again.</span>
                                )
                              )}
                            </div>
                          )
                        })}
                      </div>
                    )}
                    {/* After refresh: live File objects are gone — restore from backend storage */}
                    {!message.uploadedFiles?.length && message.fileRefs && message.fileRefs.length > 0 && (
                      <div className="message-file-list">
                        {message.fileRefs.map((ref) => (
                          <button
                            key={ref.id || ref.name}
                            className="message-file-link"
                            title={ref.id ? 'Click to preview' : 'File ID is not synced yet. Try again shortly.'}
                            onClick={() => {
                              if (!ref.id || !activeTaskId) return
                              void filePreview.openPreview(ref.id)
                            }}
                          >
                            <span className="file-icon">FILE</span>
                            <span className="file-link-name">{ref.name}</span>
                            <span className="file-status status-completed">OK</span>
                          </button>
                        ))}
                      </div>
                    )}

                    {message.reconRecords && (
                      <div className="recon-records">
                        {message.reconRecords.length === 0 ? (
                          <div className="recon-empty">No historical records to reconcile</div>
                        ) : (
                          message.reconRecords.map((record) => (
                            <details key={record.id} className="recon-record">
                              <summary>
                                <span className="recon-record-title">{record.title}</span>
                                <span className="recon-record-meta">{record.processingMode || 'AR'} · {record.fileCount} file(s) · {record.pageCount} page(s)</span>
                              </summary>
                              {record.spreadsheetData && record.spreadsheetData.length > 0 ? (
                                <EditableSpreadsheet data={record.spreadsheetData} readOnly categoryOptions={getCategoryOptionsForRows(record.spreadsheetData)} />
                              ) : (
                                <div className="recon-empty">This record has no table data</div>
                              )}
                            </details>
                          ))
                        )}
                      </div>
                    )}

                    {/* reconContainer: ReconContainer moved to right panel — only text content shown here */}

                    {message.reconUnmatched && (
                      <div className="recon-unmatched">
                        <div className="recon-section-header">Unmatched - Bank</div>
                        {message.reconUnmatched.bank.length > 0 ? (
                          <EditableSpreadsheet data={message.reconUnmatched.bank} enableRowExpand categoryOptions={getCategoryOptionsForMode('BANK')} onDataChange={(updatedData) => handleReconUnmatchedChange(message.id, 'bank', updatedData)} />
                        ) : <div className="recon-empty">No unmatched bank transactions</div>}
                        <div className="recon-section-header">Unmatched - AR/AP</div>
                        {message.reconUnmatched.ledger.length > 0 ? (
                          <EditableSpreadsheet data={message.reconUnmatched.ledger} enableRowExpand categoryOptions={getCategoryOptionsForMode('RECON')} onDataChange={(updatedData) => handleReconUnmatchedChange(message.id, 'ledger', updatedData)} />
                        ) : <div className="recon-empty">No unmatched AR/AP transactions</div>}
                      </div>
                    )}

                    {/* Bank mode: dedicated review UI */}
                    {message.bankTransactions && (
                      <div id={`ocr-bank-table-${message.id}`} className="ocr-review-table-anchor">
                      <BankStatementReview
                        transactions={message.bankTransactions}
                        filename={message.bankFilename}
                        coaOptions={getCategoryOptionsForMode('RECON')}
                        isCashTable={message.isCashTable}
                        onDeploy={message.isCashTable ? undefined : () => handleDeployCodes(message.id, 'BANK')}
                        reconState={message.isCashTable ? undefined : reconState}
                        onUnlock={message.isCashTable ? undefined : handleReconUnlock}
                        glPostedBankLockKeys={glPostedBankLockKeys}
                        glVoucherNoByGroupId={glVoucherNoByGroupId}
                        onDataChange={(updated) => {
                          suppressScrollRef.current = true
                          if (!activeTaskId) { suppressScrollRef.current = false; return }
                          setMessages((prev) =>
                            prev.map((m) => m.id === message.id ? { ...m, bankTransactions: updated } : m)
                          )
                          const merged = mergeBankMessagesForOcrSnapshot([{ ...message, bankTransactions: updated }])
                          const content = mergedBankOcrSnapshotContent(merged, message.content)
                          debouncedSaveSnapshot(activeTaskId, message.id, content, {
                            spreadsheetData: merged.spreadsheetData,
                            bankTransactions: merged.bankTransactions,
                            bankFilename: merged.bankFilename,
                            fileRefs: merged.fileRefs,
                          }, activeCompany?.id)
                          scheduleOcrAccountCodePersist(activeTaskIdRef.current, message.id, 'bank')
                        }}
                      />
                      </div>
                    )}

                    {/* AR/AP mode: dedicated JSON table */}
                    {arapTxnsForMessage && arapTxnsForMessage.length > 0 && (
                      <div
                        id={`ocr-arap-table-${message.id}`}
                        className="ocr-review-table-anchor"
                        onDragOver={handleArapTableDragOver}
                        onDrop={handleArapTableDropForMessage(message.id)}
                      >
                      <ARAPReview
                        key={`${message.id}-arap`}
                        transactions={arapTxnsForMessage}
                        filename={message.arapFilename}
                        useApTableSchema={message.apVlmTablePreset === 'ap_table'}
                        isProcessing={activeTask?.status === 'processing'}
                        completedFiles={activeTask?.fileQueue.filter(f => f.status === 'completed' || f.status === 'failed').length ?? 0}
                        totalFiles={activeTask?.fileQueue.length ?? 0}
                        coaOptionsByType={{
                          AR: getCategoryOptionsForMode('RECON'),
                          AP: getCategoryOptionsForMode('RECON'),
                        }}
                        onDeploy={() => handleDeployCodes(message.id, activeTask?.processingMode ?? 'AR')}
                        reconState={reconState}
                        onUnlock={handleReconUnlock}
                        glPostedLedgerLockKeys={glPostedLedgerLockKeys}
                        glVoucherNoByGroupId={glVoucherNoByGroupId}
                        crossTableMoveEnabled={false}
                        messageId={message.id}
                        onRetryOcrPage={(jobId, page) =>
                          void handleRetryOcrFailedPage(message.id, jobId, page)
                        }
                        onDataChange={(updated) => {
                          suppressScrollRef.current = true
                          if (!activeTaskId) { suppressScrollRef.current = false; return }
                          const prevInferred = inferHomogeneousArapMode(message.arapTransactions)
                          const nextInferred = inferHomogeneousArapMode(updated)
                          const taskBefore = tasksRef.current.find(t => t.id === activeTaskId)
                          const pmBefore = taskBefore?.processingMode

                          setMessages((prev) =>
                            prev.map((m) => m.id === message.id ? { ...m, arapTransactions: updated } : m)
                          )

                          if (
                            pmBefore === 'AR' || pmBefore === 'AP'
                          ) {
                            if (nextInferred && (pmBefore === 'AR' || pmBefore === 'AP') && nextInferred !== pmBefore) {
                              setTasks(prev => {
                                const next = prev.map(t =>
                                  t.id === activeTaskId ? { ...t, processingMode: nextInferred } : t
                                )
                                _writeLocalCache(next)
                                return next
                              })
                              patchTaskMetadataFireAndForget(activeTaskId, { processing_mode: nextInferred })
                              if (
                                activeTaskIdRef.current === activeTaskId &&
                                (processingModeRef.current === 'AR' || processingModeRef.current === 'AP')
                              ) {
                                setProcessingMode(nextInferred)
                              }
                              const label = nextInferred === 'AP'
                                ? 'Accounts Payable (AP)'
                                : 'Accounts Receivable (AR)'
                              window.alert(
                                `This chat task was moved to the ${label} folder to match the Type column.\nTo undo, change all rows back to the previous type.`
                              )
                            } else if (prevInferred && !nextInferred) {
                              window.alert(
                                'The table mixes AR and AP (or has incomplete types). The sidebar folder is unchanged. When every row is the same type, the folder will update automatically.'
                              )
                            }
                          }

                          // Read fresh message state from ref to avoid stale closure
                          const freshTask = tasksRef.current.find(t => t.id === activeTaskId)
                          const freshMsg = freshTask?.messages.find(m => m.id === message.id)
                          debouncedSaveSnapshot(activeTaskId, message.id, freshMsg?.content ?? message.content, {
                            spreadsheetData: freshMsg?.spreadsheetData ?? message.spreadsheetData,
                            arapTransactions: updated,
                            arapFilename: freshMsg?.arapFilename ?? message.arapFilename,
                            fileRefs: freshMsg?.fileRefs ?? message.fileRefs,
                            apVlmTablePreset: freshMsg?.apVlmTablePreset ?? message.apVlmTablePreset,
                          }, activeCompany?.id)
                          scheduleOcrAccountCodePersist(activeTaskIdRef.current, message.id, 'ledger')
                          scheduleOcrLedgerDocTypePersist(activeTaskIdRef.current, message.id)
                        }}
                      />
                      </div>
                    )}

                    {/* Document Gate confirmation card */}
                    {message.gateCard && (
                      <div className="gate-card">
                        <div className="gate-card-icon">
                          {message.gateCard.gateResult === 'REFERENCE_FINANCIAL' ? 'REF' : '!'}
                        </div>
                        <div className="gate-card-body">
                          <div className="gate-card-title">
                            {message.gateCard.gateResult === 'REFERENCE_FINANCIAL'
                              ? `Financial Reference Document Detected`
                              : message.gateCard.gateResult === 'NON_FINANCIAL'
                              ? `Non-Financial Document`
                              : `Document Type Unclear`}
                          </div>
                          <div className="gate-card-file">{message.gateCard.fileName}</div>
                          <div className="gate-card-message">{message.gateCard.gateMessage}</div>
                          {message.gateCard.gateResult !== 'NON_FINANCIAL' && (
                            <div className="gate-card-actions">
                              {message.gateCard.gateResult === 'REFERENCE_FINANCIAL' && (
                                <button
                                  className="gate-btn gate-btn-primary"
                                  onClick={async () => {
                                    try {
                                      const routed = await api.routeToOther({
                                        source_task_id: message.gateCard!.sourceTaskId,
                                        document_subtype: message.gateCard!.documentSubtype as 'loan' | 'fixed_asset',
                                        ocr_text: message.gateCard!.ocrText,
                                        gate_document_hint: message.gateCard!.gateResult,
                                      })
                                      setMessages(prev => prev.map(m =>
                                        m.id === message.id
                                          ? { ...m, gateCard: undefined, content: `✓ Routed to Other task (Task: ${routed.task_id.slice(0, 8)}...)` }
                                          : m
                                      ))
                                    } catch (err) {
                                      alert('Failed to route document: ' + String(err))
                                    }
                                  }}
                                >
                                  Route to Other
                                </button>
                              )}
                              <button
                                className="gate-btn gate-btn-secondary"
                                onClick={async () => {
                                  setMessages(prev => prev.map(m =>
                                    m.id === message.id ? { ...m, gateCard: undefined, content: '⏭ Processing anyway...' } : m
                                  ))
                                  const form = new FormData()
                                  form.append('file', message.gateCard!.originalFile)
                                  form.append('processing_mode', message.gateCard!.processingMode)
                                  form.append('force_process', 'true')
                                  try {
                                    const res = await apiFetch('/ocr/test', { method: 'POST', body: form })
                                    const reResult = await res.json()
                                    setMessages(prev => prev.map(m =>
                                      m.id === message.id ? { ...m, content: 'Document processed (forced).', ocrResult: reResult } : m
                                    ))
                                  } catch { /* ignore */ }
                                }}
                              >
                                Process anyway
                              </button>
                              <button
                                className="gate-btn gate-btn-ghost"
                                onClick={() => setMessages(prev => prev.map(m =>
                                  m.id === message.id ? { ...m, gateCard: undefined, content: '⏭ Document skipped.' } : m
                                ))}
                              >
                                Skip
                              </button>
                            </div>
                          )}
                          {message.gateCard.gateResult === 'NON_FINANCIAL' && (
                            <div className="gate-card-actions">
                              <button
                                className="gate-btn gate-btn-ghost"
                                onClick={() => setMessages(prev => prev.map(m =>
                                  m.id === message.id ? { ...m, gateCard: undefined, content: '⏭ Document skipped.' } : m
                                ))}
                              >
                                Dismiss
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    )}

                    {/* REPORT mode: setup card (Step 3 of pre-dialogue) */}
                    {message.reportSetupCard && (
                      <ReportSetupCard
                        data={message.reportSetupCard}
                        onGenerate={(opts) => void handleGenerateReport(opts)}
                      />
                    )}

                    {message.reportGlDraftConflict && (
                      <ReportGlDraftPickCard
                        conflicts={message.reportGlDraftConflict.conflicts}
                        baseOpts={message.reportGlDraftConflict.baseOpts}
                        onPick={(groupKey, journalId) => {
                          const c = message.reportGlDraftConflict!
                          void handleGenerateReport({
                            ...c.baseOpts,
                            glDraftPicks: { ...c.accumulatedPicks, [groupKey]: journalId },
                          })
                        }}
                      />
                    )}

                    {/* financialReportData: FinancialReportsView moved to right panel — only text content shown here */}

                    {/* Fallback: existing editable spreadsheet (RECON and other modes) */}
                    {message.spreadsheetData && !message.bankTransactions && !arapTxnsForMessage?.length && (
                      <div
                        id={`ocr-arap-table-${message.id}`}
                        className="ocr-review-table-anchor"
                      >
                      <EditableSpreadsheet
                        data={message.spreadsheetData}
                        columnsOverride={message.spreadsheetColumns}
                        headersOverride={message.spreadsheetHeaders}
                        readOnly={message.spreadsheetReadOnly}
                        enableRowExpand={false}
                        categoryOptions={getCategoryOptionsForRows(message.spreadsheetData)}
                        onDataChange={message.spreadsheetReadOnly ? undefined : (updatedData) => {
                          suppressScrollRef.current = true
                          if (!activeTaskId) { suppressScrollRef.current = false; return }
                          setMessages((prev) => {
                            return prev.map((m) => m.id === message.id ? { ...m, spreadsheetData: updatedData } : m)
                          })
                          updateTask(activeTaskId, t => ({ ...t, spreadsheetData: updatedData }))
                          if (message.contentType === 'ocr_snapshot') {
                            debouncedSaveSnapshot(activeTaskId, message.id, message.content, {
                              spreadsheetData: updatedData,
                              fileRefs: message.fileRefs,
                            }, activeCompany?.id)
                          }
                        }}
                      />
                      </div>
                    )}

                    {message.reconciliationResults && processingMode === 'BANK' && isLastSpreadsheet && (
                      <ReconciliationTable
                        matchedPairs={message.reconciliationResults.matches || []}
                        unmatchedBank={message.reconciliationResults.unmatched_bank_txns || []}
                        unmatchedLedger={message.reconciliationResults.unmatched_ledger_txns || []}
                      />
                    )}
                  </div>
                </div>
              )
            })}

          </div>
          )}

          {/* Keep composer on step 1 even on welcome — otherwise typed input cannot run ensureComposerTaskIfTyping. */}
          {(!showWorkspaceWelcome || currentStep === 1) && !moduleGridView ? (
          <div
            className="composer"
            onDragOver={handleComposerDragOver}
            onDrop={handleComposerDrop}
          >
            <div className="composer-input-wrapper" ref={composerHubDismissBoundsRef}>
              {currentStep === 1 ? (
                <ComposerWorkspaceHub
                  disabled={isProcessing}
                  processingMode={processingMode}
                  onSelectMode={setProcessingMode}
                  apReceiptSignal={apReceiptSignal}
                  apTablePreset={apTablePreset}
                  onApReceiptSignal={setApReceiptSignal}
                  onApTablePreset={setApTablePreset}
                  onBankInsertCashTable={() => setInput('Create cash table')}
                  hubDismissBoundsRef={composerHubDismissBoundsRef}
                />
              ) : null}
              <div className="composer-input-main">
                <input
                  type="text"
                  placeholder={processingMode === 'REPORT' ? 'Type a command or click Generate report...' : 'Type a message...'}
                  value={input}
                  onChange={handleComposerInputChange}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') handleSend()
                  }}
                />
              </div>
              <div className="composer-actions">
                <button className="composer-icon-btn" onClick={() => attachFileInputRef.current?.click()} title="Attach file">
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <path d="M14 10V12.5C14 13.163 13.7366 13.7989 13.2678 14.2678C12.7989 14.7366 12.163 15 11.5 15H4.5C3.83696 15 3.20107 14.7366 2.73223 14.2678C2.26339 13.7989 2 13.163 2 12.5V10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                    <path d="M11 5L8 2L5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                    <path d="M8 2V10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </button>
                <button className="composer-send-btn" onClick={handleSend} disabled={isProcessing} title="Send message" data-mode={processingMode}>
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <path d="M8 14L8 2M8 2L3 7M8 2L13 7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </button>
              </div>
            </div>

          </div>
          ) : null}
        </section>

        {/* Right resize handle — desktop only */}
        {isDesktop && (
          <div
            className={`panel-resize-handle${isRightPanelResizing ? ' resizing' : ''}`}
            onMouseDown={handleRightPanelResizeStart}
          />
        )}

        {/* Right Panel — inline on desktop, drawer on mobile/tablet */}
        {!isDesktop && workspaceOpen && (
          <div className="drawer-backdrop drawer-backdrop--right" onClick={() => setWorkspaceOpen(false)} />
        )}
        <div className={`workspace-drawer${!isDesktop ? (workspaceOpen ? ' workspace-drawer--open' : ' workspace-drawer--closed') : ''}`}>
          <RightPanel
            mode={processingMode}
            mdContent={mdContent}
            extractionTask={extractionTask}
            onOcrDestination={handleOcrDestination}
            financialReportData={latestFinancialReportData}
            coaList={coaList}
            sourcePoolTransactions={sourcePoolTransactions}
            bankPoolTransactions={bankPoolTransactions}
            reconSelectedSourceTxnIds={reconSelectedSourceTxnIds}
            reconSelectedBankTxnIds={reconSelectedBankTxnIds}
            reconMatchedSourceUids={reconMatchedSourceUids}
            reconMatchedBankUids={reconMatchedBankUids}
            reconMatchResult={reconMatchResult}
            isProcessing={isProcessing}
            onAllDrag={handleReconAllDrag}
            onClearContainer={handleReconClearContainer}
            onSelectSource={handleReconSelectSource}
            onSelectBank={handleReconSelectBank}
            onRemoveSource={handleReconRemoveSource}
            onRemoveBank={handleReconRemoveBank}
            onRunMatch={handleRunRecon}
            onCheckDuplicates={runDuplicateScan}
            reconStatusText={reconStatusText}
            reconMatchedRows={reconMatchedRows}
            reconMatchedColumns={reconMatchedColumns}
            setReconMatchedRows={setReconMatchedRows}
            reconMatchedGroups={reconMatchedGroups}
            setReconMatchedGroups={setReconMatchedGroups}
            reconUnmatchedTxns={reconUnmatchedTxns}
            setReconUnmatchedTxns={setReconUnmatchedTxns}
            reconUnmatchedRows={reconUnmatchedRows}
            setReconUnmatchedRows={setReconUnmatchedRows}
            reconPartialTxns={reconPartialTxns}
            setReconPartialTxns={setReconPartialTxns}
            setReconStatusText={setReconStatusText}
            duplicateAlerts={duplicateAlerts}
            onDuplicateResolve={handleDuplicateResolve}
            getCategoryOptionsForMode={getCategoryOptionsForMode}
            onMatchedIdsUpdate={handleReconMatchedIdsUpdate}
            onGroupUnmatched={handleReconGroupUnmatched}
            onReconMatchComplete={applyReconMultiManualMatchResult}
            onReconGroupRemoved={removeReconMatchedGroupById}
            onReconGroupsRefresh={refreshReconMatchedGroupsFromApi}
            onRestoreMemberToUnmatched={restoreReconMemberToUnmatched}
            onGlAccountCodesSynced={sync => applyAccountCategorySyncFromDb(sync.bank, sync.ledger)}
            glJournalRefetchSignal={glJournalRefetchSignal}
            glApplyPatchSeeds={glApplyPatchSeeds}
            onPrimaryJournalStatusByGroup={handlePrimaryJournalStatusByGroup}
            glVoucherNoByGroupId={glVoucherNoByGroupId}
            onGlVoucherNoByGroup={handleGlVoucherNoByGroup}
            reconLedgerOnlyPools={reconLedgerOnlyPools}
            reconScrollTargetGroupId={reconScrollTargetGroupId}
            onReconScrollTargetConsumed={() => setReconScrollTargetGroupId(null)}
          />
        </div>
      </main>

      {reconAiAccountCodeConfirm && (
        <div
          role="presentation"
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(15, 23, 42, 0.4)',
            zIndex: 10050,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 16,
          }}
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setReconAiAccountCodeConfirm(null)
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="recon-ai-acct-title"
            style={{
              background: '#fff',
              borderRadius: 12,
              maxWidth: 440,
              width: '100%',
              padding: '20px 22px',
              boxShadow: '0 20px 40px rgba(0,0,0,0.18)',
            }}
            onMouseDown={ev => ev.stopPropagation()}
          >
            <h3 id="recon-ai-acct-title" style={{ margin: '0 0 10px', fontSize: 16, fontWeight: 700 }}>
              Change account codes?
            </h3>
            <p style={{ margin: '0 0 6px', fontSize: 13, color: '#111827', fontWeight: 600, lineHeight: 1.45 }}>
              The AI proposal changes account codes on these draft lines. Confirm before applying.
            </p>
            <p style={{ margin: '0 0 12px', fontSize: 12, color: '#6b7280', lineHeight: 1.45 }}>
              The AI proposal changes account codes on existing journal lines. Confirm to apply.
            </p>
            <ul style={{ margin: '0 0 16px', paddingLeft: 18, fontSize: 12, color: '#374151' }}>
              {reconAiAccountCodeConfirm.changes.map(c => (
                <li key={`${c.groupId}-${c.lineId}`} style={{ marginBottom: 6 }}>
                  <code style={{ fontSize: 11 }}>{c.lineId.slice(0, 8)}…</code>
                  {' · '}
                  <strong>{c.oldCode}</strong>
                  {' → '}
                  <strong>{c.newCode}</strong>
                </li>
              ))}
            </ul>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
              <button
                type="button"
                className="stage-prompt-btn ghost"
                onClick={() => setReconAiAccountCodeConfirm(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="stage-prompt-btn primary"
                onClick={() => {
                  const mid = reconAiAccountCodeConfirm.messageId
                  setReconAiAccountCodeConfirm(null)
                  void handleApplyReconAiActions(mid, { skipAccountCodeConfirm: true })
                }}
              >
                Confirm apply
              </button>
            </div>
          </div>
        </div>
      )}

      {apComposerDialog === 'incomplete_upload' && (
        <div
          role="presentation"
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(15, 23, 42, 0.45)',
            zIndex: 10055,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 16,
          }}
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setApComposerDialog(null)
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="ap-opt-incomplete-title"
            style={{
              background: '#fff',
              borderRadius: 12,
              maxWidth: 460,
              width: '100%',
              padding: '20px 22px',
              boxShadow: '0 20px 40px rgba(0,0,0,0.18)',
              maxHeight: '90vh',
              overflowY: 'auto',
            }}
            onMouseDown={ev => ev.stopPropagation()}
          >
            <h3 id="ap-opt-incomplete-title" style={{ margin: '0 0 12px', fontSize: 16, fontWeight: 700 }}>
              Select AP options before uploading
            </h3>
            <p style={{ margin: '0 0 10px', fontSize: 13, color: '#374151', lineHeight: 1.5 }}>
              Please choose receipt layout and table style for OCR/VLM below. Attach is blocked until both are selected.
            </p>
            {apReceiptSignal === null ? (
              <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#92400e' }}>Missing: receipt layout</div>
                <ApModalReceiptPickList selected={apReceiptSignal} onSelect={v => { setApReceiptSignal(v) }} />
              </div>
            ) : null}
            {apTablePreset === null ? (
              <div style={{ marginTop: 10 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#92400e' }}>Missing: table style</div>
                <ApModalTablePickList selected={apTablePreset} onSelect={v => { setApTablePreset(v) }} />
              </div>
            ) : null}
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 14 }}>
              <button
                type="button"
                className="stage-prompt-btn primary"
                onClick={() => setApComposerDialog(null)}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {apComposerDialog === 'typing_blocked' && (
        <div
          role="presentation"
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(15, 23, 42, 0.45)',
            zIndex: 10055,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 16,
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="ap-typing-block-title"
            style={{
              background: '#fff',
              borderRadius: 12,
              maxWidth: 420,
              width: '100%',
              padding: '20px 22px',
              boxShadow: '0 20px 40px rgba(0,0,0,0.18)',
            }}
            onMouseDown={ev => ev.stopPropagation()}
          >
            <h3 id="ap-typing-block-title" style={{ margin: '0 0 10px', fontSize: 16, fontWeight: 700 }}>
              Chat blocked until OCR starts
            </h3>
            <p style={{ margin: '0 0 14px', fontSize: 13, color: '#374151', lineHeight: 1.5 }}>
              With receipt layout and table style selected, messages cannot be sent until you attach files and OCR/VLM processing has started. This includes sends that only carry the AP option lines from the composer.
            </p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, justifyContent: 'flex-end' }}>
              <button
                type="button"
                className="stage-prompt-btn"
                onClick={() => {
                  apTypingBlockedPendingTextRef.current = ''
                  setApComposerDialog(null)
                }}
              >
                Dismiss
              </button>
              <button
                type="button"
                className="stage-prompt-btn primary"
                onClick={() => {
                  const t = apTypingBlockedPendingTextRef.current
                  apTypingBlockedPendingTextRef.current = ''
                  setApReceiptSignal(null)
                  setApTablePreset(null)
                  setInput(t)
                  setApComposerDialog(null)
                }}
              >
                Clear all options & restore text
              </button>
            </div>
          </div>
        </div>
      )}

      {ocrOverloadModal && (
        <div
          role="presentation"
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(15, 23, 42, 0.4)',
            zIndex: 10050,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 16,
          }}
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) setOcrOverloadModal(null)
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="ocr-overload-title"
            style={{
              background: '#fff',
              borderRadius: 12,
              maxWidth: 440,
              width: '100%',
              padding: '20px 22px',
              boxShadow: '0 20px 40px rgba(0,0,0,0.18)',
            }}
            onMouseDown={ev => ev.stopPropagation()}
          >
            <h3 id="ocr-overload-title" style={{ margin: '0 0 10px', fontSize: 16, fontWeight: 700 }}>
              Upload queue
            </h3>
            {ocrOverloadModal.ocr && (
              <p style={{ margin: '0 0 8px', fontSize: 13, color: '#374151', lineHeight: 1.5 }}>
                There is a limit on files scanned at once. Extra files wait in queue until a slot is free.
              </p>
            )}
            {ocrOverloadModal.task && (
              <p style={{ margin: '0 0 8px', fontSize: 13, color: '#374151', lineHeight: 1.5 }}>
                Several sessions are already processing. This queue will continue when a slot is free.
              </p>
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 14 }}>
              <button
                type="button"
                className="stage-prompt-btn primary"
                onClick={() => setOcrOverloadModal(null)}
              >
                OK
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Bottom navigation — mobile / tablet only */}
      {!isDesktop && (
        <MobileBottomNav
          activeTab={sidebarOpen ? 'tasks' : workspaceOpen ? 'workspace' : 'chat'}
          onTabChange={(tab) => {
            if (tab === 'tasks') { setSidebarOpen(true); setWorkspaceOpen(false) }
            else if (tab === 'workspace') { setWorkspaceOpen(true); setSidebarOpen(false) }
            else { setSidebarOpen(false); setWorkspaceOpen(false) }
          }}
          taskCount={sidebarTasks.filter(t => t.status === 'processing' || t.status === 'queued').length}
        />
      )}

      {showDashboard && (
        <DashboardPage onClose={() => setShowDashboard(false)} />
      )}

      <Settings
        isOpen={showSettings}
        onClose={() => { setShowSettings(false); setOpenSettingsToMemory(false) }}
        allTransactions={allTransactions}
        openToMemoryTab={openSettingsToMemory}
        onOpenChatWithMode={(mode) => {
          setShowSettings(false)
          setOpenSettingsToMemory(false)
          setInput(`Please review and update ${mode} rules memory`)
        }}
        onOpenWizard={() => {
          setShowSettings(false)
          setShowWizard(true)
        }}
      />

      {showWizard && (
        <OnboardingWizard
          onComplete={() => {
            setShowWizard(false)
            sessionStorage.setItem('wizard_dismissed', '1')
            // Refresh company list so new company appears in the switcher
            refreshCompanies()
          }}
          onSkip={() => {
            setShowWizard(false)
            sessionStorage.setItem('wizard_dismissed', '1')
          }}
        />
      )}

      {/* Company picker — shown post-login when user has 2+ companies and no saved choice */}
      {needsCompanyPick && companies.length > 1 && (
        <CompanyPickerModal
          companies={companies}
          onSelect={(id) => {
            handleSwitchCompany(id)
          }}
        />
      )}

      {taskNotifications.length > 0 && (
        <div className="task-notification-stack">
          {taskNotifications.map(n => (
            <div key={n.id} className="task-notification">
              <div className="task-notification-icon">✓</div>
              <div className="task-notification-body">
                <div className="task-notification-title">Task Completed</div>
                <div className="task-notification-sub">{n.title}</div>
              </div>
              <button className="task-notification-close" onClick={() => setTaskNotifications(ns => ns.filter(nn => nn.id !== n.id))}>×</button>
            </div>
          ))}
        </div>
      )}

      {arapMoveUndo && (
        <div
          className="task-notification-stack"
          style={{ bottom: 24, top: 'auto' }}
          role="status"
        >
          <div className="task-notification">
            <div className="task-notification-body">
              <div className="task-notification-title">Rows moved</div>
              <div className="task-notification-sub">Undo within a few seconds if this was a mistake.</div>
            </div>
            <button
              type="button"
              className="task-notification-close"
              style={{ position: 'static', marginRight: 4 }}
              onClick={() => {
                void handleArapMoveUndo()
              }}
            >
              Undo
            </button>
            <button
              type="button"
              className="task-notification-close"
              style={{ position: 'static' }}
              onClick={() => {
                clearArapMoveUndoTimer()
                setArapMoveUndo(null)
              }}
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {/* AR/AP cross-table move: pick target assistant message */}
      {arapMoveTargetModal && activeTaskId && (
        <div className="delete-confirm-overlay" onClick={() => setArapMoveTargetModal(null)}>
          <div className="delete-confirm-dialog" onClick={e => e.stopPropagation()}>
            <p className="delete-confirm-title">Move rows to</p>
            <p className="delete-confirm-body" style={{ marginBottom: 12 }}>
              Choose another AR/AP table in this task. Selected rows: {arapMoveTargetModal.rows.length}.
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 280, overflowY: 'auto' }}>
              {activeTask?.messages.filter(
                (m) =>
                  m.id !== arapMoveTargetModal.sourceMessageId &&
                  (m.arapTransactions?.length ?? 0) > 0,
              ).length === 0 ? (
                <p style={{ color: '#6b7280', fontSize: 14 }}>No other AR/AP tables in this task.</p>
              ) : (
                activeTask?.messages
                  .filter(
                    (m) =>
                      m.id !== arapMoveTargetModal.sourceMessageId &&
                      (m.arapTransactions?.length ?? 0) > 0,
                  )
                  .map((m) => (
                    <button
                      key={m.id}
                      type="button"
                      className="gate-btn gate-btn-primary"
                      style={{ justifyContent: 'flex-start', textAlign: 'left' }}
                      onClick={() => {
                        void executeArapCrossTableMove(
                          arapMoveTargetModal.sourceMessageId,
                          m.id,
                          arapMoveTargetModal.rows,
                        )
                      }}
                    >
                      {m.arapFilename || 'File'}
                    </button>
                  ))
              )}
            </div>
            <div className="delete-confirm-actions" style={{ marginTop: 16 }}>
              <button className="delete-confirm-cancel" type="button" onClick={() => setArapMoveTargetModal(null)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Delete-task confirmation modal ─────────────────────────────────── */}
      {deleteConfirm && (
        <div className="delete-confirm-overlay" onClick={() => setDeleteConfirm(null)}>
          <div className="delete-confirm-dialog" onClick={e => e.stopPropagation()}>
            <p className="delete-confirm-title">Delete Record</p>
            <p className="delete-confirm-body">
              Are you sure you want to delete{' '}
              <strong>「{deleteConfirm.taskTitle}」</strong>?
              {' '}This action cannot be undone.
            </p>
            {deleteConfirm.hasReconState && (
              <p className="delete-confirm-warning">
                ⚠ All reconciliation results linked to this record — including matched groups,
                unmatched transactions, and match history — will also be permanently removed.
              </p>
            )}
            <div className="delete-confirm-actions">
              <button className="delete-confirm-cancel" onClick={() => setDeleteConfirm(null)}>
                Cancel
              </button>
              <button
                className="delete-confirm-delete"
                onClick={() => _commitDeleteTask(deleteConfirm.taskId)}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      <FilePreviewModal
        open={filePreview.state.open}
        onClose={filePreview.closePreview}
        filename={filePreview.state.filename}
        mimeType={filePreview.state.mimeType}
        previewUrl={filePreview.state.previewUrl}
        loading={filePreview.state.loading}
        error={filePreview.state.error}
        files={filePreview.fileList}
        activeFileId={filePreview.state.activeFileId}
        onSelectFile={id => void filePreview.openPreview(id)}
        onRetry={filePreview.retryPreview}
        onDownload={filePreview.downloadActive}
      />
    </div>
  )
}
