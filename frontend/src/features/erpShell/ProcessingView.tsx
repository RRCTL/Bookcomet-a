import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { CLOUD_AI_DATA_NOTICE } from '../../constants/privacyNotices'
import { useAuth } from '../../contexts/AuthContext'
import { useResizeDrag } from '../../hooks/useResizeDrag'
import {
  workflowApi,
  applyRunStoppedLocally,
  applyRunReVlmLocally,
  runLooksProcessing,
  shouldIgnoreRunRefreshAfterStop,
  type WorkflowRunSummary,
  type WorkflowRun,
  type WorkflowGraph,
  type WorkflowTemplate,
  type WorkflowNodeCatalogEntry,
} from '../nodeWorkspace/workflowApi'
import {
  NODE_IO,
  graphWithDoubleCheckEnabled,
  graphWithDoubleCheckDisabled,
  layoutGraphVertical,
} from '../nodeWorkspace/defaultGraphs'
import {
  loadAllBatchTablePayloads,
  buildBatchTablePayloadsFromRun,
  reconcileBatchPayloadsWithRun,
  mergeBatchTablePayloads,
  combineBatchTablePayloads,
  mapCombinedPayloadToBatches,
  runHasLockedApprovedTable,
} from '../nodeWorkspace/batchTableSnapshots'
import { hasOcrDataOnRun } from '../nodeWorkspace/tablePayloadMerge'
import { safeRandomUUID } from '../../utils/safeRandomUUID'
import { coalesceBankAccountTypeRows } from '../../utils/bankAccountTypeCoalesce'

function resolveBatchPayloads(
  run: WorkflowRun,
  loaded: Record<string, Record<string, unknown>>,
): Record<string, Record<string, unknown>> {
  const built = buildBatchTablePayloadsFromRun(run)
  if (Object.keys(loaded).length === 0) return built
  const reconciled = reconcileBatchPayloadsWithRun(run, loaded)
  return mergeBatchTablePayloads(run, reconciled, built)
}
import { ARAPReview, type ARAPTransaction } from '../../components/ARAPReview'
import { BankStatementReview, type BankTransaction } from '../../components/BankStatementReview'
import { OtherTable } from '../../components/OtherTable'
import type { OtherRow } from '../../types/other'
import { api } from '../../services/api'
import { assetSourceLabel, filesByIdFromRun } from '../../utils/rowSourceLabel'
import { GridFooter } from './GridFooter'
import { FileStatusIcon } from '../nodeWorkspace/shell/FileStatusIcon'
import { FileStatusIconRow } from '../nodeWorkspace/shell/FileStatusIconRow'
import { formatFilePageCount } from '../nodeWorkspace/filePageLabel'
import { summaryFromRun } from '../nodeWorkspace/workflowRunStore'
import { useWorkflowRunEvents } from '../nodeWorkspace/useWorkflowRunEvents'
import { ReVlmModal } from '../nodeWorkspace/shell/ReVlmModal'
import { ConfirmDialog } from '../nodeWorkspace/shell/ConfirmDialog'
import type { ReVlmConfirmPayload } from '../nodeWorkspace/reVlmReasonChips'
import { receiptSettingsFromGraph } from '../nodeWorkspace/shell/graphReceiptSettings'
import {
  applyWorkflowSettingsToGraph,
  hasVlmProviderControl,
  patchProviderInGraph,
  providerFromGraph,
  providerOptionsFromCatalog,
  resolveProviderSelection,
  workflowSettingsChanged,
} from '../nodeWorkspace/shell/processingWorkflowHeader'
import {
  resolvePaletteTemplateSelection,
  sortPaletteTemplates,
  templateMatchingGraph,
} from '../nodeWorkspace/workflowTemplates'
import {
  AP_RECEIPT_OPTIONS_ORDER,
  AP_TABLE_OPTIONS_ORDER,
  type ApVlmReceiptSignal,
  type ApVlmTablePreset,
} from '../workspace/apComposerOptions'
import { FilePreviewModal } from '../../components/filePreview/FilePreviewModal'
import { useTaskFilePreview } from '../../components/filePreview/useTaskFilePreview'
import { processingModeLabel } from '../../components/ModeSelector'

// Modes offered when creating a run (matches enabled Phase 1 grid modules).
const PROC_MODES = ['AP', 'AR', 'BANK', 'OTHER'] as const

const RECEIPT_SIGNAL_LABELS: Record<ApVlmReceiptSignal, string> = {
  guess: 'Guess (auto)',
  single_per_page: 'Single receipt / page',
  multi_per_page: 'Multi receipt / page',
  single_span_pages: 'Receipt spans pages',
}

const TABLE_PRESET_LABELS: Record<ApVlmTablePreset, string> = {
  default: 'Default columns',
  ap_table: 'AP table',
}

function receiptSignalLabel(value: unknown): string {
  const key = String(value ?? 'guess') as ApVlmReceiptSignal
  return RECEIPT_SIGNAL_LABELS[key] ?? key
}

type GraphNode = WorkflowGraph['nodes'][number]
type NodeState = {
  status?: string
  detail?: unknown
  started_at?: string
  finished_at?: string
  duration_ms?: number
}
type StatusKind = 'done' | 'run' | 'pend' | 'fail'

const RUNNING_RUN_STATUSES = new Set(['executing', 'coa_running', 'running', 'queued'])

const PROC_RAIL_WIDTH_KEY = 'erp.proc.railWidth'
const PROC_RIGHT_WIDTH_KEY = 'erp.proc.rightWidth'

function readStoredWidth(key: string, fallback: number): number {
  try {
    const raw = localStorage.getItem(key)
    const n = raw ? Number(raw) : NaN
    return Number.isFinite(n) ? n : fallback
  } catch {
    return fallback
  }
}

function clampWidth(n: number, min: number, max: number): number {
  return Math.min(Math.max(n, min), max)
}

function nodeStateOf(run: WorkflowRun | null, nodeId: string): NodeState | undefined {
  const states = run?.node_states_json
  if (!states || typeof states !== 'object' || Array.isArray(states)) return undefined
  const raw = (states as Record<string, unknown>)[nodeId]
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return undefined
  return raw as NodeState
}

/**
 * Topological order (Kahn) with ties broken by declared node order. Unlike a
 * single-edge follower, this handles fan-out: parallel branches such as the
 * three VLM proposers stay grouped and appear before their join node
 * (ProposalPoolJoin) instead of being dumped after later nodes (CoA/Save).
 */
function orderNodes(graph: WorkflowGraph): GraphNode[] {
  const nodes = graph.nodes
  if (nodes.length === 0) return nodes
  const byId = new Map(nodes.map(n => [n.id, n]))
  const indegree = new Map(nodes.map(n => [n.id, 0]))
  const targetsBySource = new Map<string, string[]>()
  for (const e of graph.edges) {
    if (!byId.has(e.source) || !byId.has(e.target)) continue
    targetsBySource.set(e.source, [...(targetsBySource.get(e.source) ?? []), e.target])
    indegree.set(e.target, (indegree.get(e.target) ?? 0) + 1)
  }
  const order: GraphNode[] = []
  const emitted = new Set<string>()
  let progress = true
  while (order.length < nodes.length && progress) {
    progress = false
    for (const n of nodes) {
      if (emitted.has(n.id) || (indegree.get(n.id) ?? 0) > 0) continue
      emitted.add(n.id)
      order.push(n)
      for (const t of targetsBySource.get(n.id) ?? []) {
        indegree.set(t, (indegree.get(t) ?? 0) - 1)
      }
      progress = true
    }
  }
  // Append any remaining nodes (cycles / disconnected) preserving declared order.
  for (const n of nodes) if (!emitted.has(n.id)) order.push(n)
  return order
}

