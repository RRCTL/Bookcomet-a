import { apiFetch, type ApiFetchInit } from '../../services/api'
import { RE_VLM_REASON_CHIPS } from './reVlmReasonChips'

export type WorkflowRunFile = {
  id: string
  task_file_id: string
  file_status: string
  gate_result?: string | null
  error_text?: string | null
  original_filename?: string | null
  page_count?: number | null
  upload_batch_id?: string | null
  uploaded_at?: string | null
  batch_committed_at?: string | null
  batch_table_preset?: string | null
  batch_receipt_signal?: string | null
  result_summary_json?: Record<string, unknown> | null
}

export type WorkflowRun = {
  id: string
  task_id: string
  company_id: string
  processing_mode: string
  title: string
  run_status: string
  graph_json: WorkflowGraph
  node_states_json?: Record<string, unknown> | null
  console_log_json?: Array<{ ts: string; level: string; message: string }>
  snapshot_message_id?: string | null
  folder_id?: string | null
  archived_at?: string | null
  processing_removed_at?: string | null
  files: WorkflowRunFile[]
  created_at: string
  updated_at: string
}

/** Slice B: scanned BANK file stopped for explicit bank_override (never from filename). */
export function fileNeedsBankSelection(file: WorkflowRunFile): boolean {
  const err = (file.error_text || '').toLowerCase()
  if (err.includes('bank_selection_required')) return true
  const summary = file.result_summary_json
  if (!summary || typeof summary !== 'object') return false
  return String((summary as { parse_status?: unknown }).parse_status || '') === 'bank_selection_required'
}

export function taskFileIdsNeedingBankSelection(run: WorkflowRun): string[] {
  return (run.files ?? []).filter(fileNeedsBankSelection).map(f => f.task_file_id)
}

export type WorkflowGraph = {
  schemaVersion?: number
  nodes: Array<{
    id: string
    type: string
    position: { x: number; y: number }
    data: Record<string, unknown>
  }>
  edges: Array<{
    id: string
    source: string
    target: string
    sourceHandle?: string
    targetHandle?: string
  }>
  processingMode?: string
  graphV1Backup?: WorkflowGraph
  /** Slice B: explicit bank for BANK mode first VLM (never from filename). */
  bank_override?: string | null
}

export type WorkflowTemplate = {
  id: string
  name: string
  processing_mode: string
  is_default: boolean
  graph_json: WorkflowGraph
}

export type WorkflowNodeCatalogEntry = {
  type: string
  label: string
  category: string
  description?: string
  inputs: Record<string, string>
  outputs: Record<string, string>
  modes: string[]
  params: Record<string, Record<string, unknown>>
  defaults: Record<string, unknown>
  handlerKey: string
  skillAttachable: boolean
  protected: boolean
}

export type WorkflowSkill = {
  id: string
  company_id: string
  mode: string
  skill_key: string
  structured_json: Record<string, string>
  generated_markdown: string
  version: number
  updated_at?: string | null
  previous_versions: Array<{ version: number; saved_at?: string | null }>
}

export type EnvSetting = {
  key: string
  value: string
  is_secret?: boolean
}

export type EnvSettingsResponse = {
  env_path: string
  restart_required: boolean
  settings: EnvSetting[]
}

export type WorkflowRunSummaryBatch = {
  upload_batch_id: string
  status: string
  uploaded_at?: string
}

export type WorkflowRunSummaryFileStatus = {
  task_file_id: string
  file_status: string
}

/** Tab list metadata only (GET /runs). Full graph loaded via getRun. */
export type WorkflowRunSummary = {
  id: string
  task_id: string
  company_id: string
  processing_mode: string
  title: string
  run_status: string
  file_count: number
  batches: WorkflowRunSummaryBatch[]
  file_statuses?: WorkflowRunSummaryFileStatus[]
  folder_id?: string | null
  archived_at?: string | null
  processing_removed_at?: string | null
  created_at: string
  updated_at: string
}

