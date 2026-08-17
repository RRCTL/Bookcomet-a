import type { WorkflowRunFile } from './workflowApi'

export type WorkflowFileBatch = {
  uploadBatchId: string
  uploadedAt: string
  files: WorkflowRunFile[]
}

const STAGING_STATUSES = new Set(['pending', 'warning', 'failed'])

export function isComposerStagingFile(file: WorkflowRunFile): boolean {
  return !file.batch_committed_at && STAGING_STATUSES.has(file.file_status)
}

export function composerStagingFiles(files: WorkflowRunFile[]): WorkflowRunFile[] {
  return files.filter(isComposerStagingFile)
}

/** Files/VLM canvas queue: staging uploads + active Re-VLM/retry + in-flight running. */
export function workflowQueueFiles(
  files: WorkflowRunFile[],
  retryTaskFileIds?: Iterable<string>,
): WorkflowRunFile[] {
  const retryIds = retryTaskFileIds ? new Set(retryTaskFileIds) : new Set<string>()
  const byId = new Map<string, WorkflowRunFile>()
  for (const f of composerStagingFiles(files)) {
    byId.set(f.task_file_id, f)
  }
  for (const f of files) {
    if (f.file_status === 'running') {
      byId.set(f.task_file_id, f)
    } else if (retryIds.has(f.task_file_id)) {
      byId.set(f.task_file_id, f)
    }
  }
  return Array.from(byId.values())
}

function batchKey(file: WorkflowRunFile): string {
  return file.upload_batch_id ?? file.task_file_id
}

function batchUploadedAt(files: WorkflowRunFile[]): string {
  let earliest = ''
  for (const f of files) {
    const ts = f.uploaded_at ?? f.batch_committed_at ?? ''
    if (ts && (!earliest || ts < earliest)) earliest = ts
  }
  return earliest
}

export function committedTimelineBatches(files: WorkflowRunFile[]): WorkflowFileBatch[] {
  const byBatch = new Map<string, WorkflowRunFile[]>()
  for (const f of files) {
    if (!f.batch_committed_at) continue
    const key = batchKey(f)
    const list = byBatch.get(key) ?? []
    list.push(f)
    byBatch.set(key, list)
  }
  return Array.from(byBatch.entries())
    .map(([uploadBatchId, batchFiles]) => ({
      uploadBatchId,
      uploadedAt: batchUploadedAt(batchFiles),
      files: batchFiles,
    }))
    .sort((a, b) => a.uploadedAt.localeCompare(b.uploadedAt) || a.uploadBatchId.localeCompare(b.uploadBatchId))
}

export function formatBatchUploadedAt(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}
