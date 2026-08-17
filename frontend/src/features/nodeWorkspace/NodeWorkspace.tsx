import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react'
import type { Node } from '@xyflow/react'
import type { ApVlmReceiptSignal, ApVlmTablePreset } from '../workspace/apComposerOptions'
import { CompanyPickerModal } from '../../components/CompanyPickerModal'
import { FilePreviewModal, useTaskFilePreview } from '../../components/filePreview'
import { useAuth } from '../../contexts/AuthContext'
import { api } from '../../services/api'
import {
  apReceiptReady,
  isHorizontalLayout,
  layoutGraphVertical,
  needsVerticalRelayout,
  REQUIRED_RUN_NODE_TYPES,
} from './defaultGraphs'
import { CutoverNotice, dismissCutoverNotice, shouldShowCutoverNotice } from './CutoverNotice'
import { maybeGenerateWorkflowTitle } from './generateWorkflowTitle'
import {
  batchPayloadRowCount,
  buildBatchTablePayloadsFromRun,
  combineBatchTablePayloads,
  frozenPresetForBatch,
  loadAllBatchTablePayloads,
  mapCombinedPayloadToBatches,
  mergeBatchTablePayloads,
  moveFileRowsBetweenBatches,
  persistBatchTableSnapshot,
  reconcileBatchPayloadsWithRun,
  resolveBatchTablePayloadAfterVlm,
} from './batchTableSnapshots'
import { getOcrByFileFromRun } from './ocrTableBuilder'
import { hasOcrDataOnRun, tablePayloadHasRows } from './tablePayloadMerge'
import { committedTimelineBatches, composerStagingFiles, workflowQueueFiles } from './runFileBatches'
import { enrichNodesFromRun, type WorkflowNodeData } from './nodes/workflowNodeTypes'
import {
  ConfirmDialog,
  ControlsBottomSheet,
  ControlsFilesPanel,
  ControlsLogPanel,
  ControlsPane,
  NodeSettingsPanel,
  OpenWebUIShell,
  receiptSettingsFromGraph,
  RunComposer,
  RunNavbar,
  RunSidebar,
  RunTimeline,
  ReVlmModal,
  useMediaQuery,
  WorkflowSearchModal,
  WorkflowSkillsModal,
  type ControlsTab,
} from './shell'
import {
  applyWorkflowSettingsToGraph,
  workflowSettingsChanged,
} from './shell/processingWorkflowHeader'
import { TemplatesManagerModal } from './shell/TemplatesManagerModal'
import { useWorkflowRunEvents } from './useWorkflowRunEvents'
import { WorkflowCanvas } from './WorkflowCanvas'
import {
  workflowApi,
  applyRunStoppedLocally,
  applyRunReVlmLocally,
  runLooksProcessing,
  shouldIgnoreRunRefreshAfterStop,
  type WorkflowFolder,
  type WorkflowGraph,
  type WorkflowNodeCatalogEntry,
  type WorkflowRun,
  type WorkflowRunSummary,
  type WorkflowSkill,
  type WorkflowTemplate,
} from './workflowApi'
import {
  resolvePaletteTemplateSelection,
  sortPaletteTemplates,
  templateMatchingGraph,
} from './workflowTemplates'
import { useWorkflowRuns } from './workflowRunStore'
import type { ReVlmConfirmPayload } from './reVlmReasonChips'
import { WORKFLOW_THEME_KEY, readStoredWorkflowTheme, type WorkflowTheme } from './workflowTheme'
import { processingModeLabel } from '../../components/ModeSelector'
import './comfy-theme.css'
import './reactflow-comfy.css'
import './review-panel.css'

const MODES = ['AR', 'AP', 'BANK', 'OTHER', 'RECON'] as const
const GRAPH_PATCH_DEBOUNCE_MS = 512
const API_ERROR_DEDUPE_MS = 5000
const RECEIPT_NODE_ID = 'receipt'
const SIDEBAR_OPEN_KEY = 'workflow-sidebar-open'
const SIDEBAR_WIDTH_KEY = 'workflow-sidebar-width'
const CONTROLS_WIDTH_KEY = 'workflow-controls-width'

const REQUIRED_NODE_LABELS: Record<string, string> = {
  Files: 'Files node',
  ModeConfig: 'Mode node',
  TableReview: 'Table Review node',
  CoADeploy: 'CoA Deploy node',
  SaveResult: 'Save Result node',
}

function hasGraphPath(graph: WorkflowRun['graph_json'], sourceIds: Set<string>, targetIds: Set<string>): boolean {
  if (sourceIds.size === 0 || targetIds.size === 0) return false
  const nextBySource = new Map<string, string[]>()
  for (const edge of graph.edges) {
    const list = nextBySource.get(edge.source) ?? []
    list.push(edge.target)
    nextBySource.set(edge.source, list)
  }
  const queue = [...sourceIds]
  const seen = new Set(queue)
  while (queue.length > 0) {
    const id = queue.shift()
    if (!id) continue
    if (targetIds.has(id)) return true
    for (const next of nextBySource.get(id) ?? []) {
      if (seen.has(next)) continue
      seen.add(next)
      queue.push(next)
    }
  }
  return false
}

function workflowRunChecklist(graph: WorkflowRun['graph_json']): string[] {
  const items: string[] = []
  for (const type of REQUIRED_RUN_NODE_TYPES) {
    if (!graph.nodes.some(node => node.type === type)) {
      items.push(REQUIRED_NODE_LABELS[type] ?? type)
    }
  }
  const producers = new Set(
    graph.nodes.filter(node => ['VLM_API', 'MergeResult', 'VLMDoubleCheck'].includes(node.type)).map(node => node.id),
  )
  const tableNodes = new Set(graph.nodes.filter(node => node.type === 'TableReview').map(node => node.id))
  if (!hasGraphPath(graph, producers, tableNodes)) {
    items.push('OCR result path to Table Review')
  }
  return items
}

function readStoredNumber(key: string, fallback: number, min: number, max: number): number {
  try {
    const raw = localStorage.getItem(key)
    const parsed = raw ? Number(raw) : NaN
    if (!Number.isFinite(parsed)) return fallback
    return Math.max(min, Math.min(max, parsed))
  } catch {
    return fallback
  }
}

function maxControlsWidth(): number {
  return Math.max(300, Math.floor(window.innerWidth * 0.5))
}

function readSidebarOpen(): boolean {
  try {
    const v = localStorage.getItem(SIDEBAR_OPEN_KEY)
    return v == null ? true : v === 'true'
  } catch {
    return true
  }
}