type NodeRow = { key: string; nodes: GraphNode[] }

/** Group consecutive parallel branches (VLM proposers) into a single side-by-side row. */
function groupNodeRows(ordered: GraphNode[]): NodeRow[] {
  const rows: NodeRow[] = []
  let i = 0
  while (i < ordered.length) {
    const node = ordered[i]!
    if (node.type === 'VLMProposer') {
      const group: GraphNode[] = []
      while (i < ordered.length && ordered[i]!.type === 'VLMProposer') {
        group.push(ordered[i]!)
        i += 1
      }
      rows.push({ key: group[0]!.id, nodes: group })
      continue
    }
    rows.push({ key: node.id, nodes: [node] })
    i += 1
  }
  return rows
}

function tagForType(type: string): string {
  const io = NODE_IO[type]
  if (!io) return ''
  return Object.values(io.outputs)[0] ?? ''
}

function statusKind(status?: string): StatusKind {
  const s = (status ?? '').toLowerCase()
  // 'active' is the review checkpoint (table ready for review), not a running step.
  if (['completed', 'done', 'ok', 'saved', 'active'].includes(s)) return 'done'
  if (['running', 'executing', 'coa_running'].includes(s)) return 'run'
  if (['failed', 'error'].includes(s)) return 'fail'
  return 'pend'
}

function nodeIconStatus(status?: string): string {
  if (!status) return 'pending'
  const s = status.toLowerCase()
  if (['running', 'executing', 'coa_running'].includes(s)) return 'running'
  if (['completed', 'done', 'ok', 'saved', 'active'].includes(s)) return 'ok'
  if (['failed', 'error'].includes(s)) return 'failed'
  if (s === 'warning') return 'warning'
  if (s === 'queued') return 'queued'
  return 'pending'
}

function fileStatusLabel(status: string): string {
  switch (status) {
    case 'ok':
      return 'Passed'
    case 'failed':
      return 'Error'
    case 'warning':
      return 'Needs review'
    case 'running':
      return 'Processing'
    case 'pending':
    case 'queued':
      return 'Queued'
    default:
      return status || '-'
  }
}

function statusText(state?: NodeState): string {
  if (!state?.status) return 'Pending'
  const s = state.status.toLowerCase()
  if (s === 'active') return 'Review'
  if (['running', 'executing', 'coa_running'].includes(s)) return 'Processing'
  if (['completed', 'done', 'ok', 'saved'].includes(s)) return 'Finished'
  if (['failed', 'error'].includes(s)) return 'Error'
  if (s === 'skipped') return 'Skipped'
  if (s === 'cancelled') return 'Cancelled'
  return 'Pending'
}

function fmtDuration(ms?: number): string {
  if (ms == null) return ''
  return `${(ms / 1000).toFixed(1)}s`
}

function pillKind(runStatus: string): { cls: string; label: string } {
  const kind = statusKind(runStatus)
  if (kind === 'done') return { cls: 'done', label: 'DONE' }
  if (kind === 'run') return { cls: 'run', label: runStatus.toLowerCase() === 'queued' ? 'QUEUED' : 'RUNNING' }
  if (kind === 'fail') return { cls: 'fail', label: 'FAILED' }
  if (runStatus.toLowerCase() === 'queued') return { cls: 'queue', label: 'QUEUED' }
  if (runStatus.toLowerCase() === 'awaiting_review') return { cls: 'run', label: 'REVIEW' }
  return { cls: 'queue', label: (runStatus || 'draft').toUpperCase() }
}

function nodeDetailLines(state?: NodeState): { k: string; v: string }[] {
  const d = state?.detail
  if (!d || typeof d !== 'object' || Array.isArray(d)) return []
  const o = d as Record<string, unknown>
  const lines: { k: string; v: string }[] = []
  const push = (k: string, v: unknown) => {
    const s = String(v ?? '').trim()
    if (s) lines.push({ k, v: s })
  }
  push('Error', o.error)
  push('Feedback', o.feedback)
  push('Re-VLM focus', o.rescan_focus)
  push('Note', o.rescan_note)
  push('Expected receipts', o.expected_receipt_count)
  const reason = String(o.reason ?? '').trim()
  if (reason && !lines.some(l => l.v === reason)) push('Reason', reason)
  if (typeof o.row_count === 'number') push('Rows', o.row_count)
  if (typeof o.proposal_count === 'number') push('Proposals', o.proposal_count)
  const resultParts: string[] = []
  if (typeof o.ok === 'number') resultParts.push(`${o.ok} ok`)
  if (typeof o.warnings === 'number' && o.warnings > 0) resultParts.push(`${o.warnings} warning(s)`)
  if (typeof o.capped === 'number' && o.capped > 0) resultParts.push(`${o.capped} capped`)
  if (resultParts.length) push('Result', resultParts.join(', '))
  return lines
}

function kvForNode(node: GraphNode, run: WorkflowRun, rowCount: number): { k: string; v: string }[] {
  const d = (node.data ?? {}) as Record<string, unknown>
  switch (node.type) {
    case 'Files': {
      const pages = run.files.reduce((s, f) => s + (f.page_count ?? 0), 0)
      return [{ k: 'Uploaded', v: `${run.files.length} files${pages ? ` \u00b7 ${pages} pages` : ''}` }]
    }
    case 'ModeConfig':
      return [{ k: 'Processing mode', v: String(d.processingMode ?? run.processing_mode) }]
    case 'ReceiptStyle':
      return [
        { k: 'Signal', v: receiptSignalLabel(d.receiptSignal) },
        { k: 'Table preset', v: TABLE_PRESET_LABELS[String(d.tablePreset ?? 'default') as ApVlmTablePreset] ?? String(d.tablePreset ?? 'default') },
      ]
    case 'VLM_API':
      return [
        { k: 'Provider', v: String(d.provider ?? 'Qwen') },
        { k: 'Cross-VLM', v: run.graph_json.nodes.some(n => n.type === 'VLMDoubleCheck') ? 'on' : 'off' },
      ]
    case 'VLMDoubleCheck':
      return [
        { k: 'Provider', v: String(d.provider ?? 'Qwen') },
        { k: 'Merge', v: String(d.mergePolicy ?? 'cross_vlm') },
      ]
    case 'TableReview':
      return [{ k: 'Rows extracted', v: rowCount > 0 ? String(rowCount) : '\u2014' }]
    case 'CoADeploy':
      return [{ k: 'Account mapping', v: 'auto' }]
    case 'SaveResult':
      return [{ k: 'Destination', v: `${run.processing_mode} ledger` }]
    default:
      return []
  }
}

