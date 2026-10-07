/**
 * Pure enablement gates for Processing toolbar actions (Stop / Upload / Run / Re-VLM).
 * Fiction-only helpers — no network, no real document data.
 */

export const RUNNING_RUN_STATUSES = new Set(['executing', 'coa_running', 'running', 'queued'])

const RUNNING_NODE_STATUSES = new Set(['running', 'executing', 'coa_running'])

export const ANOTHER_RUN_PROCESSING_HINT =
  'Another run is processing — you can upload files now; Run unlocks when that finishes or is stopped.'

export type GateRunSummary = {
  id: string
  run_status: string
}

export type GateFile = {
  file_status: string
}

export type GateNodeState = {
  status?: string
}

/** True when a run-level status means VLM / workflow work is in flight. */
export function runStatusIsProcessing(runStatus: string | null | undefined): boolean {
  return RUNNING_RUN_STATUSES.has((runStatus ?? '').toLowerCase())
}

/** Live-output file rows that mean mid-batch work (Processing or Queued while executing). */
export function liveOutputShowsActiveBatch(files: GateFile[], runIsProcessing: boolean): boolean {
  if (!runIsProcessing) return false
  return files.some(f => {
    const s = (f.file_status ?? '').toLowerCase()
    return s === 'running' || s === 'pending' || s === 'queued'
  })
}

export function anyNodeSpinning(nodeStates: Record<string, GateNodeState | unknown> | null | undefined): boolean {
  if (!nodeStates || typeof nodeStates !== 'object') return false
  for (const raw of Object.values(nodeStates)) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) continue
    const status = String((raw as GateNodeState).status ?? '').toLowerCase()
    if (RUNNING_NODE_STATUSES.has(status)) return true
  }
  return false
}

/**
 * Stop is clickable when THIS run is mid-batch: Live output Processing/Queued,
 * or a sidebar VLM/node spinner — never gated by a sibling run or a long HTTP "busy".
 */
export function canStopActiveRun(opts: {
  hasActiveRun: boolean
  runStatus: string
  files: GateFile[]
  nodeStates?: Record<string, GateNodeState | unknown> | null
}): boolean {
  if (!opts.hasActiveRun) return false
  const runProcessing = runStatusIsProcessing(opts.runStatus)
  if (runProcessing) return true
  if (anyNodeSpinning(opts.nodeStates)) return true
  if (opts.files.some(f => (f.file_status ?? '').toLowerCase() === 'running')) return true
  return false
}

/** Another run (not the open one) is Processing / has an in-flight busy HTTP. */
export function anotherRunIsProcessing(
  runs: GateRunSummary[],
  activeRunId: string | null,
  busyRunId: string | null,
): boolean {
  if (busyRunId && busyRunId !== activeRunId) return true
  return runs.some(
    r => r.id !== activeRunId && runStatusIsProcessing(r.run_status),
  )
}

/** Staging PDFs on the open run never waits on someone else's VLM. */
export function canUploadOnActiveRun(opts: {
  hasActiveRun: boolean
  fileCount: number
  /** True only while THIS run's own upload request is in flight. */
  busyOnActiveRun: boolean
}): boolean {
  if (!opts.hasActiveRun) return false
  if (opts.busyOnActiveRun) return false
  if (opts.fileCount > 0) return false
  return true
}

/** Run stays grey on a quiet run while any other run is Processing. */
export function canExecuteOnActiveRun(opts: {
  hasActiveRun: boolean
  fileCount: number
  activeRunProcessing: boolean
  busyOnActiveRun: boolean
  anotherRunProcessing: boolean
}): boolean {
  if (!opts.hasActiveRun) return false
  if (opts.fileCount === 0) return false
  if (opts.activeRunProcessing) return false
  if (opts.busyOnActiveRun) return false
  if (opts.anotherRunProcessing) return false
  return true
}

/** Re-VLM stays grey on a quiet run while any other run is Processing. */
export function canReVlmOnActiveRun(opts: {
  hasActiveRun: boolean
  hasProcessedFiles: boolean
  activeRunProcessing: boolean
  busyOnActiveRun: boolean
  anotherRunProcessing: boolean
  reVlmLocked: boolean
}): boolean {
  if (!opts.hasActiveRun) return false
  if (opts.reVlmLocked) return false
  if (!opts.hasProcessedFiles) return false
  if (opts.activeRunProcessing) return false
  if (opts.busyOnActiveRun) return false
  if (opts.anotherRunProcessing) return false
  return true
}