export type WorkflowFolder = {
  id: string
  name: string
  parent_id?: string | null
  sort_order?: number
  /** Module/processing-mode this folder is scoped to (null = untyped/legacy). */
  mode?: string | null
}

export function workflowApiError(res: Response, body: string): Error {
  const trimmed = body.trim()
  if (res.status === 401) {
    return new Error('Session expired. Please log in again.')
  }
  if (
    res.status === 502 ||
    res.status === 503 ||
    trimmed.startsWith('<!DOCTYPE') ||
    trimmed.startsWith('<html')
  ) {
    return new Error(
      'Backend unavailable (502). On the tunnel machine, run `python run.py` in backend (port 8000) while Vite is on 5173.',
    )
  }
  try {
    const parsed = JSON.parse(trimmed) as { detail?: unknown }
    if (typeof parsed.detail === 'string') {
      if (res.status === 404 && parsed.detail === 'Not Found') {
        return new Error(
          'Move API not available. Restart or redeploy the backend with the latest workflow routes.',
        )
      }
      return new Error(parsed.detail)
    }
    if (parsed.detail != null) return new Error(JSON.stringify(parsed.detail))
  } catch {
    /* not JSON */
  }
  if (trimmed.length > 240) return new Error(`${res.status} ${res.statusText}`)
  return new Error(trimmed || `${res.status} ${res.statusText}`)
}

async function readErrorBody(res: Response): Promise<string> {
  return res.text().catch(() => res.statusText)
}

