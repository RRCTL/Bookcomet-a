import { BG_JOB_STORAGE_PREFIX } from '../../services/api'
import type { ProcessingMode } from '../../components/ModeSelector'
import type { ChatTask, Message, QueuedFile } from './types'
import { normalizeClientProcessingMode } from './taskMappers'

/** True while OCR/VLM is likely in-flight or upload queue hasn't finished — keep messages for localStorage hydrate. */
export function taskConversationPersistWhileBusy(t: ChatTask): boolean {
  const st = t.status
  if (st === 'queued' || st === 'processing') return true
  const fq = t.fileQueue ?? []
  if (fq.some(fileIsBusyQueue)) return true
  const msgs = t.messages ?? []
  if (msgs.some(m => Boolean(m.progressJob))) return true
  if (
    msgs.some(m =>
      (m.uploadedFiles ?? []).some(
        uf => uf.status !== 'completed' && uf.status !== 'failed' && uf.status !== 'cancelled',
      ),
    )
  ) {
    return true
  }
  return false
}

function fileIsBusyQueue(f: QueuedFile): boolean {
  return f.status === 'pending' || f.status === 'processing'
}

/** Preserve display name before JSON.stringify drops `File` via replacer (avoids `undefined.name` on reload). */
function messagesWithPersistableUploadedFiles(messages: Message[]): Message[] {
  return messages.map(m => {
    if (!m.uploadedFiles?.length) return m
    return {
      ...m,
      uploadedFiles: m.uploadedFiles.map(uf => ({
        ...uf,
        storedFileName:
          uf.file instanceof File
            ? uf.file.name
            : typeof (uf as QueuedFile & { storedFileName?: string }).storedFileName === 'string'
              ? (uf as QueuedFile & { storedFileName?: string }).storedFileName!
              : '',
      })),
    }
  })
}

function hydrateMessagesUploadedFilesFromCache(messages: Message[]): Message[] {
  return messages.map(m => {
    if (!m.uploadedFiles?.length) return m
    const uploadedFiles = m.uploadedFiles.map(uf => {
      if (uf.file instanceof File) return uf
      const sn =
        typeof (uf as QueuedFile & { storedFileName?: string }).storedFileName === 'string'
          ? (uf as QueuedFile & { storedFileName?: string }).storedFileName!.trim()
          : ''
      const name = sn || 'file'
      return { ...uf, file: new File([], name) }
    })
    return { ...m, uploadedFiles }
  })
}

/** After `JSON.parse` of `tasks_v1_*`; restores placeholder `File` for message rows that lost `file` in cache. */
export function hydrateChatTasksFromCache(tasks: ChatTask[]): ChatTask[] {
  return tasks.map(t => ({
    ...t,
    messages: hydrateMessagesUploadedFilesFromCache(t.messages ?? []),
  }))
}

function jsonSanitizeReplacer(_key: string, value: unknown): unknown {
  if (value instanceof File || value instanceof Blob) return undefined
  return value
}

/** Strip fileQueue before writing `tasks_v1_*` — File objects cannot round-trip JSON. Optionally keep busy-task messages for refresh/relogin hydrate. */
export function slimChatTasksForLocalCache(taskList: ChatTask[]): ChatTask[] {
  return taskList.map(t => ({
    ...t,
    messages: taskConversationPersistWhileBusy(t)
      ? messagesWithPersistableUploadedFiles([...(t.messages ?? [])])
      : [],
    fileQueue: [],
  }))
}

/** Persist tasks for localStorage; drops Blob/File so stringify never throws or serializes `{}` for uploads. */
export function stringifyChatTasksForLocalCache(taskList: ChatTask[]): string {
  const slimmed = slimChatTasksForLocalCache(taskList)
  return JSON.stringify(slimmed, jsonSanitizeReplacer)
}

function isOcrBgMeta(meta: Record<string, unknown>): boolean {
  if (typeof meta.kind === 'string' && meta.kind === 'ocr') return true
  // Legacy OCR rows before `kind` was added — require task + queue id cues
  return (
    typeof meta.taskId === 'string' &&
    typeof meta.progressMessageId === 'string' &&
    typeof meta.queuedFileId === 'string'
  )
}

/**
 * Tasks that appear only via local BG keys (server list skipped the row briefly, or logout cleared cache)
 * — without a sidebar row OCR resume skips delivery because `tasksRef` had no matching id.
 */
export function ghostChatTasksFromActiveOcrBgJobs(
  companyId: string,
  taskIdsAlreadyPresent: Set<string>,
): ChatTask[] {
  const byTask = new Map<string, ChatTask>()

  for (let i = 0; i < localStorage.length; i++) {
    const lsKey = localStorage.key(i)
    if (!lsKey?.startsWith(BG_JOB_STORAGE_PREFIX)) continue
    try {
      const raw = localStorage.getItem(lsKey)
      if (!raw) continue
      const meta = JSON.parse(raw) as Record<string, unknown>
      if (!isOcrBgMeta(meta)) continue
      const cid = typeof meta.companyId === 'string' ? meta.companyId : null
      if (cid !== companyId) continue
      const taskId = typeof meta.taskId === 'string' ? meta.taskId : ''
      const jobId = lsKey.slice(BG_JOB_STORAGE_PREFIX.length)
      if (!taskId || taskIdsAlreadyPresent.has(taskId)) continue

      const fileName =
        typeof meta.fileName === 'string' && meta.fileName.trim().length > 0
          ? meta.fileName.trim()
          : 'file'
      const queuedFileId = typeof meta.queuedFileId === 'string' ? meta.queuedFileId : ''
      const progressMessageId =
        typeof meta.progressMessageId === 'string' && meta.progressMessageId.length > 0
          ? meta.progressMessageId
          : `bg-ocr-${jobId}`
      const pmRaw = meta.processingMode
      const processingMode: ProcessingMode =
        typeof pmRaw === 'string' ? normalizeClientProcessingMode(pmRaw) : normalizeClientProcessingMode('AR')

      const progressMsg = {
        id: progressMessageId,
        role: 'assistant' as const,
        content: '',
        progressPercent: 12,
        progressLabel: `Resuming OCR (${fileName})`,
        progressJob: {
          kind: 'ocr' as const,
          jobId,
          taskId,
          ...(queuedFileId ? { fileId: queuedFileId } : {}),
        },
      }

      const existing = byTask.get(taskId)
      if (!existing) {
        byTask.set(taskId, {
          id: taskId,
          title: `Processing: ${fileName}`,
          createdAt: new Date().toISOString(),
          status: 'processing',
          processingMode,
          messages: [progressMsg],
          fileQueue: [],
          fileCount: 1,
          pageCount: 0,
          hasSpreadsheet: false,
        })
      } else {
        const ids = new Set(existing.messages.map(m => m.id))
        if (!ids.has(progressMessageId))
          existing.messages = [...existing.messages, progressMsg]
      }
    } catch {
      /* ignore */
    }
  }

  return [...byTask.values()]
}
