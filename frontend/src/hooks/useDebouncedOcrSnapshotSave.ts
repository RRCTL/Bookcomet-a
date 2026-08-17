import { useCallback, useEffect, useRef } from 'react'
import { taskApi } from '../services/api'

const OCR_SNAPSHOT_DEBOUNCE_MS = 1500

type PendingOcrSnapshotSave = {
  taskId: string
  messageId: string
  contentText: string
  payloadJson: object
  companyId?: string | null
}

/**
 * Debounced PATCH of one ocr_snapshot row (by message id).
 * Timer keyed by taskId + messageId so edits to different tables do not clobber each other.
 */
export function useDebouncedOcrSnapshotSave() {
  const timersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({})
  const pendingRef = useRef<Record<string, PendingOcrSnapshotSave>>({})

  useEffect(() => {
    return () => {
      const timers = { ...timersRef.current }
      timersRef.current = {}
      pendingRef.current = {}
      for (const k of Object.keys(timers)) {
        if (timers[k]) clearTimeout(timers[k])
      }
    }
  }, [])

  const debouncedSaveSnapshot = useCallback(
    (
      taskId: string,
      messageId: string,
      contentText: string,
      payloadJson: object,
      companyId?: string | null,
    ) => {
      const key = `${taskId}\0${messageId}`
      pendingRef.current[key] = { taskId, messageId, contentText, payloadJson, companyId }
      const prev = timersRef.current[key]
      if (prev) clearTimeout(prev)
      timersRef.current[key] = setTimeout(() => {
        const pending = pendingRef.current[key]
        delete pendingRef.current[key]
        delete timersRef.current[key]
        if (!pending) return
        taskApi
          .patchMessage(
            pending.taskId,
            pending.messageId,
            { content_text: pending.contentText, payload_json: pending.payloadJson },
            pending.companyId,
          )
          .catch(err => console.warn('[Tasks] Edit snapshot persist failed:', err))
      }, OCR_SNAPSHOT_DEBOUNCE_MS)
    },
    [],
  )

  /** Cancel a pending debounced PATCH so eager awaits (e.g. cross-table move) do not race stale timers. */
  const cancelDebouncedOcrSnapshot = useCallback((taskId: string, messageId: string) => {
    const key = `${taskId}\0${messageId}`
    const prev = timersRef.current[key]
    if (prev) clearTimeout(prev)
    delete timersRef.current[key]
    delete pendingRef.current[key]
  }, [])

  const flushDebouncedOcrSnapshot = useCallback(async (taskId?: string, messageId?: string) => {
    const keys = Object.keys(pendingRef.current)
    for (const key of keys) {
      const pending = pendingRef.current[key]
      if (!pending) continue
      if (taskId && messageId && key !== `${taskId}\0${messageId}`) continue
      const timer = timersRef.current[key]
      if (timer) clearTimeout(timer)
      delete timersRef.current[key]
      delete pendingRef.current[key]
      await taskApi.patchMessage(
        pending.taskId,
        pending.messageId,
        { content_text: pending.contentText, payload_json: pending.payloadJson },
        pending.companyId,
      )
    }
  }, [])

  return { debouncedSaveSnapshot, cancelDebouncedOcrSnapshot, flushDebouncedOcrSnapshot }
}