async function wfJson<T>(companyId: string, path: string, init: ApiFetchInit = {}): Promise<T> {
  const res = await apiFetch(path, { ...init, companyId })
  if (!res.ok) {
    throw workflowApiError(res, await readErrorBody(res))
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

/** SEC-CODE-013: WebSocket URL must not include the access token. */
export function workflowRunWsUrl(runId: string): string {
  const proto = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = typeof window !== 'undefined' ? window.location.host : 'localhost:5173'
  const base =
    import.meta.env.DEV ||
    (typeof window !== 'undefined' && window.location.protocol === 'https:')
      ? '/api-proxy'
      : (import.meta.env.VITE_API_URL as string | undefined)?.trim() || ''
  const path = `${base}/api/workflows/runs/${runId}/ws`
  return base.startsWith('http')
    ? `${base.replace(/^http/, 'ws')}/api/workflows/runs/${runId}/ws`
    : `${proto}//${host}${path.startsWith('/') ? path : `/${path}`}`
}

export function workflowRunWsAuthMessage(accessToken: string): string {
  return JSON.stringify({ type: 'auth', token: accessToken })
}

export const workflowApi = {
  nodeCatalog: (companyId: string, processingMode?: string) => {
    const query = processingMode ? `?processing_mode=${encodeURIComponent(processingMode)}` : ''
    return wfJson<WorkflowNodeCatalogEntry[]>(companyId, `/api/workflows/node-catalog${query}`)
  },
  listSkills: (companyId: string, mode?: string) => {
    const query = mode ? `?mode=${encodeURIComponent(mode)}` : ''
    return wfJson<WorkflowSkill[]>(companyId, `/api/workflows/skills${query}`)
  },
  updateSkill: (
    companyId: string,
    mode: string,
    skillKey: string,
    structured_json: Record<string, string>,
  ) =>
    wfJson<WorkflowSkill>(companyId, `/api/workflows/skills/${mode}/${skillKey}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ structured_json }),
    }),
  resetSkill: (companyId: string, mode: string, skillKey: string) =>
    wfJson<WorkflowSkill>(companyId, `/api/workflows/skills/${mode}/${skillKey}/reset`, { method: 'POST' }),
  rollbackSkill: (companyId: string, mode: string, skillKey: string, version?: number) =>
    wfJson<WorkflowSkill>(companyId, `/api/workflows/skills/${mode}/${skillKey}/rollback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ version }),
    }),
  getEnvSettings: (companyId: string) => wfJson<EnvSettingsResponse>(companyId, '/settings/env'),
  updateEnvSettings: (
    companyId: string,
    settings: Array<{ key: string; value: string }>,
    delete_keys: string[] = [],
  ) =>
    wfJson<{ success: boolean; message: string; backup_path?: string | null; restart_required: boolean }>(
      companyId,
      '/settings/env',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ settings, delete_keys }),
      },
    ),
  exportAuditJson: (companyId: string, runId: string) =>
    wfJson<Record<string, unknown>>(companyId, `/api/workflows/runs/${runId}/audit-json`),
  clearDebugOutputs: (companyId: string, runId: string) =>
    wfJson<void>(companyId, `/api/workflows/runs/${runId}/debug`, { method: 'DELETE' }),
  listRuns: (companyId: string, archived = false) =>
    wfJson<WorkflowRunSummary[]>(companyId, `/api/workflows/runs?archived=${archived ? 'true' : 'false'}`),
  createRun: (companyId: string, processing_mode: string, template_id?: string) =>
    wfJson<WorkflowRun>(companyId, '/api/workflows/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ processing_mode, template_id }),
    }),
  getRun: (companyId: string, id: string) => wfJson<WorkflowRun>(companyId, `/api/workflows/runs/${id}`),
  getApprovedPackage: (companyId: string, id: string) =>
    wfJson<{
      package_id: string
      storage_path: string
      approved_payload: Record<string, unknown>
      manifest: Record<string, unknown>
    }>(companyId, `/api/workflows/runs/${id}/approved-package`),
  patchRun: (companyId: string, id: string, graph_json: WorkflowGraph, title?: string) =>
    wfJson<WorkflowRun>(companyId, `/api/workflows/runs/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ graph_json, title }),
    }),
  patchRunMeta: (
    companyId: string,
    id: string,
    meta: {
      folder_id?: string | null
      clear_folder?: boolean
      archive?: boolean
      title?: string
      remove_from_processing?: boolean
    },
  ) => {
    const body: Record<string, unknown> = {}
    if (meta.folder_id !== undefined && meta.folder_id !== null) body.folder_id = meta.folder_id
    if (meta.clear_folder) body.clear_folder = true
    if (meta.archive !== undefined) body.archive = meta.archive
    if (meta.title !== undefined) body.title = meta.title
    if (meta.remove_from_processing !== undefined) body.remove_from_processing = meta.remove_from_processing
    return wfJson<WorkflowRun>(companyId, `/api/workflows/runs/${id}/meta`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  },
  deleteRun: (companyId: string, id: string) =>
    wfJson<void>(companyId, `/api/workflows/runs/${id}`, { method: 'DELETE' }),
  uploadFile: async (
    companyId: string,
    runId: string,
    file: File,
    batch?: { uploadBatchId: string; uploadedAt: string },
  ) => {
    const form = new FormData()
    form.append('file', file)
    if (batch?.uploadBatchId) form.append('upload_batch_id', batch.uploadBatchId)
    if (batch?.uploadedAt) form.append('uploaded_at', batch.uploadedAt)
    const res = await apiFetch(`/api/workflows/runs/${runId}/files`, {
      method: 'POST',
      body: form,
      companyId,
    })
    if (!res.ok) throw workflowApiError(res, await readErrorBody(res))
    return res.json()
  },
  execute: (companyId: string, id: string) =>
    wfJson<WorkflowRun>(companyId, `/api/workflows/runs/${id}/execute`, { method: 'POST' }),
  rerun: (companyId: string, id: string) =>
    wfJson<WorkflowRun>(companyId, `/api/workflows/runs/${id}/rerun`, { method: 'POST' }),
  cancel: (companyId: string, id: string) =>
    wfJson<WorkflowRun>(companyId, `/api/workflows/runs/${id}/cancel`, { method: 'POST' }),
  resume: (companyId: string, id: string, approved_payload: Record<string, unknown>, skip_coa = false) =>
    wfJson<WorkflowRun>(companyId, `/api/workflows/runs/${id}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approved_payload, skip_coa }),
    }),
  reVlm: (
    companyId: string,
    id: string,
    task_file_ids: string[],
    options?: {
      force_process?: boolean
      rescan_reasons?: string[]
      rescan_note?: string | null
      expected_receipt_count?: number | null
      bank_override?: string | null
    },
  ) =>
    wfJson<WorkflowRun>(companyId, `/api/workflows/runs/${id}/re-vlm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        task_file_ids,
        force_process: options?.force_process ?? false,
        rescan_reasons: options?.rescan_reasons ?? [],
        rescan_note: options?.rescan_note ?? null,
        expected_receipt_count: options?.expected_receipt_count ?? null,
        bank_override: options?.bank_override ?? null,
      }),
    }),
  forceProcess: (companyId: string, runId: string, taskFileId: string) =>
    wfJson<WorkflowRun>(companyId, `/api/workflows/runs/${runId}/files/${taskFileId}/force-process`, {
      method: 'POST',
    }),
  recoverStuck: (companyId: string, runId: string) =>
    wfJson<WorkflowRun>(companyId, `/api/workflows/runs/${runId}/recover-stuck`, { method: 'POST' }),
  removeRunFile: (companyId: string, runId: string, taskFileId: string) =>
    wfJson<void>(companyId, `/api/workflows/runs/${runId}/files/${taskFileId}`, { method: 'DELETE' }),
  moveRunFileToBatch: (companyId: string, runId: string, taskFileId: string, uploadBatchId: string) =>
    wfJson<WorkflowRun>(companyId, `/api/workflows/runs/${runId}/files/${taskFileId}/batch`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ upload_batch_id: uploadBatchId }),
    }),
  listTemplates: (companyId: string) => wfJson<WorkflowTemplate[]>(companyId, '/api/workflows/templates'),
  createTemplate: (
    companyId: string,
    name: string,
    processing_mode: string,
    graph_json: WorkflowGraph,
    is_default = false,
  ) =>
    wfJson<{ id: string; name: string }>(companyId, '/api/workflows/templates', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, processing_mode, graph_json, is_default }),
    }),
  patchTemplate: (
    companyId: string,
    id: string,
    patch: { name?: string; is_default?: boolean },
  ) =>
    wfJson<{ id: string; name: string; is_default: boolean }>(companyId, `/api/workflows/templates/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }),
  deleteTemplate: (companyId: string, id: string) =>
    wfJson<void>(companyId, `/api/workflows/templates/${id}`, { method: 'DELETE' }),
  listFolders: (companyId: string) => wfJson<WorkflowFolder[]>(companyId, '/api/workflows/folders'),
  createFolder: (companyId: string, name: string) =>
    wfJson<WorkflowFolder>(companyId, '/api/workflows/folders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }),
  patchFolder: (
    companyId: string,
    id: string,
    patch: { name?: string; sort_order?: number },
  ) =>
    wfJson<WorkflowFolder>(companyId, `/api/workflows/folders/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }),
  deleteFolder: (companyId: string, id: string) =>
    wfJson<void>(companyId, `/api/workflows/folders/${id}`, { method: 'DELETE' }),
  defaultGraph: (companyId: string, mode: string) =>
    wfJson<WorkflowGraph>(companyId, `/api/workflows/default-graph/${mode}`),
}

const RUNNING_NODE_STATUSES = new Set(['running', 'executing', 'coa_running'])
const RUNNING_RUN_STATUSES = new Set(['executing', 'coa_running', 'running', 'queued'])

export function runLooksProcessing(run: WorkflowRun): boolean {
  if (RUNNING_RUN_STATUSES.has((run.run_status || '').toLowerCase())) return true
  if (run.files.some(f => f.file_status === 'running')) return true
  const states = run.node_states_json
  if (states && typeof states === 'object') {
    for (const raw of Object.values(states)) {
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) continue
      if (RUNNING_NODE_STATUSES.has(String((raw as Record<string, unknown>).status ?? '').toLowerCase())) {
        return true
      }
    }
  }
  return false
}

/** Ignore stale poll/WS payloads that still say executing after the user confirmed Stop. */
export function shouldIgnoreRunRefreshAfterStop(
  guardedRunId: string | null,
  incoming: WorkflowRun,
): boolean {
  return Boolean(guardedRunId && guardedRunId === incoming.id && runLooksProcessing(incoming))
}

/** Optimistic UI while hard Stop is in flight (matches backend cancel reset). */
export function applyRunStoppedLocally(run: WorkflowRun): WorkflowRun {
  const files = run.files.map(f =>
    f.file_status === 'running' ? { ...f, file_status: 'pending', error_text: null } : f,
  )
  const nodeStates: Record<string, unknown> = { ...(run.node_states_json ?? {}) }
  delete nodeStates.cancel_requested
  for (const [nodeId, raw] of Object.entries(nodeStates)) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) continue
    const entry = raw as Record<string, unknown>
    if (RUNNING_NODE_STATUSES.has(String(entry.status ?? '').toLowerCase())) {
      nodeStates[nodeId] = { ...entry, status: 'cancelled' }
    }
  }
  const hasReviewable = files.some(f => f.file_status === 'ok' || f.file_status === 'warning')
  return {
    ...run,
    run_status: hasReviewable ? 'awaiting_review' : 'draft',
    files,
    node_states_json: nodeStates,
  }
}

const OCR_PRODUCER_NODE_TYPES = new Set(['VLM_API', 'VLMProposer', 'VLMDoubleCheck'])

/** Optimistic UI when Re-VLM starts (matches backend node state reset). */
export function applyRunReVlmLocally(
  run: WorkflowRun,
  taskFileIds: string[],
  options?: {
    rescanReasons?: string[]
    rescanNote?: string
    expectedReceiptCount?: number | null
  },
): WorkflowRun {
  const idSet = new Set(taskFileIds)
  const files = run.files.map(f =>
    idSet.has(f.task_file_id) ? { ...f, file_status: 'running', error_text: null } : f,
  )
  const graphNodes = run.graph_json?.nodes ?? []
  const graphIds = new Set(graphNodes.map(n => n.id))
  const ocrIds = new Set(graphNodes.filter(n => OCR_PRODUCER_NODE_TYPES.has(n.type)).map(n => n.id))
  const prev = run.node_states_json ?? {}
  const nodeStates: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(prev)) {
    if (!graphIds.has(key)) nodeStates[key] = value
  }
  const chipLabels = (options?.rescanReasons ?? [])
    .map(id => RE_VLM_REASON_CHIPS.find(c => c.id === id)?.label)
    .filter((l): l is string => Boolean(l))
  const focus = chipLabels.length > 0 ? chipLabels.join(', ') : undefined
  const note = (options?.rescanNote ?? '').trim() || undefined
  const expected =
    typeof options?.expectedReceiptCount === 'number' && options.expectedReceiptCount >= 2
      ? options.expectedReceiptCount
      : undefined
  const ocrDetail: Record<string, unknown> = { file_count: taskFileIds.length }
  if (focus) ocrDetail.rescan_focus = focus
  if (note) ocrDetail.rescan_note = note
  if (expected != null) ocrDetail.expected_receipt_count = expected
  if (focus) ocrDetail.reason = `Re-VLM: ${focus}`
  else if (note || expected != null) ocrDetail.reason = 'Re-VLM'
  for (const node of graphNodes) {
    if (ocrIds.has(node.id)) {
      nodeStates[node.id] = { status: 'running', detail: ocrDetail }
    } else {
      nodeStates[node.id] = { status: 'pending', detail: null }
    }
  }
  return {
    ...run,
    run_status: 'executing',
    files,
    node_states_json: nodeStates,
  }
}
