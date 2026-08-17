import type { Message } from './types'

/** Client-only rows not returned by GET /messages — keep during server sync. */
export function shouldKeepLocalOnlyMessage(m: Message): boolean {
  if (m.uploadedFiles && m.uploadedFiles.length > 0) return true
  if (m.progressJob) return true
  if (typeof m.progressPercent === 'number' && m.progressPercent < 100) return true
  if (m.content === '__QUEUE_NOTICE__') return true
  const id = m.id
  if (id.startsWith('progress-') || id.startsWith('remote-bg-') || id.startsWith('remote-bank-')) {
    return true
  }
  if (id.startsWith('ocr-batch-')) return true
  return false
}

/** Server wins persisted fields; local wins client-only upload/progress UI. */
export function mergeMessagePreserveClient(server: Message, local: Message): Message {
  return {
    ...server,
    ...(local.uploadedFiles?.length ? { uploadedFiles: local.uploadedFiles } : {}),
    ...(local.progressJob ? { progressJob: local.progressJob } : {}),
    ...(local.progressPercent !== undefined ? { progressPercent: local.progressPercent } : {}),
    ...(local.progressLabel !== undefined ? { progressLabel: local.progressLabel } : {}),
    ...(local.progressMeta !== undefined ? { progressMeta: local.progressMeta } : {}),
    ...(local.gateCard ? { gateCard: local.gateCard } : {}),
    ...(local.dupConfirmPending !== undefined ? { dupConfirmPending: local.dupConfirmPending } : {}),
    ...(local.dupConfirmId !== undefined ? { dupConfirmId: local.dupConfirmId } : {}),
    ...(local.dupAlertType !== undefined ? { dupAlertType: local.dupAlertType } : {}),
    ...(local.dupFileNames !== undefined ? { dupFileNames: local.dupFileNames } : {}),
    ...(local.csvHint !== undefined ? { csvHint: local.csvHint } : {}),
    ...(local.ocrUploadBatchId !== undefined ? { ocrUploadBatchId: local.ocrUploadBatchId } : {}),
  }
}

/**
 * Merge GET /messages into in-memory task messages without dropping upload bubbles
 * or in-flight progress rows that only exist on the client.
 */
export function mergeTaskMessagesFromServer(prev: Message[], server: Message[]): Message[] {
  const serverById = new Map(server.map(m => [m.id, m]))
  const seenServer = new Set<string>()
  const out: Message[] = []

  for (const local of prev) {
    const srv = serverById.get(local.id)
    if (srv) {
      seenServer.add(local.id)
      out.push(mergeMessagePreserveClient(srv, local))
    } else if (shouldKeepLocalOnlyMessage(local)) {
      out.push(local)
    }
  }

  for (const srv of server) {
    if (!seenServer.has(srv.id)) out.push(srv)
  }

  return out
}
