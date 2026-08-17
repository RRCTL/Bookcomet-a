import type { ARAPTransaction } from '../../components/ARAPReview'
import type { SpreadsheetRow } from '../../components/EditableSpreadsheet'
import { spreadsheetRowsToArapTransactions } from './buildSpreadsheetFromOcrResult'
import type { ChatTask, Message, QueuedFile } from './types'

export function batchOcrSnapshotMessageId(uploadBatchId: string): string {
  return `ocr-batch-${uploadBatchId}`
}

export function isLocalBatchOcrSnapshotMessage(m: Message): boolean {
  return m.id.startsWith('ocr-batch-')
}

function queueBatchKey(f: QueuedFile): string {
  return f.uploadBatchId ?? f.id
}

export function batchUploadStats(task: ChatTask, uploadBatchId: string): {
  total: number
  completed: number
  failed: number
  processing: number
  pending: number
} {
  const members = task.fileQueue.filter(f => queueBatchKey(f) === uploadBatchId)
  return {
    total: members.length,
    completed: members.filter(f => f.status === 'completed').length,
    failed: members.filter(f => f.status === 'failed').length,
    processing: members.filter(f => f.status === 'processing').length,
    pending: members.filter(f => f.status === 'pending').length,
  }
}

/** Replace rows for one queued file id (row ids are prefixed with file id). */
export function mergeSpreadsheetRowsForFile(
  existing: SpreadsheetRow[],
  incoming: SpreadsheetRow[],
  fileId: string,
): SpreadsheetRow[] {
  const prefix = `${fileId}-`
  const kept = existing.filter(r => {
    const id = String(r.id ?? '')
    return id !== fileId && !id.startsWith(prefix)
  })
  return [...kept, ...incoming]
}

export function buildBatchOcrSnapshotContent(opts: {
  rowCount: number
  completedCount: number
  totalInBatch: number
  failedCount: number
  isFinal: boolean
}): string {
  const { rowCount, completedCount, totalInBatch, failedCount, isFinal } = opts
  if (isFinal) {
    let s = `Batch complete: ${completedCount} file(s) succeeded`
    if (failedCount > 0) s += `, ${failedCount} failed`
    s += `\n${rowCount} record(s) extracted.\n\nEditable summary table — double-click a cell to edit:`
    return s
  }
  return (
    `[In progress] Summary table · ${rowCount} row(s)`
    + ` (${completedCount}/${totalInBatch} files done${failedCount > 0 ? `, ${failedCount} failed` : ''})`
    + `\n\nEditable summary table — double-click a cell to edit:`
  )
}

export type BatchOcrSnapshotExtras = Pick<
  Message,
  'spreadsheetData' | 'arapTransactions' | 'arapFilename' | 'apVlmTablePreset'
>

export function upsertLocalBatchOcrSnapshotInMessages(
  messages: Message[],
  task: ChatTask,
  uploadBatchId: string,
  queuedFileId: string,
  extras: BatchOcrSnapshotExtras,
  processingMode: string,
  isFinal = false,
): Message[] {
  const id = batchOcrSnapshotMessageId(uploadBatchId)
  const stats = batchUploadStats(task, uploadBatchId)
  const idx = messages.findIndex(m => m.id === id)
  const prev = idx >= 0 ? messages[idx] : undefined
  const mergedSheet = mergeSpreadsheetRowsForFile(
    prev?.spreadsheetData ?? [],
    extras.spreadsheetData ?? [],
    queuedFileId,
  )
  if (mergedSheet.length === 0) return messages

  const arapTransactions = spreadsheetRowsToArapTransactions(mergedSheet, processingMode)
  const next: Message = {
    id,
    role: 'assistant',
    content: buildBatchOcrSnapshotContent({
      rowCount: mergedSheet.length,
      completedCount: stats.completed,
      totalInBatch: stats.total,
      failedCount: stats.failed,
      isFinal,
    }),
    contentType: 'ocr_snapshot',
    ocrUploadBatchId: uploadBatchId,
    spreadsheetData: mergedSheet,
    arapTransactions,
    apVlmTablePreset: extras.apVlmTablePreset ?? prev?.apVlmTablePreset,
    arapFilename: stats.total === 1 ? extras.arapFilename : prev?.arapFilename,
  }
  if (idx >= 0) {
    const out = [...messages]
    out[idx] = next
    return out
  }
  return [...messages, next]
}

export function removeLocalBatchOcrSnapshotMessages(
  messages: Message[],
  uploadBatchId: string,
): Message[] {
  const id = batchOcrSnapshotMessageId(uploadBatchId)
  return messages.filter(m => m.id !== id)
}

export function resolveArapTransactionsForMessage(
  m: Message,
  processingMode: string,
): ARAPTransaction[] | undefined {
  if (m.arapTransactions && m.arapTransactions.length > 0) return m.arapTransactions
  if (m.spreadsheetData && m.spreadsheetData.length > 0) {
    return spreadsheetRowsToArapTransactions(m.spreadsheetData, processingMode)
  }
  return undefined
}