export default function NodeWorkspace() {
  const { user, accessToken, activeCompany, companies, needsCompanyPick, switchCompany, refreshCompanies, logout } =
    useAuth()
  const companyId = activeCompany?.id
  const companyIdRef = useRef(companyId)
  const { state: runsState, dispatch: runsDispatch, activeRun, setFullRun } = useWorkflowRuns()
  const { summaries, activeRunId } = runsState

  const scopedSummaries = useMemo(
    () => (companyId ? summaries.filter(s => s.company_id === companyId) : []),
    [summaries, companyId],
  )

  const [nodes, setNodes] = useState<Node<WorkflowNodeData>[]>([])
  const [batchTablePayloads, setBatchTablePayloads] = useState<
    Record<string, Record<string, unknown>>
  >({})
  const [busyRunId, setBusyRunId] = useState<string | null>(null)
  const [coaBusy, setCoaBusy] = useState(false)
  const [apiError, setApiError] = useState<string | null>(null)
  const [showNotice, setShowNotice] = useState(shouldShowCutoverNotice)
  const [showNewMode, setShowNewMode] = useState(false)
  const [showReVlm, setShowReVlm] = useState(false)
  const [reVlmInitialFileIds, setReVlmInitialFileIds] = useState<string[]>([])
  const [showManager, setShowManager] = useState(false)
  const [showSkills, setShowSkills] = useState(false)
  const [incompleteWorkflowItems, setIncompleteWorkflowItems] = useState<string[]>([])
  const [skillsBusy, setSkillsBusy] = useState(false)
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([])
  const [workflowSkills, setWorkflowSkills] = useState<WorkflowSkill[]>([])
  const [nodeCatalog, setNodeCatalog] = useState<WorkflowNodeCatalogEntry[]>([])
  const [folders, setFolders] = useState<WorkflowFolder[]>([])
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([])
  const [controlsOpen, setControlsOpen] = useState(false)
  const [controlsTab, setControlsTab] = useState<ControlsTab>('workflow')
  const [sidebarOpen, setSidebarOpen] = useState(readSidebarOpen)
  const [sidebarWidth, setSidebarWidth] = useState(() => readStoredNumber(SIDEBAR_WIDTH_KEY, 260, 220, 420))
  const [controlsWidth, setControlsWidth] = useState(() =>
    readStoredNumber(CONTROLS_WIDTH_KEY, 380, 300, maxControlsWidth()),
  )
  const [showSearch, setShowSearch] = useState(false)
  const [deleteRunTarget, setDeleteRunTarget] = useState<{ id: string; title: string } | null>(null)
  const [deployTemplateTarget, setDeployTemplateTarget] = useState<WorkflowTemplate | null>(null)
  const [selectedPaletteTemplateId, setSelectedPaletteTemplateId] = useState('')
  const [archivedSummaries, setArchivedSummaries] = useState<WorkflowRunSummary[]>([])
  const [expandAllTablesNonce, setExpandAllTablesNonce] = useState(0)
  const bumpExpandAllTables = useCallback(() => {
    setExpandAllTablesNonce(n => n + 1)
  }, [])
  const [showArchived, setShowArchived] = useState(false)
  const [dismissedWorkflowErrorKey, setDismissedWorkflowErrorKey] = useState('')
  const [dismissedWorkflowWarningsKey, setDismissedWorkflowWarningsKey] = useState('')
  const [latchedWorkflowError, setLatchedWorkflowError] = useState<{
    runId: string
    key: string
    message: string
  } | null>(null)
  const [latchedWorkflowWarnings, setLatchedWorkflowWarnings] = useState<{
    runId: string
    key: string
    messages: string[]
  } | null>(null)
  const [theme, setTheme] = useState<WorkflowTheme>(readStoredWorkflowTheme)
  const [reVlmTaskFileIds, setReVlmTaskFileIds] = useState<Set<string>>(() => new Set())
  const reVlmTaskFileIdsRef = useRef(reVlmTaskFileIds)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const titleGeneratedRef = useRef<Set<string>>(new Set())
  const syncedRunIdRef = useRef<string | null>(null)
  const activeRunIdRef = useRef<string | null>(null)
  const summariesRef = useRef(summaries)
  const graphPatchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingGraphRef = useRef<{ runId: string; graph: WorkflowRun['graph_json'] } | null>(null)
  const syncedPaletteRunRef = useRef<string | null>(null)
  const lastApiErrorRef = useRef<{ msg: string; at: number } | null>(null)
  const nodesRef = useRef(nodes)
  const batchTablePayloadsRef = useRef(batchTablePayloads)
  const activeRunRef = useRef(activeRun)
  const stopGuardRunIdRef = useRef<string | null>(null)
  const executePollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const isMobile = useMediaQuery('(max-width: 767px)')

  useEffect(() => {
    companyIdRef.current = companyId
  }, [companyId])

  useEffect(() => {
    reVlmTaskFileIdsRef.current = reVlmTaskFileIds
  }, [reVlmTaskFileIds])

  useEffect(() => {
    setReVlmTaskFileIds(new Set())
  }, [activeRunId])

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_OPEN_KEY, String(sidebarOpen))
    } catch {
      /* ignore */
    }
  }, [sidebarOpen])

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_WIDTH_KEY, String(sidebarWidth))
    } catch {
      /* ignore */
    }
  }, [sidebarWidth])

  useEffect(() => {
    try {
      localStorage.setItem(CONTROLS_WIDTH_KEY, String(controlsWidth))
    } catch {
      /* ignore */
    }
  }, [controlsWidth])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setShowSearch(true)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (!showSearch || !companyId) return
    void workflowApi
      .listRuns(companyId, true)
      .then(setArchivedSummaries)
      .catch(() => setArchivedSummaries([]))
  }, [showSearch, companyId])

  useEffect(() => {
    nodesRef.current = nodes
  }, [nodes])

  useEffect(() => {
    activeRunIdRef.current = activeRunId
    setSelectedNodeIds([])
  }, [activeRunId])

  useEffect(() => {
    summariesRef.current = summaries
  }, [summaries])

  useEffect(() => {
    batchTablePayloadsRef.current = batchTablePayloads
  }, [batchTablePayloads])

  const combinedTablePayload = useCallback(
    (run: WorkflowRun, payloads = batchTablePayloadsRef.current) =>
      combineBatchTablePayloads(payloads, run),
    [],
  )

  useEffect(() => {
    activeRunRef.current = activeRun
  }, [activeRun])

  const persistBatchTablesOnUnload = useCallback(async () => {
    const run = activeRunRef.current
    if (!run?.task_id || !companyId) return
    const batches = committedTimelineBatches(run.files)
    for (const batch of batches) {
      const payload = batchTablePayloadsRef.current[batch.uploadBatchId]
      if (!payload || !tablePayloadHasRows(payload, run.processing_mode)) continue
      const preset = frozenPresetForBatch(run, batch.uploadBatchId)
      try {
        await persistBatchTableSnapshot(run, batch.uploadBatchId, payload, preset, companyId)
      } catch {
        /* best effort on unload */
      }
    }
  }, [companyId])

  useEffect(() => {
    const onPageHide = () => {
      void persistBatchTablesOnUnload()
    }
    window.addEventListener('pagehide', onPageHide)
    return () => window.removeEventListener('pagehide', onPageHide)
  }, [persistBatchTablesOnUnload])

  useEffect(() => {
    return () => {
      if (graphPatchTimerRef.current) clearTimeout(graphPatchTimerRef.current)
    }
  }, [])

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'dark') {
      root.classList.add('dark')
      root.dataset.workflowTheme = 'dark'
    } else {
      root.classList.remove('dark')
      delete root.dataset.workflowTheme
    }
    try {
      localStorage.setItem(WORKFLOW_THEME_KEY, theme)
    } catch {
      /* ignore */
    }
    return () => {
      root.classList.remove('dark')
      delete root.dataset.workflowTheme
    }
  }, [theme])

  const reportApiError = useCallback((err: unknown) => {
    const msg = err instanceof Error ? err.message : String(err)
    const now = Date.now()
    const prev = lastApiErrorRef.current
    if (prev && prev.msg === msg && now - prev.at < API_ERROR_DEDUPE_MS) return
    lastApiErrorRef.current = { msg, at: now }
    setApiError(msg)
  }, [])

  const clearApiError = useCallback(() => {
    setApiError(null)
    lastApiErrorRef.current = null
  }, [])

  const mergeServerRun = useCallback((run: WorkflowRun): WorkflowRun => {
    const pending = pendingGraphRef.current
    if (pending?.runId === run.id) {
      return { ...run, graph_json: pending.graph }
    }
    return run
  }, [])

  const setFullRunFromServer = useCallback(
    (run: WorkflowRun) => {
      if (companyIdRef.current && run.company_id !== companyIdRef.current) return
      if (shouldIgnoreRunRefreshAfterStop(stopGuardRunIdRef.current, run)) return
      setFullRun(mergeServerRun(run))
    },
    [setFullRun, mergeServerRun],
  )

  const clearExecutePoll = useCallback(() => {
    if (executePollRef.current) {
      window.clearInterval(executePollRef.current)
      executePollRef.current = null
    }
  }, [])

  const commitGraphPatch = useCallback(
    async (runId: string, g: WorkflowRun['graph_json']) => {
      if (!companyId) return
      const snapshot = pendingGraphRef.current ?? { runId, graph: g }
      try {
        const r = await workflowApi.patchRun(companyId, runId, g)
        runsDispatch({ type: 'patch_run', run: { ...r, graph_json: g } })
        if (
          pendingGraphRef.current?.runId === runId &&
          pendingGraphRef.current?.graph === snapshot.graph
        ) {
          pendingGraphRef.current = null
        }
      } catch (err) {
        reportApiError(err)
      }
    },
    [companyId, runsDispatch, reportApiError],
  )

  const persistGraphChange = useCallback(
    (runId: string, g: WorkflowRun['graph_json'], options?: { immediate?: boolean }) => {
      if (!companyId) return
      pendingGraphRef.current = { runId, graph: g }
      runsDispatch({ type: 'patch_run_graph', runId, graph: g })
      if (graphPatchTimerRef.current) clearTimeout(graphPatchTimerRef.current)
      const flush = () => {
        graphPatchTimerRef.current = null
        const pending = pendingGraphRef.current
        if (pending) {
          void commitGraphPatch(pending.runId, pending.graph)
        }
      }
      if (options?.immediate) {
        flush()
      } else {
        graphPatchTimerRef.current = setTimeout(flush, GRAPH_PATCH_DEBOUNCE_MS)
      }
    },
    [companyId, runsDispatch, commitGraphPatch],
  )

  const flushGraphBeforeRun = useCallback(
    async (runId: string, g: WorkflowRun['graph_json']) => {
      if (!companyId) return
      if (graphPatchTimerRef.current) {
        clearTimeout(graphPatchTimerRef.current)
        graphPatchTimerRef.current = null
      }
      pendingGraphRef.current = { runId, graph: g }
      await commitGraphPatch(runId, g)
    },
    [companyId, commitGraphPatch],
  )

  const patchGraphNode = useCallback(
    (nodeId: string, patch: Record<string, unknown>) => {
      const run = activeRunRef.current
      if (!run || !companyId) return
      const graph = { ...run.graph_json }
      graph.nodes = graph.nodes.map(n =>
        n.id === nodeId ? { ...n, data: { ...n.data, ...patch } } : n,
      )
      setNodes(prev =>
        prev.map(n => (n.id === nodeId ? { ...n, data: { ...n.data, ...patch } } : n)),
      )
      const immediate = 'provider' in patch || 'model' in patch
      persistGraphChange(run.id, graph, { immediate })
    },
    [companyId, persistGraphChange],
  )

  const activateRun = useCallback(
    async (id: string, scopeId: string) => {
      runsDispatch({ type: 'set_loading', id })
      try {
        const r = await workflowApi.getRun(scopeId, id)
        let run = r
        if (
          run.run_status === 'executing' &&
          run.files.length > 0 &&
          run.files.every(f => f.file_status === 'running')
        ) {
          run = await workflowApi.recoverStuck(scopeId, id)
        }
        run = mergeServerRun(run)
        if (run.company_id !== companyIdRef.current) return null
        setFullRun(run)
        const loaded = await loadAllBatchTablePayloads(run, scopeId)
        let payloads = hasOcrDataOnRun(run)
          ? mergeBatchTablePayloads(run, loaded, buildBatchTablePayloadsFromRun(run))
          : loaded
        if (Object.keys(payloads).length > 0) {
          payloads = reconcileBatchPayloadsWithRun(run, payloads)
        }
        const hasAnyRows = Object.values(payloads).some(p =>
          tablePayloadHasRows(p, run.processing_mode),
        )
        if (hasAnyRows) {
          setBatchTablePayloads(payloads)
        } else {
          setBatchTablePayloads({})
        }
        return run
      } catch (err) {
        reportApiError(err)
        return null
      } finally {
        runsDispatch({ type: 'set_loading', id: null })
      }
    },
    [setFullRun, mergeServerRun, runsDispatch, reportApiError],
  )

  const refreshRun = useCallback(
    async (id: string, scopeId: string) => {
      const r = await workflowApi.getRun(scopeId, id)
      if (r.company_id !== companyIdRef.current) return null
      const merged = mergeServerRun(r)
      setFullRun(merged)
      return merged
    },
    [setFullRun, mergeServerRun],
  )

  const loadRuns = useCallback(
    async (scopeId: string, archived: boolean) => {
      try {
        const list = await workflowApi.listRuns(scopeId, archived)
        summariesRef.current = list
        runsDispatch({ type: 'set_summaries', summaries: list })
        const cur = activeRunIdRef.current
        const nextActive = cur && list.some(s => s.id === cur) ? cur : list[0]?.id ?? null
        runsDispatch({ type: 'set_active', id: nextActive })
        if (nextActive) await activateRun(nextActive, scopeId)
      } catch (err) {
        reportApiError(err)
      }
    },
    [runsDispatch, activateRun, reportApiError],
  )

  const loadTemplates = useCallback(
    (scopeId: string) => {
      void workflowApi.listTemplates(scopeId).then(setTemplates).catch(reportApiError)
    },
    [reportApiError],
  )

  const loadNodeCatalog = useCallback(
    (scopeId: string, mode?: string) => {
      void workflowApi.nodeCatalog(scopeId, mode).then(setNodeCatalog).catch(reportApiError)
    },
    [reportApiError],
  )

  const workflowProviderOptions = useMemo(() => {
    for (const entry of nodeCatalog) {
      const raw = entry.params?.provider?.options
      if (Array.isArray(raw) && raw.length > 0) {
        return raw.map(String)
      }
    }
    return ['Qwen']
  }, [nodeCatalog])

  const loadWorkflowSkills = useCallback(
    (scopeId: string, mode?: string) => {
      void workflowApi.listSkills(scopeId, mode).then(setWorkflowSkills).catch(reportApiError)
    },
    [reportApiError],
  )

  const loadFolders = useCallback(
    (scopeId: string) => {
      void workflowApi.listFolders(scopeId).then(setFolders).catch(reportApiError)
    },
    [reportApiError],
  )

  useEffect(() => {
    if (!companyId) {
      runsDispatch({ type: 'reset' })
      setTemplates([])
      setFolders([])
      setBatchTablePayloads({})
      setNodes([])
      pendingGraphRef.current = null
      syncedRunIdRef.current = null
      syncedPaletteRunRef.current = null
      return
    }
    runsDispatch({ type: 'reset' })
    pendingGraphRef.current = null
    syncedRunIdRef.current = null
    syncedPaletteRunRef.current = null
    setBatchTablePayloads({})
    setNodes([])
    void loadRuns(companyId, showArchived)
    loadTemplates(companyId)
    loadFolders(companyId)
  }, [companyId, showArchived, loadRuns, loadTemplates, loadFolders, runsDispatch])

  useEffect(() => {
    if (!companyId) {
      setNodeCatalog([])
      setWorkflowSkills([])
      return
    }
    loadNodeCatalog(companyId, activeRun?.processing_mode)
    loadWorkflowSkills(companyId, activeRun?.processing_mode)
  }, [companyId, activeRun?.processing_mode, loadNodeCatalog, loadWorkflowSkills])

  useEffect(() => {
    if (!activeRun) return
    const err =
      typeof activeRun.node_states_json?.workflow_error === 'string'
        ? activeRun.node_states_json.workflow_error
        : null
    if (!err) return
    const key = `${activeRun.id}:${err}`
    if (dismissedWorkflowErrorKey === key) return
    setLatchedWorkflowError({ runId: activeRun.id, key, message: err })
  }, [activeRun?.id, activeRun?.node_states_json?.workflow_error, dismissedWorkflowErrorKey])

  useEffect(() => {
    if (!activeRun) return
    const warnings = Array.isArray(activeRun.node_states_json?.workflow_warnings)
      ? activeRun.node_states_json.workflow_warnings
      : []
    if (warnings.length === 0) return
    const key = `${activeRun.id}:${warnings.join('\u001e')}`
    if (dismissedWorkflowWarningsKey === key) return
    setLatchedWorkflowWarnings({ runId: activeRun.id, key, messages: warnings })
  }, [activeRun?.id, activeRun?.node_states_json?.workflow_warnings, dismissedWorkflowWarningsKey])

  const handleResume = useCallback(
    async (run: WorkflowRun, payload: Record<string, unknown>, skipCoa: boolean) => {
      if (!companyId) return
      setBusyRunId(run.id)
      try {
        const updated = await workflowApi.resume(companyId, run.id, payload, skipCoa)
        setFullRun(updated)
      } catch (err) {
        reportApiError(err)
      } finally {
        setBusyRunId(prev => (prev === run.id ? null : prev))
      }
    },
    [companyId, setFullRun, reportApiError],
  )

  const handleApprove = useCallback(
    async (run: WorkflowRun) => {
      setCoaBusy(true)
      try {
        const combined = combineBatchTablePayloads(batchTablePayloadsRef.current, run)
        await handleResume(run, combined, false)
      } finally {
        setCoaBusy(false)
      }
    },
    [handleResume],
  )

  const persistBatchTableChange = useCallback(
    (run: WorkflowRun, uploadBatchId: string, payload: Record<string, unknown>) => {
      if (!companyId || !run.task_id) return
      const preset = frozenPresetForBatch(run, uploadBatchId)
      void persistBatchTableSnapshot(run, uploadBatchId, payload, preset, companyId)
    },
    [companyId],
  )

  const syncNodes = useCallback(
    (run: WorkflowRun, payload?: Record<string, unknown>, preserveLayout = false) => {
      const pl = payload ?? combinedTablePayload(run)
      let graphJson = run.graph_json
      if (!preserveLayout) {
        const needsMigrate = isHorizontalLayout(graphJson) || needsVerticalRelayout(graphJson)
        graphJson = layoutGraphVertical(graphJson)
        if (needsMigrate) {
          persistGraphChange(run.id, graphJson)
        }
      }
      const handlers: Partial<WorkflowNodeData> = {
        onUpload: () => fileInputRef.current?.click(),
        onGraphDataChange: patchGraphNode,
        providerOptions: workflowProviderOptions,
        queueFiles: workflowQueueFiles(run.files, reVlmTaskFileIdsRef.current),
        onGraphStructureChange: (nextGraph: WorkflowGraph) => {
          if (!companyId) return
          persistGraphChange(run.id, nextGraph)
          runsDispatch({ type: 'patch_run_graph', runId: run.id, graph: nextGraph })
          setFullRun({ ...run, graph_json: nextGraph })
        },
        currentGraph: graphJson,
        tablePayload: pl,
        onOpenReview: () => bumpExpandAllTables(),
        onTableChange: p => {
          const batches = committedTimelineBatches(run.files)
          const batchId = batches[batches.length - 1]?.uploadBatchId
          if (!batchId) return
          setBatchTablePayloads(prev => ({ ...prev, [batchId]: p }))
          persistBatchTableChange(run, batchId, p)
        },
        onApprove: () => void handleApprove(run),
        onSkipCoa: () => {
          const combined = combinedTablePayload(run)
          void handleResume(run, combined, true)
        },
        coaBusy,
      }
      const enriched = enrichNodesFromRun(graphJson.nodes, run, handlers)
      setNodes(prev => {
        if (!preserveLayout || prev.length === 0) return enriched
        const posById = new Map(prev.map(n => [n.id, n.position]))
        return enriched.map(n => {
          const pos = posById.get(n.id)
          return pos ? { ...n, position: pos } : n
        })
      })
    },
    [
      patchGraphNode,
      batchTablePayloads,
      coaBusy,
      combinedTablePayload,
      handleApprove,
      handleResume,
      persistGraphChange,
      persistBatchTableChange,
      runsDispatch,
      companyId,
      setFullRun,
      workflowProviderOptions,
    ],
  )

  const deployTemplateToRun = useCallback(
    async (template: WorkflowTemplate) => {
      if (!companyId || !activeRun) return
      if (graphPatchTimerRef.current) {
        clearTimeout(graphPatchTimerRef.current)
        graphPatchTimerRef.current = null
      }
      try {
        const nextGraph = layoutGraphVertical(JSON.parse(JSON.stringify(template.graph_json)) as WorkflowGraph)
        const updated = await workflowApi.patchRun(companyId, activeRun.id, nextGraph)
        pendingGraphRef.current = null
        const runWithGraph = { ...updated, graph_json: nextGraph }
        runsDispatch({ type: 'patch_run', run: runWithGraph })
        setFullRun(runWithGraph)
        const loaded = await loadAllBatchTablePayloads(runWithGraph, companyId)
        const rebuilt = hasOcrDataOnRun(runWithGraph) ? buildBatchTablePayloadsFromRun(runWithGraph) : {}
        const tablePayloads = mergeBatchTablePayloads(
          runWithGraph,
          loaded,
          mergeBatchTablePayloads(runWithGraph, batchTablePayloadsRef.current, rebuilt),
        )
        if (
          Object.values(tablePayloads).some(p => tablePayloadHasRows(p, runWithGraph.processing_mode))
        ) {
          setBatchTablePayloads(tablePayloads)
        }
        setSelectedPaletteTemplateId(template.id)
        setSelectedNodeIds([])
        setNodes([])
        setIncompleteWorkflowItems(workflowRunChecklist(nextGraph))
        syncNodes(runWithGraph, combineBatchTablePayloads(tablePayloads, runWithGraph), false)
        setShowManager(false)
      } catch (err) {
        reportApiError(err)
      } finally {
        setDeployTemplateTarget(null)
      }
    },
    [
      activeRun,
      companyId,
      runsDispatch,
      setFullRun,
      syncNodes,
      reportApiError,
    ],
  )

  const handleMoveFileToBatch = useCallback(
    async (sourceBatchId: string, targetBatchId: string, fileId: string) => {
      if (!activeRun || !companyId) return
      const sourcePreset = frozenPresetForBatch(activeRun, sourceBatchId)
      const targetPreset = frozenPresetForBatch(activeRun, targetBatchId)
      if (sourcePreset !== targetPreset) {
        const ok = window.confirm(
          'Source and target batches use different table styles. Moved rows may not align with all columns. Continue?',
        )
        if (!ok) return
      }
      try {
        const updatedRun = await workflowApi.moveRunFileToBatch(
          companyId,
          activeRun.id,
          fileId,
          targetBatchId,
        )
        setFullRun(updatedRun)
        const { payloads: next, moved } = moveFileRowsBetweenBatches(
          sourceBatchId,
          targetBatchId,
          fileId,
          batchTablePayloadsRef.current,
          updatedRun,
        )
        setBatchTablePayloads(next)
        await persistBatchTableSnapshot(
          updatedRun,
          sourceBatchId,
          next[sourceBatchId] ?? {},
          sourcePreset,
          companyId,
        )
        await persistBatchTableSnapshot(
          updatedRun,
          targetBatchId,
          next[targetBatchId] ?? {},
          targetPreset,
          companyId,
        )
        syncNodes(updatedRun, combinedTablePayload(updatedRun, next), true)
        if (moved === 0) {
          reportApiError(
            new Error(
              'File moved between batches, but no table rows matched that file. Expand Table review on the target batch.',
            ),
          )
        }
      } catch (err) {
        reportApiError(err)
      }
    },
    [
      activeRun,
      companyId,
      combinedTablePayload,
      setFullRun,
      reportApiError,
    ],
  )

  const removeGraphNodes = useCallback(
    (nodeIds: string[]) => {
      if (!activeRun || !companyId) return
      const ids = new Set(nodeIds)
      if (ids.size === 0) return
      const nextGraph: WorkflowGraph = {
        ...activeRun.graph_json,
        nodes: activeRun.graph_json.nodes.filter(node => !ids.has(node.id)),
        edges: activeRun.graph_json.edges.filter(edge => !ids.has(edge.source) && !ids.has(edge.target)),
      }
      const checklist = workflowRunChecklist(nextGraph)
      persistGraphChange(activeRun.id, nextGraph)
      runsDispatch({ type: 'patch_run_graph', runId: activeRun.id, graph: nextGraph })
      setFullRun({ ...activeRun, graph_json: nextGraph })
      setSelectedNodeIds(prev => prev.filter(id => !ids.has(id)))
      setIncompleteWorkflowItems(checklist)
      syncNodes({ ...activeRun, graph_json: nextGraph }, combinedTablePayload(activeRun), true)
    },
    [
      activeRun,
      companyId,
      persistGraphChange,
      runsDispatch,
      setFullRun,
      syncNodes,
      combinedTablePayload,
    ],
  )

  const autoLayoutGraph = useCallback(() => {
    if (!activeRun || !companyId) return
    const nextGraph = layoutGraphVertical(activeRun.graph_json)
    persistGraphChange(activeRun.id, nextGraph)
    runsDispatch({ type: 'patch_run_graph', runId: activeRun.id, graph: nextGraph })
    setFullRun({ ...activeRun, graph_json: nextGraph })
    syncNodes({ ...activeRun, graph_json: nextGraph }, combinedTablePayload(activeRun), true)
  }, [
    activeRun,
    companyId,
    persistGraphChange,
    runsDispatch,
    setFullRun,
    syncNodes,
    combinedTablePayload,
  ])

  const addCatalogNode = useCallback(
    (entry: WorkflowNodeCatalogEntry) => {
      if (!activeRun || !companyId) return
      const existingIds = new Set(activeRun.graph_json.nodes.map(n => n.id))
      const base = entry.type.replace(/[^A-Za-z0-9_]/g, '_').toLowerCase()
      let suffix = activeRun.graph_json.nodes.length + 1
      let id = `${base}_${suffix}`
      while (existingIds.has(id)) {
        suffix += 1
        id = `${base}_${suffix}`
      }
      const anchor = activeRun.graph_json.nodes.find(node => node.id === selectedNodeIds[0])
      const position = anchor
        ? { x: anchor.position.x + 260, y: anchor.position.y }
        : { x: 320, y: 120 + (activeRun.graph_json.nodes.length % 4) * 40 }
      const nextGraph: WorkflowGraph = {
        ...activeRun.graph_json,
        nodes: [
          ...activeRun.graph_json.nodes,
          {
            id,
            type: entry.type,
            position,
            data: {
              ...entry.defaults,
              label: entry.label,
              nodeType: entry.type,
              description: entry.description,
            },
          },
        ],
      }
      persistGraphChange(activeRun.id, nextGraph)
      runsDispatch({ type: 'patch_run_graph', runId: activeRun.id, graph: nextGraph })
      setFullRun({ ...activeRun, graph_json: nextGraph })
      setSelectedNodeIds([id])
      syncNodes({ ...activeRun, graph_json: nextGraph }, combinedTablePayload(activeRun), true)
    },
    [
      activeRun,
      companyId,
      persistGraphChange,
      runsDispatch,
      setFullRun,
      selectedNodeIds,
      syncNodes,
      combinedTablePayload,
    ],
  )

  const handleCanvasNodeClick = useCallback((node: Node<WorkflowNodeData>, event: ReactMouseEvent) => {
    setSelectedNodeIds(prev => {
      if (!event.shiftKey) return [node.id]
      return prev.includes(node.id) ? prev.filter(id => id !== node.id) : [...prev, node.id]
    })
    if (node.type === 'TableReview') {
      bumpExpandAllTables()
    }
  }, [bumpExpandAllTables])

  const replaceWorkflowSkill = useCallback((skill: WorkflowSkill) => {
    setWorkflowSkills(prev => prev.map(item => (item.id === skill.id ? skill : item)))
  }, [])

  const handleSaveWorkflowSkill = useCallback(
    async (skill: WorkflowSkill, structured: Record<string, string>) => {
      if (!companyId) return
      setSkillsBusy(true)
      try {
        const updated = await workflowApi.updateSkill(companyId, skill.mode, skill.skill_key, structured)
        replaceWorkflowSkill(updated)
      } catch (err) {
        reportApiError(err)
      } finally {
        setSkillsBusy(false)
      }
    },
    [companyId, replaceWorkflowSkill, reportApiError],
  )

  const handleResetWorkflowSkill = useCallback(
    async (skill: WorkflowSkill) => {
      if (!companyId) return
      setSkillsBusy(true)
      try {
        const updated = await workflowApi.resetSkill(companyId, skill.mode, skill.skill_key)
        replaceWorkflowSkill(updated)
      } catch (err) {
        reportApiError(err)
      } finally {
        setSkillsBusy(false)
      }
    },
    [companyId, replaceWorkflowSkill, reportApiError],
  )

  const handleRollbackWorkflowSkill = useCallback(
    async (skill: WorkflowSkill, version?: number) => {
      if (!companyId) return
      setSkillsBusy(true)
      try {
        const updated = await workflowApi.rollbackSkill(companyId, skill.mode, skill.skill_key, version)
        replaceWorkflowSkill(updated)
      } catch (err) {
        reportApiError(err)
      } finally {
        setSkillsBusy(false)
      }
    },
    [companyId, replaceWorkflowSkill, reportApiError],
  )

  const handleExportAuditJson = useCallback(async () => {
    if (!companyId || !activeRun) return
    try {
      const payload = await workflowApi.exportAuditJson(companyId, activeRun.id)
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `${activeRun.title || activeRun.id}-audit.json`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch (err) {
      reportApiError(err)
    }
  }, [companyId, activeRun, reportApiError])

  const runSyncKey = useMemo(
    () =>
      activeRun
        ? [
            activeRun.id,
            activeRun.run_status,
            activeRun.files.length,
            JSON.stringify(activeRun.node_states_json ?? null),
          ].join('|')
        : '',
    [activeRun],
  )

  const syncTableFromRun = useCallback(
    (r: WorkflowRun, persistSnapshot: boolean) => {
      const ocrFileIds = Object.keys(getOcrByFileFromRun(r))
      if (!ocrFileIds.length) return
      const batches = committedTimelineBatches(r.files)
      let next = { ...batchTablePayloadsRef.current }
      let anyUpdated = false

      const isExecuting = r.run_status === 'executing'

      for (const batch of batches) {
        const batchTaskIds = batch.files.map(f => f.task_file_id)
        if (isExecuting) {
          const batchActive = batch.files.some(f =>
            ['running', 'pending', 'warning', 'failed'].includes(f.file_status),
          )
          if (!batchActive) continue
        }
        const hasOcrForBatch =
          batchTaskIds.some(id => ocrFileIds.includes(id)) || ocrFileIds.includes('workflow')
        if (!batchTaskIds.length || !hasOcrForBatch) continue
        const base = next[batch.uploadBatchId] ?? {}
        const merged = resolveBatchTablePayloadAfterVlm(
          r,
          batch.uploadBatchId,
          base,
          batchTaskIds,
        )
        if (!tablePayloadHasRows(merged, r.processing_mode)) continue
        const prevCount = batchPayloadRowCount(base, r.processing_mode)
        const nextCount = batchPayloadRowCount(merged, r.processing_mode)
        const isMergeSource = r.node_states_json?.table_source === 'merge'
        const allowShrink = isMergeSource || r.run_status === 'executing' || r.run_status === 'draft'
        if (nextCount <= prevCount && tablePayloadHasRows(base, r.processing_mode) && !allowShrink) continue
        next = { ...next, [batch.uploadBatchId]: merged }
        anyUpdated = true
        if (persistSnapshot && companyId && r.run_status === 'awaiting_review') {
          const preset = frozenPresetForBatch(r, batch.uploadBatchId)
          void persistBatchTableSnapshot(r, batch.uploadBatchId, merged, preset, companyId)
        }
      }

      if (!anyUpdated) return
      setBatchTablePayloads(next)
      bumpExpandAllTables()
      syncNodes(r, combinedTablePayload(r, next), true)
    },
    [companyId, combinedTablePayload, syncNodes],
  )

  useEffect(() => {
    if (!activeRun || !companyId) return
    const states = activeRun.node_states_json ?? {}
    const hasPool2 = Boolean(states.pool2_package_id || states.pool2_storage_path)
    if (activeRun.run_status !== 'completed' || !hasPool2) return
    let cancelled = false
    void (async () => {
      try {
        const saved = await loadAllBatchTablePayloads(activeRun, companyId)
        const pkg = await workflowApi.getApprovedPackage(companyId, activeRun.id)
        if (cancelled) return
        if (!pkg.approved_payload) {
          if (Object.keys(saved).length > 0) {
            setBatchTablePayloads(saved)
            syncNodes(activeRun, combineBatchTablePayloads(saved, activeRun), true)
          }
          return
        }
        const mapped = mapCombinedPayloadToBatches(
          activeRun,
          pkg.approved_payload as Record<string, unknown>,
        )
        const built = buildBatchTablePayloadsFromRun(activeRun)
        const mergedFromRun = hasOcrDataOnRun(activeRun)
          ? mergeBatchTablePayloads(activeRun, mapped, built)
          : mapped
        const final = mergeBatchTablePayloads(activeRun, saved, mergedFromRun)
        setBatchTablePayloads(final)
        syncNodes(activeRun, combineBatchTablePayloads(final, activeRun), true)
      } catch {
        if (cancelled || !activeRun || !hasOcrDataOnRun(activeRun)) return
        const built = buildBatchTablePayloadsFromRun(activeRun)
        setBatchTablePayloads(built)
        syncNodes(activeRun, combineBatchTablePayloads(built, activeRun), true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [activeRun?.id, activeRun?.run_status, activeRun?.node_states_json, companyId])

  useEffect(() => {
    if (!activeRun) return
    const preserveLayout = syncedRunIdRef.current === activeRun.id
    syncedRunIdRef.current = activeRun.id
    const hasAnyRows = committedTimelineBatches(activeRun.files).some(b =>
      tablePayloadHasRows(batchTablePayloads[b.uploadBatchId] ?? {}, activeRun.processing_mode),
    )
    const canSyncTable =
      activeRun.run_status === 'awaiting_review' ||
      (activeRun.run_status === 'draft' && hasOcrDataOnRun(activeRun))
    const shouldRebuild = canSyncTable && !hasAnyRows && hasOcrDataOnRun(activeRun)
    if (shouldRebuild) {
      syncTableFromRun(activeRun, true)
      syncNodes(activeRun, undefined, preserveLayout)
      bumpExpandAllTables()
    } else if (canSyncTable && hasOcrDataOnRun(activeRun)) {
      syncTableFromRun(activeRun, true)
      syncNodes(activeRun, undefined, preserveLayout)
    } else {
      syncNodes(activeRun, undefined, preserveLayout)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed by runSyncKey
  }, [runSyncKey, activeRunId])

  useWorkflowRunEvents({
    runId: activeRunId,
    runStatus: activeRun?.run_status,
    companyId,
    accessToken,
    enabled: Boolean(companyId && activeRun?.company_id === companyId),
    onEvent: run => {
      setFullRunFromServer(run)
      if (run.run_status === 'executing' || run.run_status === 'awaiting_review') {
        syncTableFromRun(run, run.run_status === 'awaiting_review')
      }
    },
    onError: reportApiError,
  })

  const applyTitleIfNeeded = useCallback(
    async (run: WorkflowRun) => {
      if (titleGeneratedRef.current.has(run.id)) return
      const updated = await maybeGenerateWorkflowTitle(run)
      if (updated && updated.company_id === companyIdRef.current) {
        titleGeneratedRef.current.add(run.id)
        setFullRun(updated)
      }
    },
    [setFullRun],
  )

  const applyTableAfterVlm = useCallback(
    async (r: WorkflowRun, processedFileIds: string[]) => {
      if (!companyId) return null
      const loaded = await loadAllBatchTablePayloads(r, companyId)
      let next = { ...batchTablePayloadsRef.current, ...loaded }

      for (const batch of committedTimelineBatches(r.files)) {
        const batchTaskIds = batch.files.map(f => f.task_file_id)
        const idsForBatch =
          processedFileIds.length > 0
            ? batchTaskIds.filter(id => processedFileIds.includes(id))
            : batchTaskIds
        if (!idsForBatch.length) continue
        const preset = frozenPresetForBatch(r, batch.uploadBatchId)
        const base = next[batch.uploadBatchId] ?? {}
        const merged = resolveBatchTablePayloadAfterVlm(r, batch.uploadBatchId, base, idsForBatch)
        if (!tablePayloadHasRows(merged, r.processing_mode)) continue
        next = { ...next, [batch.uploadBatchId]: merged }
        await persistBatchTableSnapshot(r, batch.uploadBatchId, merged, preset, companyId)
      }

      setBatchTablePayloads(next)
      bumpExpandAllTables()
      syncNodes(r, combinedTablePayload(r, next), true)
      void applyTitleIfNeeded(r)
      return next
    },
    [companyId, combinedTablePayload, syncNodes, applyTitleIfNeeded],
  )

  const selectRun = useCallback(
    (id: string) => {
      runsDispatch({ type: 'set_active', id })
      bumpExpandAllTables()
      if (companyId) void activateRun(id, companyId)
    },
    [runsDispatch, companyId, activateRun],
  )

  const handleCreateWorkspace = useCallback(
    async (name: string) => {
      try {
        const row = await api.createCompany(name)
        localStorage.setItem('activeCompanyId', row.id)
        await refreshCompanies()
      } catch (err) {
        reportApiError(err)
      }
    },
    [refreshCompanies, reportApiError],
  )

  const handleDeleteWorkspace = useCallback(
    async (workspace: { id: string; name: string }) => {
      try {
        const result = await api.deleteCompany(workspace.id, workspace.name)
        if (activeCompany?.id === workspace.id) {
          if (result.suggested_company_id) {
            localStorage.setItem('activeCompanyId', result.suggested_company_id)
          } else {
            localStorage.removeItem('activeCompanyId')
          }
        }
        await refreshCompanies()
      } catch (err) {
        reportApiError(err)
      }
    },
    [activeCompany?.id, refreshCompanies, reportApiError],
  )

  const createRun = async (mode: string, templateId?: string) => {
    if (!companyId) {
      reportApiError(new Error('Select a workspace before creating a workflow run.'))
      return
    }
    try {
      const r = await workflowApi.createRun(companyId, mode, templateId)
      pendingGraphRef.current = null
      syncedRunIdRef.current = null
      setFullRun(r)
      runsDispatch({ type: 'set_active', id: r.id })
      setBatchTablePayloads({})
      bumpExpandAllTables()
      setShowNewMode(false)
      syncNodes(r, {}, false)
    } catch (err) {
      reportApiError(err)
    }
  }

  const handleRun = async () => {
    const run = activeRunRef.current
    if (!run || !companyId) return
    const graphForRun =
      pendingGraphRef.current?.runId === run.id
        ? pendingGraphRef.current.graph
        : run.graph_json
    if (!apReceiptReady(graphForRun)) return
    const runId = run.id
    setBusyRunId(runId)
    const scopeId = companyId
    clearExecutePoll()
    executePollRef.current = window.setInterval(() => {
      void refreshRun(runId, scopeId).then(r => {
        if (!r) return
        syncNodes(r, undefined, true)
        if (r.run_status === 'executing') {
          syncTableFromRun(r, false)
        }
      })
    }, 2000)
    try {
      await flushGraphBeforeRun(runId, graphForRun)
      const r = await workflowApi.execute(companyId, activeRun.id)
      setFullRunFromServer(r)
      if (r.run_status === 'awaiting_review') {
        syncTableFromRun(r, true)
      }
    } catch (err) {
      reportApiError(err)
      void refreshRun(activeRun.id, companyId).then(refreshed => {
        if (refreshed) syncNodes(refreshed, undefined, true)
      })
    } finally {
      clearExecutePoll()
      setBusyRunId(prev => (prev === runId ? null : prev))
    }
  }

  const handleStop = async () => {
    const run = activeRunRef.current
    if (!run || !companyId) return
    if (busyRunId !== run.id && run.run_status !== 'executing') return
    const label = run.title?.trim() || 'Untitled'
    const msg = `Stop all processing on "${label}"? In-progress files will be reset. Partial results may remain for review.`
    if (!window.confirm(msg)) return
    clearExecutePoll()
    stopGuardRunIdRef.current = run.id
    const stopped = applyRunStoppedLocally(run)
    setFullRunFromServer(stopped)
    syncNodes(stopped, undefined, true)
    try {
      const r = await workflowApi.cancel(companyId, run.id)
      setFullRunFromServer(r)
      syncNodes(r, undefined, true)
      if (r.run_status === 'awaiting_review') {
        syncTableFromRun(r, true)
      }
      if (!runLooksProcessing(r)) stopGuardRunIdRef.current = null
    } catch (err) {
      reportApiError(err)
      void workflowApi
        .getRun(companyId, run.id)
        .then(fresh => {
          if (shouldIgnoreRunRefreshAfterStop(stopGuardRunIdRef.current, fresh)) return
          setFullRunFromServer(fresh)
          syncNodes(fresh, undefined, true)
          if (!runLooksProcessing(fresh)) stopGuardRunIdRef.current = null
        })
        .catch(() => {})
    }
  }

  const handleReVlm = async ({ taskFileIds, rescanReasons, rescanNote, workflow }: ReVlmConfirmPayload) => {
    if (!activeRun || !companyId || taskFileIds.length === 0 || busyRunId === activeRun.id) return
    setShowReVlm(false)
    setReVlmInitialFileIds([])
    const nextRetry = new Set(reVlmTaskFileIdsRef.current)
    taskFileIds.forEach(id => nextRetry.add(id))
    reVlmTaskFileIdsRef.current = nextRetry
    setReVlmTaskFileIds(nextRetry)
    const optimistic = applyRunReVlmLocally(activeRun, taskFileIds, {
      rescanReasons,
      rescanNote: rescanNote || undefined,
    })
    setFullRunFromServer(optimistic)
    syncNodes(optimistic, undefined, true)
    const runId = activeRun.id
    setBusyRunId(runId)
    const scopeId = companyId
    clearExecutePoll()
    executePollRef.current = window.setInterval(() => {
      void refreshRun(runId, scopeId).then(r => {
        if (!r) return
        syncNodes(r, undefined, true)
        if (r.run_status === 'executing') {
          syncTableFromRun(r, false)
        }
      })
    }, 2000)
    try {
      let runForReVlm = activeRun
      if (
        workflow &&
        workflowSettingsChanged(activeRun.graph_json, templates, activeRun.processing_mode, workflow)
      ) {
        const nextGraph = applyWorkflowSettingsToGraph(activeRun.graph_json, templates, workflow)
        runForReVlm = await workflowApi.patchRun(companyId, runId, nextGraph)
        setFullRunFromServer(runForReVlm)
        syncNodes(runForReVlm, undefined, true)
      }
      const r = await workflowApi.reVlm(companyId, runId, taskFileIds, {
        rescan_reasons: rescanReasons,
        rescan_note: rescanNote || null,
      })
      setFullRunFromServer(r)
      syncNodes(r, undefined, true)
      await applyTableAfterVlm(r, taskFileIds)
    } catch (err) {
      reportApiError(err)
      void refreshRun(runId, companyId).then(run => {
        if (run) {
          syncNodes(run, undefined, true)
        }
      })
    } finally {
      clearExecutePoll()
      const cleared = new Set(reVlmTaskFileIdsRef.current)
      taskFileIds.forEach(id => cleared.delete(id))
      reVlmTaskFileIdsRef.current = cleared
      setReVlmTaskFileIds(cleared)
      setBusyRunId(prev => (prev === runId ? null : prev))
      const run = activeRunRef.current
      if (run) syncNodes(run, undefined, true)
    }
  }

  const handleRemoveFile = async (taskFileId: string) => {
    if (!activeRun || !companyId) return
    try {
      await workflowApi.removeRunFile(companyId, activeRun.id, taskFileId)
      const r = await refreshRun(activeRun.id, companyId)
      if (r) {
        syncNodes(r, undefined, true)
      }
    } catch (err) {
      reportApiError(err)
    }
  }

  const onFileInput = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const fileList = e.target.files
    if (!fileList?.length || !activeRun || !companyId) return
    await uploadFiles(Array.from(fileList))
    e.target.value = ''
  }

  const uploadFiles = async (fileList: File[]) => {
    if (!fileList.length || !activeRun || !companyId) return
    const uploadBatchId = crypto.randomUUID()
    const uploadedAt = new Date().toISOString()
    const beforeIds = new Set(activeRun.files.map(f => f.task_file_id))
    try {
      for (const f of fileList) {
        await workflowApi.uploadFile(companyId, activeRun.id, f, { uploadBatchId, uploadedAt })
      }
      const r = await refreshRun(activeRun.id, companyId)
      if (r) {
        syncNodes(r, undefined, true)
        const newFiles = r.files.filter(f => !beforeIds.has(f.task_file_id))
        newFiles.forEach((runFile, index) => {
          const local = fileList[index]
          if (local) filePreview.registerLocalFile(runFile.task_file_id, local)
        })
        void filePreview.prefetchFiles(newFiles.map(f => f.task_file_id))
      }
    } catch (err) {
      reportApiError(err)
    }
  }

  const toggleControls = () => {
    setControlsOpen(v => !v)
  }

  const archiveRun = async (id: string) => {
    if (!companyId) return
    try {
      await workflowApi.patchRunMeta(companyId, id, { archive: true })
      runsDispatch({ type: 'remove_run', id })
      summariesRef.current = summariesRef.current.filter(s => s.id !== id)
      if (activeRunId === id) {
        const remaining = summariesRef.current.filter(s => !s.archived_at)
        const nextId = remaining[0]?.id ?? null
        runsDispatch({ type: 'set_active', id: nextId })
        if (nextId) void activateRun(nextId, companyId)
      }
    } catch (err) {
      reportApiError(err)
    }
  }

  const renameRun = useCallback(
    async (runId: string, title: string) => {
      if (!companyId) throw new Error('Select a workspace before renaming a run.')
      const trimmed = title.trim()
      if (!trimmed) throw new Error('Run title cannot be empty.')

      const tryPatchRun = async () => {
        const cached = runsState.runsById[runId] ?? (activeRun?.id === runId ? activeRun : null)
        let graph = cached?.graph_json
        if (!graph) {
          const loaded = await workflowApi.getRun(companyId, runId)
          graph = loaded.graph_json
        }
        if (!graph) throw new Error('Could not load workflow graph for rename.')
        return workflowApi.patchRun(companyId, runId, graph, trimmed)
      }

      try {
        const r = await workflowApi.patchRunMeta(companyId, runId, { title: trimmed })
        titleGeneratedRef.current.add(runId)
        setFullRunFromServer(r)
      } catch (metaErr) {
        const cached = runsState.runsById[runId] ?? (activeRun?.id === runId ? activeRun : null)
        const canPatchRun =
          cached &&
          cached.run_status !== 'executing' &&
          cached.run_status !== 'coa_running'
        if (canPatchRun) {
          try {
            const r = await tryPatchRun()
            titleGeneratedRef.current.add(runId)
            setFullRunFromServer(r)
            return
          } catch {
            /* fall through to meta error */
          }
        }
        reportApiError(metaErr)
        throw metaErr instanceof Error ? metaErr : new Error(String(metaErr))
      }
    },
    [
      companyId,
      runsState.runsById,
      activeRun,
      setFullRunFromServer,
      reportApiError,
    ],
  )

  const deleteRun = async (id: string) => {
    if (!companyId) return
    try {
      await workflowApi.deleteRun(companyId, id)
      runsDispatch({ type: 'remove_run', id })
      summariesRef.current = summariesRef.current.filter(s => s.id !== id)
      setArchivedSummaries(prev => prev.filter(s => s.id !== id))
      if (activeRunId === id) {
        const remaining = showArchived
          ? summariesRef.current
          : summariesRef.current.filter(s => !s.archived_at)
        const nextId = remaining[0]?.id ?? null
        runsDispatch({ type: 'set_active', id: nextId })
        if (nextId) void activateRun(nextId, companyId)
      }
    } catch (err) {
      reportApiError(err)
    }
  }

  const graph = activeRun?.graph_json ?? { nodes: [], edges: [] }
  const receiptSettings = receiptSettingsFromGraph(graph)
  const showReceiptOptions =
    activeRun != null && ['AR', 'AP'].includes(activeRun.processing_mode.toUpperCase())

  const composerFiles = activeRun ? composerStagingFiles(activeRun.files) : []

  const previewTaskFiles = useMemo(
    () =>
      (activeRun?.files ?? []).map(f => ({
        taskFileId: f.task_file_id,
        originalFilename: f.original_filename,
      })),
    [activeRun?.files],
  )

  const filePreview = useTaskFilePreview(activeRun?.task_id, companyId, previewTaskFiles)

  const hasProcessableFiles =
    composerFiles.some(f => ['pending', 'warning', 'failed'].includes(f.file_status))

  const busy = Boolean(activeRun && busyRunId === activeRun.id)

  const canRun =
    activeRun &&
    composerFiles.length > 0 &&
    apReceiptReady(graph) &&
    !busy &&
    hasProcessableFiles &&
    (activeRun.run_status === 'draft' || activeRun.run_status === 'awaiting_review')

  const vlmActive = Boolean(
    activeRun && (busy || activeRun.run_status === 'executing'),
  )

  const canApprove = Boolean(
    activeRun &&
      !busy &&
      activeRun.run_status !== 'coa_running' &&
      activeRun.run_status !== 'completed' &&
      activeRun.run_status !== 'executing' &&
      activeRun.run_status !== 'queued' &&
      activeRun.run_status !== 'running' &&
      (activeRun.run_status === 'awaiting_review' ||
        (activeRun.run_status === 'draft' && hasOcrDataOnRun(activeRun))),
  )
  const paletteNodes = useMemo(
    () => nodeCatalog,
    [nodeCatalog],
  )
  const paletteTemplates = useMemo(
    () =>
      sortPaletteTemplates(
        templates.filter(t => t.processing_mode === activeRun?.processing_mode),
      ),
    [templates, activeRun?.processing_mode],
  )
  const matchedWorkflowTemplate = useMemo(
    () => templateMatchingGraph(paletteTemplates, graph),
    [paletteTemplates, graph],
  )
  const selectedPaletteTemplate = useMemo(
    () => resolvePaletteTemplateSelection(paletteTemplates, selectedPaletteTemplateId, graph),
    [paletteTemplates, selectedPaletteTemplateId, graph],
  )
  const activeWorkflowLabel = matchedWorkflowTemplate?.name ?? 'Custom workflow'

  useEffect(() => {
    if (!activeRun || paletteTemplates.length === 0) return
    if (syncedPaletteRunRef.current !== activeRun.id) {
      syncedPaletteRunRef.current = activeRun.id
      const resolved = resolvePaletteTemplateSelection(paletteTemplates, '', graph)
      setSelectedPaletteTemplateId(resolved?.id ?? '')
      return
    }
    const selectedStillValid = paletteTemplates.some(t => t.id === selectedPaletteTemplateId)
    if (!selectedStillValid) {
      const resolved = resolvePaletteTemplateSelection(paletteTemplates, '', graph)
      setSelectedPaletteTemplateId(resolved?.id ?? '')
    }
  }, [activeRun?.id, paletteTemplates, graph, selectedPaletteTemplateId])

  const selectedGraphNode =
    selectedNodeIds.length === 1 ? graph.nodes.find(node => node.id === selectedNodeIds[0]) : undefined
  const selectedCatalogEntry = selectedGraphNode
    ? nodeCatalog.find(entry => entry.type === selectedGraphNode.type)
    : undefined

  const showWorkflowErrorBanner =
    latchedWorkflowError !== null &&
    activeRun?.id === latchedWorkflowError.runId &&
    dismissedWorkflowErrorKey !== latchedWorkflowError.key

  const showWorkflowWarningsBanner =
    latchedWorkflowWarnings !== null &&
    activeRun?.id === latchedWorkflowWarnings.runId &&
    dismissedWorkflowWarningsKey !== latchedWorkflowWarnings.key

  const workflowPanel = activeRun ? (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-gray-200 p-2 dark:border-gray-800">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Node palette</div>
            <div className="text-xs text-gray-500">Add workflow nodes, then connect them on the canvas.</div>
          </div>
          <button
            type="button"
            className="btn-ghost text-xs"
            onClick={() => companyId && loadNodeCatalog(companyId, activeRun.processing_mode)}
          >
            Refresh
          </button>
          <button type="button" className="btn-ghost text-xs" onClick={autoLayoutGraph}>
            Auto layout
          </button>
          <button
            type="button"
            className="btn-ghost text-xs"
            disabled={graph.nodes.length === 0}
            onClick={() => removeGraphNodes(graph.nodes.map(node => node.id))}
          >
            Delete all
          </button>
        </div>
        <div className="mb-3 rounded-lg border border-gray-200 bg-white p-2 dark:border-gray-800 dark:bg-gray-900">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Workflow templates</div>
          <div className="flex flex-wrap gap-2">
            <select
              className="ow-input min-w-0 flex-1 text-xs"
              value={selectedPaletteTemplate?.id ?? ''}
              disabled={paletteTemplates.length === 0}
              onChange={event => {
                const templateId = event.target.value
                setSelectedPaletteTemplateId(templateId)
                const template = paletteTemplates.find(t => t.id === templateId)
                if (template) void deployTemplateToRun(template)
              }}
            >
              {paletteTemplates.length === 0 ? (
                <option value="">No templates for this mode</option>
              ) : (
                paletteTemplates.map(template => (
                  <option key={template.id} value={template.id}>
                    {template.name}
                    {template.is_default ? ' (Default)' : ''}
                  </option>
                ))
              )}
            </select>
          </div>
        </div>
        <div className="max-h-40 overflow-y-auto pr-1">
          <div className="flex flex-wrap gap-2">
            {paletteNodes.length === 0 ? (
              <span className="text-xs text-gray-500">No workflow nodes available.</span>
            ) : (
              paletteNodes.map(entry => (
                <button
                  key={entry.type}
                  type="button"
                  className="rounded-lg border border-gray-200 px-2 py-1 text-xs font-medium hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800"
                  title={entry.description || entry.label}
                  onClick={() => addCatalogNode(entry)}
                >
                  {entry.label}
                </button>
              ))
            )}
          </div>
        </div>
      </div>
      <NodeSettingsPanel
        node={selectedGraphNode}
        selectedCount={selectedNodeIds.length}
        catalogEntry={selectedCatalogEntry}
        onPatch={patchGraphNode}
        onDelete={nodeId => removeGraphNodes(selectedNodeIds.length > 1 ? selectedNodeIds : [nodeId])}
        onOpenSkill={skillKey => {
          if (companyId) loadWorkflowSkills(companyId, activeRun.processing_mode)
          setShowSkills(true)
          if (skillKey) {
            setApiError(`Open workflow skills and select "${skillKey.replaceAll('_', ' ')}".`)
          }
        }}
      />
      <div className="min-h-0 flex-1">
        <WorkflowCanvas
          graph={graph}
          nodes={nodes.map(node => ({ ...node, selected: selectedNodeIds.includes(node.id) }))}
          theme={theme}
          layoutKey={activeRun.id}
          onGraphChange={g => persistGraphChange(activeRun.id, g)}
          onNodesUpdate={setNodes}
          onNodeClick={handleCanvasNodeClick}
        />
      </div>
    </div>
  ) : null

  const updateReceiptInGraph = (patch: { receiptSignal?: ApVlmReceiptSignal; tablePreset?: ApVlmTablePreset }) => {
    const receiptNode = activeRun?.graph_json.nodes.find(n => n.type === 'ReceiptStyle')
    if (!receiptNode) return
    patchGraphNode(receiptNode.id, patch)
  }

  return (
    <div className="node-workspace h-[100dvh] w-full">
      {showNotice && (
        <CutoverNotice
          onDismiss={() => {
            dismissCutoverNotice()
            setShowNotice(false)
          }}
        />
      )}

      {apiError ? (
        <div className="fixed left-0 right-0 top-0 z-50 flex items-center justify-between gap-2 bg-red-600 px-4 py-2 text-sm text-white" role="alert">
          <span className="min-w-0 flex-1">{apiError}</span>
          <button
            type="button"
            className="btn-ghost shrink-0 px-2 text-lg leading-none text-white hover:bg-red-700"
            aria-label="Dismiss error"
            onClick={clearApiError}
          >
            ×
          </button>
        </div>
      ) : null}

      {showWorkflowErrorBanner ? (
        <div
          className="fixed left-0 right-0 z-40 flex items-center justify-between gap-2 bg-amber-600 px-4 py-2 text-sm text-white"
          style={{ top: apiError ? '2.5rem' : 0 }}
          role="alert"
        >
          <span className="min-w-0 flex-1">{latchedWorkflowError?.message}</span>
          <button
            type="button"
            className="btn-ghost shrink-0 px-2 text-lg leading-none text-white hover:bg-amber-700"
            aria-label="Dismiss workflow error"
            onClick={() => {
              if (latchedWorkflowError) setDismissedWorkflowErrorKey(latchedWorkflowError.key)
              setLatchedWorkflowError(null)
            }}
          >
            ×
          </button>
        </div>
      ) : null}

      {showWorkflowWarningsBanner ? (
        <div
          className="fixed left-0 right-0 z-40 flex items-center justify-between gap-2 bg-amber-100 px-4 py-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-100"
          style={{
            top: apiError ? '2.5rem' : showWorkflowErrorBanner ? '2.5rem' : 0,
          }}
          role="status"
        >
          <span className="min-w-0 flex-1">{latchedWorkflowWarnings?.messages.join(' · ')}</span>
          <button
            type="button"
            className="btn-ghost shrink-0 px-2 text-lg leading-none text-amber-900 hover:bg-amber-200 dark:text-amber-100 dark:hover:bg-amber-900"
            aria-label="Dismiss workflow warnings"
            onClick={() => {
              if (latchedWorkflowWarnings) setDismissedWorkflowWarningsKey(latchedWorkflowWarnings.key)
              setLatchedWorkflowWarnings(null)
            }}
          >
            ×
          </button>
        </div>
      ) : null}

      <OpenWebUIShell
        controlsOpen={controlsOpen}
        sidebarOpen={sidebarOpen}
        isMobile={isMobile}
        sidebarWidth={sidebarWidth}
        controlsWidth={controlsWidth}
        onSidebarWidthChange={setSidebarWidth}
        onControlsWidthChange={setControlsWidth}
        onSidebarBackdropClick={() => setSidebarOpen(false)}
        sidebar={
          <RunSidebar
            runs={scopedSummaries}
            folders={folders}
            activeRunId={activeRunId}
            companies={companies}
            activeCompanyId={activeCompany?.id}
            companyName={activeCompany?.name}
            userLabel={user?.email}
            showArchived={showArchived}
            mobile={isMobile}
            onSwitchCompany={switchCompany}
            onCreateWorkspace={handleCreateWorkspace}
            onDeleteWorkspace={handleDeleteWorkspace}
            onToggleArchived={() => setShowArchived(v => !v)}
            onNewRun={() => setShowNewMode(true)}
            onSelectRun={id => {
              selectRun(id)
              if (isMobile) setSidebarOpen(false)
            }}
            onArchiveRun={id => void archiveRun(id)}
            onRenameRun={renameRun}
            onDeleteRun={(id, title) =>
              setDeleteRunTarget({ id, title: title || 'Untitled' })
            }
            onMoveRunToFolder={(runId, folderId) => {
              if (!companyId) return
              void workflowApi
                .patchRunMeta(companyId, runId, folderId ? { folder_id: folderId } : { clear_folder: true })
                .then(r => {
                  setFullRunFromServer(r)
                  void loadRuns(companyId, showArchived)
                })
                .catch(reportApiError)
            }}
            onCreateFolder={name => {
              if (!companyId) return
              void workflowApi.createFolder(companyId, name).then(() => loadFolders(companyId)).catch(reportApiError)
            }}
            onRenameFolder={(id, name) => {
              if (!companyId) return
              void workflowApi
                .patchFolder(companyId, id, { name })
                .then(() => loadFolders(companyId))
                .catch(reportApiError)
            }}
            onDeleteFolder={id => {
              if (!companyId) return
              void workflowApi.deleteFolder(companyId, id).then(() => loadFolders(companyId)).catch(reportApiError)
            }}
            onReorderFolders={orderedIds => {
              if (!companyId) return
              void Promise.all(
                orderedIds.map((id, index) => workflowApi.patchFolder(companyId, id, { sort_order: index })),
              )
                .then(() => loadFolders(companyId))
                .catch(reportApiError)
            }}
            onLogout={() => {
              void persistBatchTablesOnUnload().finally(() => void logout())
            }}
          />
        }
        navbar={
          <RunNavbar
            title={activeRun?.title || 'Select or create a run'}
            mode={activeRun?.processing_mode}
            runStatus={activeRun?.run_status}
            sidebarOpen={sidebarOpen}
            controlsOpen={controlsOpen}
            themeLabel={theme === 'light' ? 'Light' : 'Dark'}
            onToggleSidebar={() => setSidebarOpen(v => !v)}
            onToggleControls={toggleControls}
            onOpenManager={() => {
              if (companyId) loadTemplates(companyId)
              setShowManager(true)
            }}
            onOpenSkills={() => {
              if (companyId) loadWorkflowSkills(companyId, activeRun?.processing_mode)
              setShowSkills(true)
            }}
            onThemeToggle={() => setTheme(t => (t === 'light' ? 'dark' : 'light'))}
            onOpenSearch={() => setShowSearch(true)}
            onExportAudit={activeRun ? () => void handleExportAuditJson() : undefined}
            onDeleteRun={
              activeRun
                ? () => setDeleteRunTarget({ id: activeRun.id, title: activeRun.title || 'Untitled' })
                : undefined
            }
          />
        }
        timeline={
          <RunTimeline
            run={activeRun}
            suggestedMode={activeRun?.processing_mode ?? 'AR'}
            batchTablePayloads={batchTablePayloads}
            expandAllTablesNonce={expandAllTablesNonce}
            onNewRun={() => setShowNewMode(true)}
            onBatchTableChange={(batchId, payload) => {
              if (!activeRun) return
              setBatchTablePayloads(prev => {
                const next = { ...prev, [batchId]: payload }
                syncNodes(activeRun, combinedTablePayload(activeRun, next), true)
                return next
              })
              persistBatchTableChange(activeRun, batchId, payload)
            }}
            onMoveFileToBatch={handleMoveFileToBatch}
            onApprove={() => activeRun && void handleApprove(activeRun)}
            onSkipCoa={() => {
              if (!activeRun) return
              void handleResume(activeRun, combinedTablePayload(activeRun), true)
            }}
            onRetryFile={taskFileId => {
              setReVlmInitialFileIds([taskFileId])
              setShowReVlm(true)
            }}
            onPreviewFile={taskFileId => void filePreview.openPreview(taskFileId)}
            onForceProcess={taskFileId => {
              if (!companyId || !activeRun) return
              void workflowApi
                .forceProcess(companyId, activeRun.id, taskFileId)
                .then(r => {
                  setFullRunFromServer(r)
                  syncNodes(r, undefined, true)
                })
                .catch(reportApiError)
            }}
            coaBusy={coaBusy}
            canApprove={canApprove}
          />
        }
        composer={
          activeRun ? (
            <RunComposer
              mode={activeRun.processing_mode}
              files={composerFiles}
              workflowLabel={activeWorkflowLabel}
              reVlmFileCount={activeRun.files.length}
              receiptSignal={receiptSettings.receiptSignal}
              tablePreset={receiptSettings.tablePreset}
              showReceiptOptions={showReceiptOptions}
              canRun={Boolean(canRun)}
              vlmActive={vlmActive}
              busy={busy}
              onAttach={() => fileInputRef.current?.click()}
              onDropFiles={files => void uploadFiles(Array.from(files))}
              onReceiptChange={signal => updateReceiptInGraph({ receiptSignal: signal })}
              onTablePresetChange={preset => updateReceiptInGraph({ tablePreset: preset })}
              onRun={() => void handleRun()}
              onStop={() => void handleStop()}
              onRemoveFile={taskFileId => void handleRemoveFile(taskFileId)}
              onPreviewFile={taskFileId => void filePreview.openPreview(taskFileId)}
              onReVlm={() => {
                setReVlmInitialFileIds([])
                setShowReVlm(true)
              }}
            />
          ) : (
            <p className="text-sm text-gray-500">Create a run from the sidebar to attach files.</p>
          )
        }
        controls={
          activeRun ? (
            <ControlsPane
              tab={controlsTab}
              onTabChange={setControlsTab}
              workflowPanel={workflowPanel}
              logPanel={<ControlsLogPanel lines={activeRun.console_log_json ?? []} />}
              filesPanel={
                <ControlsFilesPanel
                  files={activeRun.files}
                  onPreviewFile={taskFileId => void filePreview.openPreview(taskFileId)}
                />
              }
            />
          ) : null
        }
        mobileControlsSheet={
          activeRun && isMobile ? (
            <ControlsBottomSheet
              open={controlsOpen}
              onClose={() => setControlsOpen(false)}
              tab={controlsTab}
              onTabChange={setControlsTab}
              workflowPanel={workflowPanel}
              logPanel={<ControlsLogPanel lines={activeRun.console_log_json ?? []} />}
              filesPanel={
                <ControlsFilesPanel
                  files={activeRun.files}
                  onPreviewFile={taskFileId => void filePreview.openPreview(taskFileId)}
                />
              }
            />
          ) : null
        }
      />

      <WorkflowSearchModal
        open={showSearch}
        onClose={() => setShowSearch(false)}
        activeRuns={scopedSummaries.filter(s => !s.archived_at)}
        archivedRuns={archivedSummaries}
        folders={folders}
        onSelectRun={id => {
          selectRun(id)
          if (isMobile) setSidebarOpen(false)
        }}
        onNewRun={() => setShowNewMode(true)}
        onOpenTemplates={() => {
          if (companyId) loadTemplates(companyId)
          setShowManager(true)
        }}
        onToggleWorkflow={() => {
          toggleControls()
        }}
      />

      <input ref={fileInputRef} type="file" multiple hidden onChange={e => void onFileInput(e)} />

      {needsCompanyPick && companies.length > 1 && (
        <CompanyPickerModal companies={companies} onSelect={switchCompany} />
      )}

      <ConfirmDialog
        open={deleteRunTarget != null}
        title="Delete run?"
        message={
          deleteRunTarget
            ? `Delete "${deleteRunTarget.title}"? This permanently removes the run, uploaded files, and review data. This cannot be undone.`
            : 'Delete this run?'
        }
        confirmLabel="Delete"
        destructive
        onCancel={() => setDeleteRunTarget(null)}
        onConfirm={() => {
          if (deleteRunTarget) void deleteRun(deleteRunTarget.id)
          setDeleteRunTarget(null)
        }}
      />

      {incompleteWorkflowItems.length > 0 ? (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4">
          <div className="ow-card w-full max-w-md p-6" role="dialog" aria-modal="true">
            <h2 className="mb-2 text-lg font-semibold text-gray-900 dark:text-gray-100">Workflow is incomplete</h2>
            <p className="mb-4 text-sm text-gray-600 dark:text-gray-400">
              The workflow can still be edited, but it cannot run until these items are restored.
            </p>
            <ul className="mb-6 space-y-2 text-sm text-gray-700 dark:text-gray-300">
              {incompleteWorkflowItems.map(item => (
                <li key={item} className="flex items-center gap-2">
                  <span className="inline-flex h-4 w-4 items-center justify-center rounded border border-gray-300 text-[10px] dark:border-gray-700">
                    !
                  </span>
                  {item}
                </li>
              ))}
            </ul>
            <div className="flex justify-end">
              <button type="button" className="btn-primary" onClick={() => setIncompleteWorkflowItems([])}>
                Continue editing
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {showNewMode && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="ow-card w-full max-w-md p-6">
            <h3 className="mb-2 text-lg font-semibold">New workflow run</h3>
            <p className="mb-4 text-sm text-gray-500">Select mode. Each run uses one mode for its batch.</p>
            <div className="flex flex-wrap gap-2">
              {MODES.map(m => (
                <button
                  key={m}
                  type="button"
                  className="btn-primary"
                  disabled={!companyId}
                  onClick={() => {
                    const defaultTemplate = templates.find(
                      t => t.processing_mode === m && t.is_default,
                    )
                    void createRun(m, defaultTemplate?.id)
                  }}
                >
                  {processingModeLabel(m)}
                </button>
              ))}
            </div>
            <button type="button" className="btn-ghost mt-4" onClick={() => setShowNewMode(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {showManager && (
        <TemplatesManagerModal
          templates={templates}
          activeMode={activeRun?.processing_mode ?? 'AR'}
          onClose={() => setShowManager(false)}
          onSaveCurrent={(name, asDefault) => {
            if (!companyId || !activeRun) return
            void workflowApi
              .createTemplate(companyId, name, activeRun.processing_mode, activeRun.graph_json, asDefault)
              .then(() => workflowApi.listTemplates(companyId))
              .then(setTemplates)
              .catch(reportApiError)
          }}
          onDelete={id => {
            if (!companyId) return
            void workflowApi
              .deleteTemplate(companyId, id)
              .then(() => workflowApi.listTemplates(companyId))
              .then(setTemplates)
              .catch(reportApiError)
          }}
          onSetDefault={id => {
            if (!companyId) return
            void workflowApi
              .patchTemplate(companyId, id, { is_default: true })
              .then(() => workflowApi.listTemplates(companyId))
              .then(setTemplates)
              .catch(reportApiError)
          }}
        />
      )}

      <ConfirmDialog
        open={deployTemplateTarget != null}
        title="Deploy template?"
        message={
          deployTemplateTarget
            ? `Deploy "${deployTemplateTarget.name}" to the current workflow? This replaces the canvas nodes and connections, but keeps uploaded files and run history.`
            : 'Deploy this template to the current workflow?'
        }
        confirmLabel="Deploy"
        onCancel={() => setDeployTemplateTarget(null)}
        onConfirm={() => {
          if (deployTemplateTarget) void deployTemplateToRun(deployTemplateTarget)
        }}
      />

      <WorkflowSkillsModal
        open={showSkills}
        mode={activeRun?.processing_mode ?? 'AR'}
        skills={workflowSkills}
        busy={skillsBusy}
        onClose={() => setShowSkills(false)}
        onSave={handleSaveWorkflowSkill}
        onReset={handleResetWorkflowSkill}
        onRollback={handleRollbackWorkflowSkill}
      />

      {showReVlm && activeRun ? (
        <ReVlmModal
          files={activeRun.files}
          initialSelectedFileIds={reVlmInitialFileIds}
          busy={busy}
          workflowContext={{
            graph: activeRun.graph_json,
            templates,
            processingMode: activeRun.processing_mode,
          }}
          onConfirm={payload => void handleReVlm(payload)}
          onCancel={() => {
            setShowReVlm(false)
            setReVlmInitialFileIds([])
          }}
        />
      ) : null}

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