export function ProcessingView() {
  const { activeCompany, accessToken } = useAuth()
  const companyId = activeCompany?.id ?? 'default'

  const [runs, setRuns] = useState<WorkflowRunSummary[]>([])
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const [activeRun, setActiveRun] = useState<WorkflowRun | null>(null)
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([])
  const [nodeCatalog, setNodeCatalog] = useState<WorkflowNodeCatalogEntry[]>([])
  const [error, setError] = useState<string | null>(null)
  const [showModePicker, setShowModePicker] = useState(false)
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState(false)
  const [approving, setApproving] = useState(false)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [railWidth, setRailWidth] = useState(() => readStoredWidth(PROC_RAIL_WIDTH_KEY, 220))
  const [rightWidth, setRightWidth] = useState(() => readStoredWidth(PROC_RIGHT_WIDTH_KEY, 312))
  const [resizingRail, setResizingRail] = useState(false)
  const [resizingRight, setResizingRight] = useState(false)
  const [payloads, setPayloads] = useState<Record<string, Record<string, unknown>>>({})
  const [assetRecords, setAssetRecords] = useState<OtherRow[]>([])
  const [editedRows, setEditedRows] = useState<ARAPTransaction[] | BankTransaction[] | null>(null)
  const [applyingTemplate, setApplyingTemplate] = useState(false)
  const [templateChoice, setTemplateChoice] = useState('')
  const [menuRunId, setMenuRunId] = useState<string | null>(null)
  const [renamingRunId, setRenamingRunId] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const [showReVlm, setShowReVlm] = useState(false)
  const [reVlmInitialFileIds, setReVlmInitialFileIds] = useState<string[]>([])
  const [headerChangeConfirm, setHeaderChangeConfirm] = useState<{
    onConfirm: () => void
    onCancel: () => void
  } | null>(null)
  const syncedTemplateRunRef = useRef<string | null>(null)
  const renameCancelRef = useRef(false)
  const stopGuardRunIdRef = useRef<string | null>(null)
  const activeRunIdRef = useRef<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const bodyRef = useRef<HTMLDivElement | null>(null)

  const assignActiveRunId = useCallback((id: string | null) => {
    activeRunIdRef.current = id
    setActiveRunId(id)
  }, [])

  useResizeDrag(resizingRail, setResizingRail, x => {
    const left = bodyRef.current?.getBoundingClientRect().left ?? 0
    setRailWidth(clampWidth(x - left, 160, 400))
  })
  useResizeDrag(resizingRight, setResizingRight, (x, w) => {
    setRightWidth(clampWidth(w - x, 240, 520))
  })

  useEffect(() => {
    try {
      localStorage.setItem(PROC_RAIL_WIDTH_KEY, String(railWidth))
      localStorage.setItem(PROC_RIGHT_WIDTH_KEY, String(rightWidth))
    } catch {
      /* storage may be unavailable */
    }
  }, [railWidth, rightWidth])

  useEffect(() => {
    activeRunIdRef.current = activeRunId
  }, [activeRunId])

  const reloadRuns = useCallback(async () => {
    const list = (await workflowApi.listRuns(companyId)).filter(r => !r.processing_removed_at)
    setRuns(list)
    return list
  }, [companyId])

  useLayoutEffect(() => {
    assignActiveRunId(null)
    setActiveRun(null)
    setPayloads({})
    setEditedRows(null)
    setSelectedNodeId(null)
    setError(null)
    stopGuardRunIdRef.current = null
  }, [companyId, assignActiveRunId])

  useEffect(() => {
    let cancelled = false
    reloadRuns()
      .then(list => {
        if (cancelled) return
        setActiveRunId(prev => {
          const next = prev && list.some(r => r.id === prev) ? prev : list[0]?.id ?? null
          activeRunIdRef.current = next
          return next
        })
      })
      .catch(err => !cancelled && setError(err instanceof Error ? err.message : 'Could not load runs.'))
    workflowApi
      .listTemplates(companyId)
      .then(t => !cancelled && setTemplates(t))
      .catch(() => {})
    workflowApi
      .nodeCatalog(companyId)
      .then(c => !cancelled && setNodeCatalog(c))
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [companyId, reloadRuns])

  // Load asset/liability records for OTHER runs.
  const refreshAssetRecords = useCallback(
    async (run: WorkflowRun) => {
      const runId = run.id
      if (runId !== activeRunIdRef.current) return
      if ((run.processing_mode ?? '').toUpperCase() !== 'OTHER') {
        setAssetRecords([])
        return
      }
      try {
        const { records } = await api.getOtherRecords(run.task_id, companyId)
        if (runId !== activeRunIdRef.current) return
        const filesById = filesByIdFromRun(run.files)
        setAssetRecords(
          records.map(rec => ({
            id: rec.id,
            record_type: rec.record_type as 'loan' | 'fixed_asset',
            source_file_id: rec.source_file_id ?? undefined,
            source_file_label: assetSourceLabel(
              { ...rec.payload_json, source_file_id: rec.source_file_id },
              filesById,
            ),
            ...rec.payload_json,
          })),
        )
      } catch {
        if (runId !== activeRunIdRef.current) return
        setAssetRecords([])
      }
    },
    [companyId],
  )

  // Load the active run (graph + node states) and its extracted table payloads.
  const loadActiveRun = useCallback(
    async (runId: string) => {
      const run = await workflowApi.getRun(companyId, runId)
      if (run.company_id !== companyId) return run
      if (runId !== activeRunIdRef.current) return run
      setActiveRun(run)
      setSelectedNodeId(prev => {
        if (prev && run.graph_json.nodes.some(n => n.id === prev)) return prev
        const running = run.graph_json.nodes.find(n => statusKind(nodeStateOf(run, n.id)?.status) === 'run')
        return running?.id ?? run.graph_json.nodes[0]?.id ?? null
      })
      try {
        const p = await loadAllBatchTablePayloads(run, companyId).then(loaded =>
          resolveBatchPayloads(run, loaded),
        )
        if (runId !== activeRunIdRef.current) return run
        setPayloads(p)
      } catch {
        if (runId !== activeRunIdRef.current) return run
        setPayloads({})
      }
      void refreshAssetRecords(run)
      return run
    },
    [companyId, refreshAssetRecords],
  )

  useEffect(() => {
    if (!activeRunId) {
      setActiveRun(null)
      setPayloads({})
      setAssetRecords([])
      return
    }
    let cancelled = false
    loadActiveRun(activeRunId).catch(err => {
      if (cancelled) return
      setActiveRun(null)
      setPayloads({})
      setError(err instanceof Error ? err.message : 'Could not load run.')
    })
    return () => {
      cancelled = true
    }
  }, [activeRunId, loadActiveRun])

  const applyRunFromServer = useCallback(
    (run: WorkflowRun, refreshPayloads = false) => {
      if (shouldIgnoreRunRefreshAfterStop(stopGuardRunIdRef.current, run)) return
      if (run.company_id !== companyId) return
      setRuns(prev => prev.map(r => (r.id === run.id ? summaryFromRun(run) : r)))
      if (run.id !== activeRunIdRef.current) return
      setActiveRun(run)
      setSelectedNodeId(prev => {
        if (prev && run.graph_json.nodes.some(n => n.id === prev)) return prev
        const running = run.graph_json.nodes.find(n => statusKind(nodeStateOf(run, n.id)?.status) === 'run')
        return running?.id ?? prev ?? run.graph_json.nodes[0]?.id ?? null
      })
      const shouldRefreshPayloads =
        refreshPayloads ||
        run.run_status === 'executing' ||
        run.run_status === 'awaiting_review' ||
        run.run_status === 'coa_running' ||
        run.run_status === 'completed' ||
        run.run_status === 'done' ||
        run.run_status === 'saved'
      if (shouldRefreshPayloads) {
        const runId = run.id
        void loadAllBatchTablePayloads(run, companyId)
          .then(loaded => {
            if (runId !== activeRunIdRef.current) return
            setPayloads(resolveBatchPayloads(run, loaded))
          })
          .catch(() => {})
        void refreshAssetRecords(run)
      }
    },
    [companyId, refreshAssetRecords],
  )

  const markRunExecuting = useCallback((runId: string) => {
    setActiveRun(prev => (prev?.id === runId ? { ...prev, run_status: 'executing' } : prev))
    setRuns(prev => prev.map(r => (r.id === runId ? { ...r, run_status: 'executing' } : r)))
  }, [])

  // Live polling while the run is executing.
  const runStatus = activeRun?.run_status ?? ''
  useEffect(() => {
    if (!activeRunId || !RUNNING_RUN_STATUSES.has(runStatus)) return
    let cancelled = false
    const poll = window.setInterval(() => {
      workflowApi
        .getRun(companyId, activeRunId)
        .then(run => {
          if (cancelled) return
          applyRunFromServer(run, true)
          if (!RUNNING_RUN_STATUSES.has(run.run_status)) void reloadRuns()
        })
        .catch(() => {})
    }, 2000)
    return () => {
      cancelled = true
      window.clearInterval(poll)
    }
  }, [activeRunId, runStatus, companyId, reloadRuns, applyRunFromServer])

  useWorkflowRunEvents({
    runId: activeRunId,
    runStatus: activeRun?.run_status,
    companyId,
    accessToken,
    enabled: Boolean(companyId && activeRun),
    onEvent: run => applyRunFromServer(run, true),
    onError: () => {},
  })

  const handleCreateRun = useCallback(
    async (mode: string) => {
      setCreating(true)
      setError(null)
      try {
        const tpl = templates.find(t => t.processing_mode === mode && t.is_default)
        const run = await workflowApi.createRun(companyId, mode, tpl?.id)
        await reloadRuns()
        assignActiveRunId(run.id)
        setShowModePicker(false)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not create a new run.')
      } finally {
        setCreating(false)
      }
    },
    [companyId, templates, reloadRuns, assignActiveRunId],
  )

  useEffect(() => {
    if (!menuRunId) return
    const close = () => setMenuRunId(null)
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [menuRunId])

  const startRename = useCallback((r: WorkflowRunSummary) => {
    setMenuRunId(null)
    renameCancelRef.current = false
    setRenamingRunId(r.id)
    setRenameDraft(r.title || '')
  }, [])

  const commitRename = useCallback(
    async (runId: string) => {
      if (renameCancelRef.current) {
        renameCancelRef.current = false
        setRenamingRunId(null)
        return
      }
      const current = runs.find(r => r.id === runId)
      const next = renameDraft.trim()
      setRenamingRunId(null)
      if (!next || next === (current?.title || '')) return
      try {
        await workflowApi.patchRunMeta(companyId, runId, { title: next })
        await reloadRuns()
        setActiveRun(prev => (prev && prev.id === runId ? { ...prev, title: next } : prev))
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not rename the run.')
      }
    },
    [companyId, renameDraft, runs, reloadRuns],
  )

  const removeRun = useCallback(
    async (r: WorkflowRunSummary) => {
      setMenuRunId(null)
      const label = r.title || 'Untitled'
      const msg = `Remove "${label}" permanently? This deletes the run, uploaded files, and unreconciled module rows derived from it. Reconciled rows are kept.`
      if (!window.confirm(msg)) return
      setBusy(true)
      setError(null)
      try {
        await workflowApi.deleteRun(companyId, r.id)
        const list = await reloadRuns()
        if (activeRunId === r.id) assignActiveRunId(list[0]?.id ?? null)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not remove the run.')
      } finally {
        setBusy(false)
      }
    },
    [companyId, reloadRuns, activeRunId, assignActiveRunId],
  )

  const patchGraph = useCallback(
    async (graph: WorkflowGraph) => {
      if (!activeRun) return
      setActiveRun({ ...activeRun, graph_json: graph })
      try {
        const updated = await workflowApi.patchRun(companyId, activeRun.id, graph)
        setActiveRun(updated)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not save node settings.')
      }
    },
    [activeRun, companyId],
  )

  const patchNodeData = useCallback(
    (nodeId: string, patch: Record<string, unknown>) => {
      if (!activeRun) return
      const graph = activeRun.graph_json
      const nodes = graph.nodes.map(n => (n.id === nodeId ? { ...n, data: { ...n.data, ...patch } } : n))
      void patchGraph({ ...graph, nodes })
    },
    [activeRun, patchGraph],
  )

  const hasProcessedFiles = useMemo(
    () => (activeRun?.files ?? []).some(f => ['ok', 'failed', 'warning'].includes(f.file_status ?? '')),
    [activeRun],
  )

  const applyTemplate = useCallback(
    async (templateId: string) => {
      if (!activeRun || !templateId) return
      const tpl = templates.find(t => t.id === templateId)
      if (!tpl) return
      setApplyingTemplate(true)
      try {
        const nextGraph = layoutGraphVertical(JSON.parse(JSON.stringify(tpl.graph_json)) as WorkflowGraph)
        await patchGraph(nextGraph)
      } finally {
        setApplyingTemplate(false)
      }
    },
    [activeRun, templates, patchGraph],
  )

  const guardHeaderChange = useCallback(
    (apply: () => void, revert: () => void) => {
      if (!hasProcessedFiles) {
        apply()
        return
      }
      setHeaderChangeConfirm({
        onConfirm: () => {
          setHeaderChangeConfirm(null)
          apply()
        },
        onCancel: () => {
          setHeaderChangeConfirm(null)
          revert()
        },
      })
    },
    [hasProcessedFiles],
  )

  const toggleCrossVlm = useCallback(
    (on: boolean) => {
      if (!activeRun) return
      const graph = on
        ? graphWithDoubleCheckEnabled(activeRun.graph_json)
        : graphWithDoubleCheckDisabled(activeRun.graph_json)
      void patchGraph(graph)
    },
    [activeRun, patchGraph],
  )

  const runAction = useCallback(
    async (action: 'execute' | 'cancel') => {
      if (!activeRun) return
      setBusy(true)
      setError(null)
      const runId = activeRun.id
      if (action === 'execute') markRunExecuting(runId)
      try {
        const updated = await workflowApi[action](companyId, runId)
        applyRunFromServer(updated, true)
        void reloadRuns()
      } catch (err) {
        setError(err instanceof Error ? err.message : `Could not ${action} the run.`)
        if (action === 'execute') {
          void workflowApi
            .getRun(companyId, runId)
            .then(run => applyRunFromServer(run, true))
            .catch(() => {})
        }
      } finally {
        setBusy(false)
      }
    },
    [activeRun, companyId, reloadRuns, markRunExecuting, applyRunFromServer],
  )

  const stopRun = useCallback(async () => {
    if (!activeRun) return
    const label = activeRun.title?.trim() || 'Untitled'
    const msg = `Stop all processing on "${label}"? In-progress files will be reset. Partial results may remain for review.`
    if (!window.confirm(msg)) return
    setBusy(true)
    setError(null)
    const runId = activeRun.id
    stopGuardRunIdRef.current = runId
    applyRunFromServer(applyRunStoppedLocally(activeRun), true)
    try {
      const updated = await workflowApi.cancel(companyId, runId)
      applyRunFromServer(updated, true)
      if (!runLooksProcessing(updated)) stopGuardRunIdRef.current = null
      void reloadRuns()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not stop the run.')
      void workflowApi
        .getRun(companyId, runId)
        .then(run => {
          if (shouldIgnoreRunRefreshAfterStop(stopGuardRunIdRef.current, run)) return
          applyRunFromServer(run, true)
          if (!runLooksProcessing(run)) stopGuardRunIdRef.current = null
        })
        .catch(() => {})
    } finally {
      setBusy(false)
    }
  }, [activeRun, companyId, reloadRuns, applyRunFromServer])

  const handleUpload = useCallback(
    async (files: FileList | null) => {
      if (!activeRun || !files || files.length === 0) return
      // One upload per task: block adding a second batch (avoids max-up bugs).
      if (activeRun.files.length > 0) {
        setError('Files already uploaded for this task. Use Re-VLM to retry failed files.')
        return
      }
      setBusy(true)
      setError(null)
      try {
        const uploadBatchId = safeRandomUUID()
        const uploadedAt = new Date().toISOString()
        for (const file of Array.from(files)) {
          await workflowApi.uploadFile(companyId, activeRun.id, file, { uploadBatchId, uploadedAt })
        }
        await loadActiveRun(activeRun.id)
        void reloadRuns()
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not upload files.')
      } finally {
        setBusy(false)
      }
    },
    [activeRun, companyId, loadActiveRun, reloadRuns],
  )

  const openReVlmModal = useCallback((taskFileIds: string[] = []) => {
    if (activeRun && runHasLockedApprovedTable(activeRun)) return
    setReVlmInitialFileIds(taskFileIds)
    setShowReVlm(true)
  }, [activeRun])

  const handleReVlmConfirm = useCallback(
    async ({
      taskFileIds,
      rescanReasons,
      rescanNote,
      expectedReceiptCount,
      workflow,
    }: ReVlmConfirmPayload) => {
      if (!activeRun || taskFileIds.length === 0 || busy) return
      if (runHasLockedApprovedTable(activeRun)) {
        setError(
          'Approved and loaded into modules — Re-VLM is disabled to avoid conflicting updates.',
        )
        setShowReVlm(false)
        setReVlmInitialFileIds([])
        return
      }
      setShowReVlm(false)
      setReVlmInitialFileIds([])
      setBusy(true)
      setError(null)
      const revlmLocalOpts = {
        rescanReasons,
        rescanNote: rescanNote || undefined,
        expectedReceiptCount: expectedReceiptCount ?? null,
      }
      // Optimistic: show processing spinners on files + VLM nodes immediately.
      applyRunFromServer(applyRunReVlmLocally(activeRun, taskFileIds, revlmLocalOpts), true)
      try {
        let runForReVlm = activeRun
        if (
          workflow &&
          workflowSettingsChanged(
            activeRun.graph_json,
            templates,
            activeRun.processing_mode,
            workflow,
          )
        ) {
          const nextGraph = applyWorkflowSettingsToGraph(activeRun.graph_json, templates, workflow)
          runForReVlm = await workflowApi.patchRun(companyId, activeRun.id, nextGraph)
          // Re-apply Re-VLM running state so patchRun does not flash Finished again.
          applyRunFromServer(applyRunReVlmLocally(runForReVlm, taskFileIds, revlmLocalOpts), true)
        }
        const updated = await workflowApi.reVlm(companyId, runForReVlm.id, taskFileIds, {
          rescan_reasons: rescanReasons,
          rescan_note: rescanNote || null,
          expected_receipt_count: expectedReceiptCount ?? null,
        })
        applyRunFromServer(updated, true)
        void reloadRuns()
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not re-run VLM.')
        void workflowApi
          .getRun(companyId, activeRun.id)
          .then(run => applyRunFromServer(run, true))
          .catch(() => {})
      } finally {
        setBusy(false)
      }
    },
    [activeRun, busy, companyId, reloadRuns, applyRunFromServer, templates],
  )

  const previewTaskFiles = useMemo(
    () =>
      (activeRun?.files ?? []).map(f => ({
        taskFileId: f.task_file_id,
        originalFilename: f.original_filename,
      })),
    [activeRun?.files],
  )

  const filePreview = useTaskFilePreview(activeRun?.task_id, companyId, previewTaskFiles)

  const orderedNodes = useMemo(
    () => (activeRun ? orderNodes(activeRun.graph_json) : []),
    [activeRun],
  )
  const nodeRows = useMemo(() => groupNodeRows(orderedNodes), [orderedNodes])

  const combined = useMemo(
    () => (activeRun ? combineBatchTablePayloads(payloads, activeRun) : {}),
    [payloads, activeRun],
  )
  const isAsset = (activeRun?.processing_mode ?? '').toUpperCase() === 'OTHER'
  const isBank = (activeRun?.processing_mode ?? '').toUpperCase() === 'BANK'
  const arapRows = (combined.arapTransactions as ARAPTransaction[] | undefined) ?? []
  const bankRows = (combined.bankTransactions as BankTransaction[] | undefined) ?? []
  const displayArapRows = (editedRows as ARAPTransaction[] | null) ?? arapRows
  const displayBankRows = (editedRows as BankTransaction[] | null) ?? bankRows
  const outputRowCount = isAsset ? assetRecords.length : Math.max(displayArapRows.length, displayBankRows.length)

  useEffect(() => {
    setEditedRows(null)
  }, [activeRunId])

  const modeTemplates = useMemo(
    () => sortPaletteTemplates(templates.filter(t => t.processing_mode === activeRun?.processing_mode)),
    [templates, activeRun?.processing_mode],
  )

  useEffect(() => {
    if (!activeRun || modeTemplates.length === 0) return
    if (syncedTemplateRunRef.current !== activeRun.id) {
      syncedTemplateRunRef.current = activeRun.id
      const resolved = resolvePaletteTemplateSelection(modeTemplates, '', activeRun.graph_json)
      setTemplateChoice(resolved?.id ?? '')
      return
    }
    const matched = templateMatchingGraph(modeTemplates, activeRun.graph_json)
    if (matched && matched.id !== templateChoice && !applyingTemplate) {
      setTemplateChoice(matched.id)
      return
    }
    const selectedStillValid = modeTemplates.some(t => t.id === templateChoice)
    if (!selectedStillValid) {
      const resolved = resolvePaletteTemplateSelection(modeTemplates, '', activeRun.graph_json)
      setTemplateChoice(resolved?.id ?? '')
    }
  }, [activeRun?.id, activeRun?.graph_json, modeTemplates, templateChoice, applyingTemplate])

  const approve = useCallback(async () => {
    if (!activeRun) return
    setApproving(true)
    setBusy(true)
    setError(null)
    try {
      const payload: Record<string, unknown> = { ...combined }
      if (isBank) {
        const raw = ((editedRows as BankTransaction[] | null) ?? bankRows).map(t => ({ ...t }))
        payload.bankTransactions = coalesceBankAccountTypeRows(raw) as BankTransaction[]
      } else {
        payload.arapTransactions = (editedRows as ARAPTransaction[] | null) ?? arapRows
      }
      const updated = await workflowApi.resume(companyId, activeRun.id, payload, false)
      setActiveRun(updated)
      setPayloads(mapCombinedPayloadToBatches(updated, payload))
      setEditedRows(null)
      void reloadRuns()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not approve and resume.')
    } finally {
      setBusy(false)
      setApproving(false)
    }
  }, [activeRun, combined, isBank, editedRows, bankRows, arapRows, companyId, reloadRuns])

  const selectedNode = activeRun?.graph_json.nodes.find(n => n.id === selectedNodeId) ?? null
  const receiptStyleNode = activeRun?.graph_json.nodes.find(n => n.type === 'ReceiptStyle') ?? null
  const headerProvider = activeRun ? providerFromGraph(activeRun.graph_json) : 'Qwen'
  const headerProviderEnabled = activeRun ? hasVlmProviderControl(activeRun.graph_json) : false
  const workflowProviderOptions = useMemo(() => providerOptionsFromCatalog(nodeCatalog), [nodeCatalog])
  const headerProviderValue = resolveProviderSelection(headerProvider, workflowProviderOptions)
  const processingModeUpper = (activeRun?.processing_mode ?? '').toUpperCase()
  const showReceiptOptions =
    (processingModeUpper === 'AP' || processingModeUpper === 'AR') && receiptStyleNode != null
  const receiptSettings = activeRun ? receiptSettingsFromGraph(activeRun.graph_json) : null
  const tablePresetOptions =
    processingModeUpper === 'AR'
      ? AP_TABLE_OPTIONS_ORDER.filter(opt => opt === 'default')
      : AP_TABLE_OPTIONS_ORDER
  const isRunning = RUNNING_RUN_STATUSES.has(runStatus)
  // A node still showing a running state means the workflow is mid-process even if the
  // run-level status lags (or is wedged); Stop must stay enabled to unstick it.
  const anyNodeRunning = useMemo(
    () =>
      (activeRun?.graph_json.nodes ?? []).some(
        n => statusKind(nodeStateOf(activeRun, n.id)?.status) === 'run',
      ),
    [activeRun],
  )
  const awaitingReview = runStatus === 'awaiting_review'
  // VLM extraction phase: show only the per-file status list, not a partial table.
  const extracting = runStatus === 'executing' || runStatus === 'queued' || runStatus === 'running'
  const anyFileRunning = Boolean(activeRun?.files.some(f => f.file_status === 'running'))
  // Live output banner + right-panel spinners stay on while Re-VLM / VLM is in flight.
  const tableProcessing = extracting || anyNodeRunning || anyFileRunning
  // Once approved (CoA posting / completed) the output table is view-only.
  const outputReadOnly = runStatus === 'coa_running' || runStatus === 'completed'
  const reVlmLocked = Boolean(activeRun && runHasLockedApprovedTable(activeRun))
  const reVlmLockedTitle =
    'Approved and loaded into modules — Re-VLM is disabled to avoid conflicting updates.'
  const canApproveTable =
    Boolean(activeRun) &&
    !outputReadOnly &&
    !isRunning &&
    !anyNodeRunning &&
    (awaitingReview || (outputRowCount > 0 && hasOcrDataOnRun(activeRun)))
  const completedFileCount = (activeRun?.files ?? []).filter(f =>
    ['ok', 'warning'].includes(f.file_status ?? ''),
  ).length
  const totalFileCount = activeRun?.files.length ?? 0

  const renderNodeCard = (node: GraphNode) => {
    const state = nodeStateOf(activeRun, node.id)
    const kind = statusKind(state?.status)
    const kvs = kvForNode(node, activeRun, outputRowCount)
    const detailLines = nodeDetailLines(state)
    const label = String((node.data as Record<string, unknown>)?.label ?? node.type)
    return (
      <div
        key={node.id}
        className={`erp-node ${kind}${node.id === selectedNodeId ? ' sel' : ''}`}
        onClick={() => setSelectedNodeId(node.id)}
      >
        <div className="nh">
          <FileStatusIcon status={nodeIconStatus(state?.status)} />
          <span className="name">{label}</span>
          <span className="stt">{statusText(state)}</span>
        </div>
        <div className="nb">
          {tagForType(node.type) && <span className="tag">{tagForType(node.type)}</span>}
          {kvs.map(kv => (
            <div className="kv" key={kv.k}>
              <span>{kv.k}</span>
              <b>{kv.v}</b>
            </div>
          ))}
          {detailLines.map(line => (
            <div className="erp-node-fb" key={line.k}>
              <span className="erp-node-fb-label">{line.k}</span>
              <div className="erp-node-fb-text" title={line.v}>
                {line.v}
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="erp-proc">
      <div className="erp-proc-toolbar">
        <div className="field">
          Mode
          <select value={activeRun?.processing_mode ?? ''} disabled title="Mode is fixed per run">
            {PROC_MODES.map(m => (
              <option key={m} value={m}>
                {processingModeLabel(m)}
              </option>
            ))}
            {activeRun && !PROC_MODES.includes(activeRun.processing_mode as (typeof PROC_MODES)[number]) && (
              <option value={activeRun.processing_mode}>{processingModeLabel(activeRun.processing_mode)}</option>
            )}
          </select>
        </div>
        <div className="field">
          Provider
          <select
            value={headerProviderValue}
            disabled={!headerProviderEnabled || busy || isRunning}
            onChange={e => {
              const next = e.target.value
              guardHeaderChange(
                () => {
                  if (!activeRun) return
                  void patchGraph(patchProviderInGraph(activeRun.graph_json, next))
                },
                () => {},
              )
            }}
          >
            {workflowProviderOptions.map(p => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          Template
          <select
            value={templateChoice}
            disabled={!activeRun || isRunning || busy || applyingTemplate || modeTemplates.length === 0}
            title="Apply a workflow template (AP Double Check / 3 VLM Vote / Manager Review)"
            onChange={e => {
              const prev = templateChoice
              const next = e.target.value
              setTemplateChoice(next)
              guardHeaderChange(
                () => void applyTemplate(next),
                () => setTemplateChoice(prev),
              )
            }}
          >
            {modeTemplates.length === 0 && <option value="">Default</option>}
            {modeTemplates.map(t => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </div>
        {showReceiptOptions && receiptStyleNode ? (
          <>
            <div className="field">
              Receipt layout
              <select
                value={receiptSettings?.receiptSignal ?? 'guess'}
                disabled={!activeRun || busy || isRunning}
                title="How receipts are laid out in uploaded files (speeds up AP/AR VLM routing)"
                onChange={e => {
                  const next = e.target.value as ApVlmReceiptSignal
                  guardHeaderChange(
                    () => patchNodeData(receiptStyleNode.id, { receiptSignal: next }),
                    () => {},
                  )
                }}
              >
                {AP_RECEIPT_OPTIONS_ORDER.map(opt => (
                  <option key={opt} value={opt}>
                    {RECEIPT_SIGNAL_LABELS[opt]}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              Table style
              <select
                value={receiptSettings?.tablePreset ?? 'default'}
                disabled={!activeRun || busy || isRunning}
                title="Column preset for extracted AP/AR rows"
                onChange={e => {
                  const next = e.target.value as ApVlmTablePreset
                  guardHeaderChange(
                    () => patchNodeData(receiptStyleNode.id, { tablePreset: next }),
                    () => {},
                  )
                }}
              >
                {tablePresetOptions.map(opt => (
                  <option key={opt} value={opt}>
                    {TABLE_PRESET_LABELS[opt]}
                  </option>
                ))}
              </select>
            </div>
          </>
        ) : null}
        <button
          className="erp-btn"
          disabled={!activeRun || busy || (activeRun?.files.length ?? 0) > 0}
          title={(activeRun?.files.length ?? 0) > 0 ? 'Files already uploaded for this task' : 'Upload files (one batch per task)'}
          onClick={() => fileInputRef.current?.click()}
        >
          Upload Files
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          style={{ display: 'none' }}
          onChange={e => {
            void handleUpload(e.target.files)
            e.target.value = ''
          }}
        />
        <p className="erp-proc-cloud-ai-notice" role="note">
          {CLOUD_AI_DATA_NOTICE}
        </p>
        <div className="erp-proc-toolbar-actions">
          <button
            className="erp-btn"
            disabled={!activeRun || isRunning || busy || !hasProcessedFiles || reVlmLocked}
            onClick={() => openReVlmModal()}
            title={
              reVlmLocked
                ? reVlmLockedTitle
                : 'Re-run VLM on selected files (choose files and correction hints)'
            }
          >
            Re-VLM
          </button>
          <button className="erp-btn" disabled={!activeRun || (!isRunning && !anyNodeRunning) || busy} onClick={() => void stopRun()}>
            Stop
          </button>
          <button
            className="erp-btn primary"
            disabled={!activeRun || isRunning || busy || (activeRun?.files.length ?? 0) === 0}
            onClick={() => void runAction('execute')}
          >
            Run
          </button>
        </div>
      </div>

      <div
        ref={bodyRef}
        className="erp-proc-body"
        style={{ gridTemplateColumns: `${railWidth}px 5px minmax(280px, 1fr) 5px ${rightWidth}px` }}
      >
        <aside className="erp-proc-rail">
          <div className="erp-proc-railhead">
            <span>Runs</span>
            <button
              type="button"
              className="erp-btn primary erp-proc-newbtn"
              disabled={creating}
              onClick={() => setShowModePicker(v => !v)}
            >
              {creating ? 'Creating...' : '+ New run'}
            </button>
          </div>
          {showModePicker && (
            <div className="erp-proc-modes">
              {PROC_MODES.map(m => (
                <button key={m} type="button" className="erp-btn" disabled={creating} onClick={() => void handleCreateRun(m)}>
                  {processingModeLabel(m)}
                </button>
              ))}
            </div>
          )}
          {error && <div className="erp-note">{error}</div>}
          {!error && runs.length === 0 && <div className="erp-note">No runs yet.</div>}
          {runs.map(r => {
            const pill = pillKind(r.run_status)
            return (
              <div
                key={r.id}
                className={`erp-proc-run${r.id === activeRunId ? ' active' : ''}`}
                onClick={() => assignActiveRunId(r.id)}
              >
                <div className="erp-proc-run-top">
                  {renamingRunId === r.id ? (
                    <input
                      className="erp-proc-rename"
                      autoFocus
                      value={renameDraft}
                      onClick={e => e.stopPropagation()}
                      onChange={e => setRenameDraft(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === 'Enter') e.currentTarget.blur()
                        else if (e.key === 'Escape') {
                          renameCancelRef.current = true
                          e.currentTarget.blur()
                        }
                      }}
                      onBlur={() => void commitRename(r.id)}
                    />
                  ) : (
                    <div
                      className="name"
                      title="Double-click to rename"
                      onDoubleClick={e => {
                        e.stopPropagation()
                        startRename(r)
                      }}
                    >
                      {r.title || 'Untitled'}
                    </div>
                  )}
                  <button
                    type="button"
                    className="erp-proc-kebab"
                    aria-label="Run actions"
                    onClick={e => {
                      e.stopPropagation()
                      setMenuRunId(prev => (prev === r.id ? null : r.id))
                    }}
                  >
                    {'\u22EE'}
                  </button>
                  {menuRunId === r.id && (
                    <div className="erp-proc-menu" onClick={e => e.stopPropagation()}>
                      <button type="button" onClick={() => startRename(r)}>
                        Rename
                      </button>
                      <button type="button" className="danger" onClick={() => void removeRun(r)}>
                        Remove
                      </button>
                    </div>
                  )}
                </div>
                <div className="m">
                  <span className={`erp-pill ${pill.cls}`}>{pill.label}</span>
                  <span className="filecount">
                    {r.file_count} file{r.file_count === 1 ? '' : 's'}
                  </span>
                  {(r.file_statuses ?? []).length > 0 ? (
                    <FileStatusIconRow statuses={r.file_statuses!} />
                  ) : null}
                </div>
              </div>
            )
          })}
        </aside>

        <div
          className={`erp-proc-resize${resizingRail ? ' resizing' : ''}`}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize runs panel"
          onMouseDown={e => {
            e.preventDefault()
            setResizingRail(true)
          }}
        />

        <div className="erp-proc-output">
          <div className="erp-proc-output-head">
            <div className="erp-set-title">Live output</div>
            <div className="erp-set-sub">Extracted rows from this run</div>
            {!activeRun ? (
              <div className="erp-empty">Select or create a run.</div>
            ) : (
              <>
                {activeRun.files.length > 0 && (
                  <ul className="erp-fstatus">
                    {activeRun.files.map(f => {
                      const reason = [f.gate_result ? `gate: ${f.gate_result}` : '', f.error_text ?? '']
                        .filter(Boolean)
                        .join(' \u00B7 ')
                      const canRetry = f.file_status === 'failed' || f.file_status === 'warning'
                      const pageLabel = formatFilePageCount(f.page_count)
                      return (
                        <li key={f.id} className="erp-fstatus-row">
                          <FileStatusIcon status={f.file_status} />
                          <button
                            type="button"
                            className="fn erp-link"
                            title={`Preview ${f.original_filename ?? f.task_file_id}`}
                            onClick={() => void filePreview.openPreview(f.task_file_id)}
                          >
                            {f.original_filename ?? f.task_file_id}
                            {pageLabel ? ` (${pageLabel})` : ''}
                          </button>
                          <span className={`fst ${f.file_status}`}>{fileStatusLabel(f.file_status)}</span>
                          {reason && (
                            <span className="fre" title={reason}>
                              {reason}
                            </span>
                          )}
                          {canRetry && (
                            <button
                              type="button"
                              className="erp-btn"
                              disabled={isRunning || busy || reVlmLocked}
                              title={reVlmLocked ? reVlmLockedTitle : 'Re-run VLM for this file'}
                              onClick={() => openReVlmModal([f.task_file_id])}
                            >
                              Retry
                            </button>
                          )}
                        </li>
                      )
                    })}
                  </ul>
                )}
              </>
            )}
          </div>
          {activeRun ? (
            <div className="erp-proc-output-scroll">
              {outputRowCount === 0 ? (
                <div className={extracting ? 'erp-empty erp-empty--compact' : 'erp-empty'}>
                  {extracting ? 'Processing…' : 'No extracted rows yet.'}
                </div>
              ) : isAsset ? (
                <OtherTable records={assetRecords} readOnly={outputReadOnly} />
              ) : isBank ? (
                <BankStatementReview
                  transactions={displayBankRows}
                  readOnly={outputReadOnly}
                  onDataChange={outputReadOnly ? undefined : rows => setEditedRows(rows)}
                  onApprove={() => void approve()}
                  canApprove={canApproveTable}
                  approveBusy={approving}
                />
              ) : (
                <ARAPReview
                  transactions={displayArapRows}
                  readOnly={outputReadOnly}
                  useApTableSchema={(activeRun.processing_mode ?? '').toUpperCase() === 'AP'}
                  onDataChange={outputReadOnly ? undefined : rows => setEditedRows(rows)}
                  onApprove={() => void approve()}
                  canApprove={canApproveTable}
                  approveBusy={approving}
                  isProcessing={tableProcessing}
                  completedFiles={completedFileCount}
                  totalFiles={totalFileCount}
                  cropPreview={
                    activeRun.task_id
                      ? {
                          taskId: activeRun.task_id,
                          companyId,
                          files: (activeRun.files ?? []).map(f => ({
                            taskFileId: f.task_file_id,
                            originalFilename: f.original_filename,
                          })),
                        }
                      : null
                  }
                />
              )}
            </div>
          ) : null}
        </div>

        <div
          className={`erp-proc-resize${resizingRight ? ' resizing' : ''}`}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize workflow panel"
          onMouseDown={e => {
            e.preventDefault()
            setResizingRight(true)
          }}
        />

        <aside className="erp-proc-right">
          <div className="erp-proc-canvas erp-proc-canvas--side">
            {!activeRun ? (
              <div className="erp-empty">Select or create a run.</div>
            ) : orderedNodes.length === 0 ? (
              <div className="erp-empty">This run has no workflow nodes.</div>
            ) : (
              nodeRows.map((row, i) => (
                <div key={row.key}>
                  {i > 0 && <div className="erp-connector" />}
                  {row.nodes.length > 1 ? (
                    <div className="erp-node-row">{row.nodes.map(renderNodeCard)}</div>
                  ) : (
                    renderNodeCard(row.nodes[0]!)
                  )}
                </div>
              ))
            )}
            {activeRun && orderedNodes.length > 0 && (
              <div className="erp-branch-note">
                Advanced: add Proposer / Judge / Vote / Manager Review branch nodes in the legacy workspace.
              </div>
            )}
          </div>
          <div className="erp-proc-side-settings">
            <NodeSettings
              node={selectedNode}
              nodeState={selectedNode ? nodeStateOf(activeRun, selectedNode.id) : undefined}
              processingMode={activeRun?.processing_mode ?? ''}
              providerOptions={workflowProviderOptions}
              onPatch={patchNodeData}
              onToggleCross={toggleCrossVlm}
              crossOn={Boolean(activeRun?.graph_json.nodes.some(n => n.type === 'VLMDoubleCheck'))}
              busy={busy}
            />
            {activeRun && (
              <ul className="erp-tl">
                <div className="lab">Run timeline</div>
                {orderedNodes.map(node => {
                  const state = nodeStateOf(activeRun, node.id)
                  const kind = statusKind(state?.status)
                  const cls = kind === 'done' ? 'ok' : kind === 'run' ? 'now' : kind === 'fail' ? 'fail' : 'wait'
                  const v = state?.duration_ms != null ? fmtDuration(state.duration_ms) : kind === 'run' ? '\u2026' : '\u2014'
                  const label = String((node.data as Record<string, unknown>)?.label ?? node.type)
                  const fb = nodeDetailLines(state)[0]
                  return (
                    <li key={node.id} className={cls}>
                      <span className="c" />
                      <span className="g">
                        {label}
                        {fb ? <span className="erp-tl-fb">{fb.v}</span> : null}
                      </span>
                      <span className="v">{v}</span>
                    </li>
                  )
                })}
              </ul>
            )}
          </div>
        </aside>
      </div>

      <GridFooter
        selectedCount={0}
        stats={[
          { label: 'Files', value: String(activeRun?.files.length ?? 0) },
          { label: 'Pages', value: String(activeRun?.files.reduce((s, f) => s + (f.page_count ?? 0), 0) ?? 0) },
          { label: 'Stage', value: statusText(nodeStateOf(activeRun, selectedNodeId ?? '')) || '-' },
          { label: 'Status', value: activeRun?.run_status ?? '-' },
        ]}
      />

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

      {showReVlm && activeRun && !reVlmLocked ? (
        <ReVlmModal
          files={activeRun.files}
          initialSelectedFileIds={reVlmInitialFileIds}
          busy={busy}
          workflowContext={{
            graph: activeRun.graph_json,
            templates,
            processingMode: activeRun.processing_mode,
            providerOptions: workflowProviderOptions,
          }}
          onConfirm={payload => void handleReVlmConfirm(payload)}
          onCancel={() => {
            setShowReVlm(false)
            setReVlmInitialFileIds([])
          }}
        />
      ) : null}

      <ConfirmDialog
        open={headerChangeConfirm != null}
        title="Workflow settings changed"
        message="This run already has extracted data. Changing workflow settings updates the graph only. Click Re-VLM to re-extract with the new settings."
        confirmLabel="Apply change"
        onConfirm={() => headerChangeConfirm?.onConfirm()}
        onCancel={() => headerChangeConfirm?.onCancel()}
      />
    </div>
  )
}

function NodeSettings({
  node,
  nodeState,
  processingMode,
  providerOptions,
  onPatch,
  onToggleCross,
  crossOn,
  busy,
}: {
  node: WorkflowGraph['nodes'][number] | null
  nodeState?: NodeState
  processingMode: string
  providerOptions: string[]
  onPatch: (nodeId: string, patch: Record<string, unknown>) => void
  onToggleCross: (on: boolean) => void
  crossOn: boolean
  busy: boolean
}) {
  if (!node) return <div className="erp-empty">Select a node.</div>
  const d = (node.data ?? {}) as Record<string, unknown>
  const label = String(d.label ?? node.type)
  const tag = tagForType(node.type)
  const isArMode = processingMode.toUpperCase() === 'AR'
  const nodeTablePresetOptions = isArMode
    ? AP_TABLE_OPTIONS_ORDER.filter(o => o === 'default')
    : AP_TABLE_OPTIONS_ORDER
  const detailLines = nodeDetailLines(nodeState)
  const defaultProvider = providerOptions[0] ?? 'Qwen'
  const providerValue = resolveProviderSelection(
    d.provider != null ? String(d.provider) : null,
    providerOptions,
  )

  return (
    <>
      <div className="erp-set-title">{label}</div>
      <div className="erp-set-sub">
        {node.type}
        {tag ? ` \u00b7 ${tag}` : ''}
      </div>

      {node.type === 'ModeConfig' && (
        <div className="erp-set-row">
          <label>Processing mode</label>
          <input type="text" value={String(d.processingMode ?? '')} disabled />
        </div>
      )}

      {node.type === 'ReceiptStyle' && (
        <>
          <div className="erp-set-row">
            <label>Receipt layout</label>
            <select
              value={String(d.receiptSignal ?? 'guess')}
              disabled={busy}
              onChange={e => onPatch(node.id, { receiptSignal: e.target.value })}
            >
              {AP_RECEIPT_OPTIONS_ORDER.map(opt => (
                <option key={opt} value={opt}>
                  {RECEIPT_SIGNAL_LABELS[opt]}
                </option>
              ))}
            </select>
          </div>
          <div className="erp-set-row">
            <label>Table style</label>
            <select
              value={String(d.tablePreset ?? 'default')}
              disabled={busy}
              onChange={e => onPatch(node.id, { tablePreset: e.target.value })}
            >
              {nodeTablePresetOptions.map(opt => (
                <option key={opt} value={opt}>
                  {TABLE_PRESET_LABELS[opt]}
                </option>
              ))}
            </select>
          </div>
        </>
      )}

      {node.type === 'VLM_API' && (
        <>
          <div className="erp-set-row">
            <label>Provider</label>
            <select
              value={providerValue}
              disabled={busy}
              onChange={e => onPatch(node.id, { provider: e.target.value })}
            >
              {providerOptions.map(p => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <div className="erp-set-row">
            <label>Prompt preset</label>
            <select value={String(d.promptPreset ?? 'default')} disabled={busy} onChange={e => onPatch(node.id, { promptPreset: e.target.value })}>
              <option value="default">default</option>
              <option value="receipt">receipt</option>
              <option value="bank">bank</option>
            </select>
          </div>
          <div className="erp-set-row">
            <label>Cross-VLM second pass</label>
            <span className="erp-toggle" onClick={() => !busy && onToggleCross(!crossOn)}>
              <span className={`sw${crossOn ? ' on' : ''}`}>
                <i />
              </span>
              {crossOn ? 'on' : 'off'}
            </span>
          </div>
        </>
      )}

      {node.type === 'VLMDoubleCheck' && (
        <>
          <div className="erp-set-row">
            <label>Provider</label>
            <select
              value={providerValue}
              disabled={busy}
              onChange={e => onPatch(node.id, { provider: e.target.value })}
            >
              {providerOptions.map(p => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
          <div className="erp-set-row">
            <label>Merge policy</label>
            <select value={String(d.mergePolicy ?? 'cross_vlm')} disabled={busy} onChange={e => onPatch(node.id, { mergePolicy: e.target.value })}>
              <option value="cross_vlm">cross_vlm</option>
              <option value="prefer_primary">prefer_primary</option>
            </select>
          </div>
        </>
      )}

      {['VLMProposer', 'VLMJudge', 'ManagerReview'].includes(node.type) && (
        <div className="erp-set-row">
          <label>Provider</label>
          <select
            value={resolveProviderSelection(
              d.provider != null ? String(d.provider) : defaultProvider,
              providerOptions,
            )}
            disabled={busy}
            onChange={e => onPatch(node.id, { provider: e.target.value })}
          >
            {providerOptions.map(p => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </div>
      )}

      {['Files', 'TableReview', 'CoADeploy', 'SaveResult'].includes(node.type) && (
        <div className="erp-set-sub">No editable settings for this node.</div>
      )}

      {detailLines.length > 0 ? (
        <div className="erp-set-feedback">
          {detailLines.map(line => (
            <div className="erp-set-feedback-row" key={line.k}>
              <span className="erp-set-feedback-label">{line.k}</span>
              <p className="erp-set-feedback-text">{line.v}</p>
            </div>
          ))}
        </div>
      ) : null}
    </>
  )
}
