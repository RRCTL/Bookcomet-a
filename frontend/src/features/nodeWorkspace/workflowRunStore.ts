import { useCallback, useReducer } from 'react'
import type { WorkflowRun, WorkflowRunFile, WorkflowRunSummary, WorkflowRunSummaryBatch } from './workflowApi'

export type WorkflowRunsState = {
  summaries: WorkflowRunSummary[]
  runsById: Record<string, WorkflowRun>
  activeRunId: string | null
  loadingRunId: string | null
}

type Action =
  | { type: 'reset' }
  | { type: 'set_summaries'; summaries: WorkflowRunSummary[] }
  | { type: 'merge_summaries'; summaries: WorkflowRunSummary[] }
  | { type: 'upsert_summary'; summary: WorkflowRunSummary }
  | { type: 'set_full_run'; run: WorkflowRun }
  | { type: 'set_active'; id: string | null }
  | { type: 'set_loading'; id: string | null }
  | { type: 'patch_run_graph'; runId: string; graph: WorkflowRun['graph_json'] }
  | { type: 'patch_run'; run: WorkflowRun }
  | { type: 'remove_run'; id: string }

function normalizeSummary(summary: WorkflowRunSummary): WorkflowRunSummary {
  return { ...summary, batches: summary.batches ?? [], file_statuses: summary.file_statuses ?? [] }
}

function mergeSummariesFromServer(
  server: WorkflowRunSummary[],
  local: WorkflowRunSummary[],
): WorkflowRunSummary[] {
  const serverIds = new Set(server.map(r => r.id))
  const localOnly = local.filter(r => !serverIds.has(r.id)).map(normalizeSummary)
  return [...localOnly, ...server.map(normalizeSummary)]
}

function summaryBatchStatus(files: WorkflowRunFile[]): string {
  if (files.some(file => file.file_status === 'failed' || file.file_status === 'warning')) return 'failed'
  if (files.length > 0 && files.every(file => file.file_status === 'ok')) return 'ok'
  if (files.some(file => file.file_status === 'running')) return 'running'
  return 'pending'
}

function summaryBatchesFromFiles(files: WorkflowRunFile[]): WorkflowRunSummaryBatch[] {
  const byBatch = new Map<string, WorkflowRunFile[]>()
  for (const file of files) {
    const key = file.upload_batch_id ?? file.task_file_id
    const list = byBatch.get(key) ?? []
    list.push(file)
    byBatch.set(key, list)
  }
  return Array.from(byBatch.entries())
    .map(([uploadBatchId, batchFiles]) => {
      const uploadedAt =
        batchFiles
          .map(file => file.uploaded_at ?? file.batch_committed_at ?? '')
          .filter(Boolean)
          .sort()[0] ?? ''
      return {
        upload_batch_id: uploadBatchId,
        status: summaryBatchStatus(batchFiles),
        uploaded_at: uploadedAt,
      }
    })
    .sort((a, b) => (a.uploaded_at ?? '').localeCompare(b.uploaded_at ?? '') || a.upload_batch_id.localeCompare(b.upload_batch_id))
}

function summaryFromRun(run: WorkflowRun): WorkflowRunSummary {
  return {
    id: run.id,
    task_id: run.task_id,
    company_id: run.company_id,
    processing_mode: run.processing_mode,
    title: run.title,
    run_status: run.run_status,
    file_count: run.files?.length ?? 0,
    batches: summaryBatchesFromFiles(run.files ?? []),
    file_statuses: (run.files ?? []).map(f => ({
      task_file_id: f.task_file_id,
      file_status: f.file_status,
    })),
    folder_id: run.folder_id,
    archived_at: run.archived_at,
    created_at: run.created_at,
    updated_at: run.updated_at,
  }
}

function reducer(state: WorkflowRunsState, action: Action): WorkflowRunsState {
  switch (action.type) {
    case 'reset':
      return { summaries: [], runsById: {}, activeRunId: null, loadingRunId: null }
    case 'set_summaries':
      return { ...state, summaries: action.summaries.map(normalizeSummary) }
    case 'merge_summaries':
      return {
        ...state,
        summaries: mergeSummariesFromServer(action.summaries, state.summaries),
      }
    case 'upsert_summary':
      return {
        ...state,
        summaries: [
          normalizeSummary(action.summary),
          ...state.summaries.filter(s => s.id !== action.summary.id),
        ],
      }
    case 'set_full_run': {
      const summary = summaryFromRun(action.run)
      return {
        ...state,
        runsById: { ...state.runsById, [action.run.id]: action.run },
        summaries: [
          summary,
          ...state.summaries.filter(s => s.id !== action.run.id),
        ],
      }
    }
    case 'set_active':
      return { ...state, activeRunId: action.id }
    case 'set_loading':
      return { ...state, loadingRunId: action.id }
    case 'remove_run': {
      const { [action.id]: _removed, ...runsById } = state.runsById
      return {
        ...state,
        runsById,
        summaries: state.summaries.filter(s => s.id !== action.id),
        activeRunId: state.activeRunId === action.id ? null : state.activeRunId,
      }
    }
    case 'patch_run_graph': {
      const existing = state.runsById[action.runId]
      if (!existing) return state
      const run = { ...existing, graph_json: action.graph }
      const summary = summaryFromRun(run)
      return {
        ...state,
        runsById: { ...state.runsById, [action.runId]: run },
        summaries: [summary, ...state.summaries.filter(s => s.id !== action.runId)],
      }
    }
    case 'patch_run':
      return reducer(state, { type: 'set_full_run', run: action.run })
    default:
      return state
  }
}

const initialState: WorkflowRunsState = {
  summaries: [],
  runsById: {},
  activeRunId: null,
  loadingRunId: null,
}

export function useWorkflowRuns() {
  const [state, dispatch] = useReducer(reducer, initialState)

  const activeRun = state.activeRunId ? state.runsById[state.activeRunId] ?? null : null

  const setFullRun = useCallback((run: WorkflowRun) => {
    dispatch({ type: 'set_full_run', run })
  }, [])

  return { state, dispatch, activeRun, setFullRun, summaryFromRun }
}

export { mergeSummariesFromServer, summaryFromRun }
