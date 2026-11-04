import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { taskApi } from '../../services/api'
import { PDF_NOT_AVAILABLE_FOR_RUN, normalizePreviewPage } from './pdfPreviewSrc'
import { guessMimeFromFilename } from './resolvePreviewKind'

export type TaskPreviewFile = {
  taskFileId: string
  originalFilename?: string | null
}

export type OpenPreviewOptions = {
  /** 1-based PDF page to open when known (e.g. empty-extract amber banner). */
  page?: number | null
}

type CacheEntry = {
  blobUrl: string
  blob: Blob
  mimeType: string
  filename: string
}

export type FilePreviewState = {
  open: boolean
  activeFileId: string | null
  filename: string
  mimeType: string
  previewUrl: string | null
  loading: boolean
  error: string | null
  /** 1-based page for PDF fragment navigation; null when unknown. */
  page: number | null
}

function filenameFor(file: TaskPreviewFile): string {
  return file.originalFilename?.trim() || file.taskFileId
}

export function useTaskFilePreview(
  taskId: string | null | undefined,
  companyId: string | null | undefined,
  files: TaskPreviewFile[],
) {
  const cacheRef = useRef<Map<string, CacheEntry>>(new Map())
  const localFilesRef = useRef<Map<string, File>>(new Map())
  const [state, setState] = useState<FilePreviewState>({
    open: false,
    activeFileId: null,
    filename: '',
    mimeType: '',
    previewUrl: null,
    loading: false,
    error: null,
    page: null,
  })

  const fileList = useMemo(
    () =>
      files.map(f => ({
        id: f.taskFileId,
        name: filenameFor(f),
      })),
    [files],
  )

  const revokeEntry = useCallback((entry: CacheEntry) => {
    URL.revokeObjectURL(entry.blobUrl)
  }, [])

  const putCache = useCallback(
    (fileId: string, blob: Blob, filename: string, mimeType: string) => {
      const prev = cacheRef.current.get(fileId)
      if (prev) revokeEntry(prev)
      const blobUrl = URL.createObjectURL(blob)
      const entry: CacheEntry = { blobUrl, blob, mimeType, filename }
      cacheRef.current.set(fileId, entry)
      return entry
    },
    [revokeEntry],
  )

  const fetchFile = useCallback(
    async (fileId: string, filename: string): Promise<CacheEntry> => {
      if (!taskId) throw new Error('No task selected.')
      const local = localFilesRef.current.get(fileId)
      if (local) {
        const mime = local.type || guessMimeFromFilename(filename)
        return putCache(fileId, local, filename, mime)
      }

      const cached = cacheRef.current.get(fileId)
      if (cached) return cached

      const blob = await taskApi.downloadFile(taskId, fileId, companyId)
      const mime = blob.type || guessMimeFromFilename(filename)
      return putCache(fileId, blob, filename, mime)
    },
    [taskId, companyId, putCache],
  )

  const openPreview = useCallback(
    async (taskFileId: string, opts?: OpenPreviewOptions) => {
      const page = normalizePreviewPage(opts?.page)
      // Security: only open this run's own stored file IDs.
      const meta = files.find(f => f.taskFileId === taskFileId)
      if (!meta) {
        setState({
          open: true,
          activeFileId: taskFileId,
          filename: taskFileId,
          mimeType: '',
          previewUrl: null,
          loading: false,
          error: PDF_NOT_AVAILABLE_FOR_RUN,
          page,
        })
        return
      }
      const filename = filenameFor(meta)
      setState({
        open: true,
        activeFileId: taskFileId,
        filename,
        mimeType: '',
        previewUrl: null,
        loading: true,
        error: null,
        page,
      })
      try {
        const entry = await fetchFile(taskFileId, filename)
        setState({
          open: true,
          activeFileId: taskFileId,
          filename: entry.filename,
          mimeType: entry.mimeType,
          previewUrl: entry.blobUrl,
          loading: false,
          error: null,
          page,
        })
      } catch {
        setState(prev => ({
          ...prev,
          loading: false,
          error: PDF_NOT_AVAILABLE_FOR_RUN,
          previewUrl: null,
        }))
      }
    },
    [files, fetchFile],
  )

  const closePreview = useCallback(() => {
    setState(prev => ({ ...prev, open: false, loading: false }))
  }, [])

  const retryPreview = useCallback(() => {
    if (state.activeFileId) {
      void openPreview(state.activeFileId, { page: state.page })
    }
  }, [state.activeFileId, state.page, openPreview])

  const registerLocalFile = useCallback((taskFileId: string, file: File) => {
    localFilesRef.current.set(taskFileId, file)
  }, [])

  const prefetchFiles = useCallback(
    async (taskFileIds: string[]) => {
      if (!taskId || taskFileIds.length === 0) return
      await Promise.all(
        taskFileIds.map(async id => {
          const meta = files.find(f => f.taskFileId === id)
          const filename = meta ? filenameFor(meta) : id
          try {
            await fetchFile(id, filename)
          } catch {
            /* prefetch is best-effort */
          }
        }),
      )
    },
    [taskId, files, fetchFile],
  )

  const downloadActive = useCallback(() => {
    const id = state.activeFileId
    if (!id) return
    const entry = cacheRef.current.get(id)
    if (!entry) return
    const a = document.createElement('a')
    a.href = entry.blobUrl
    a.download = entry.filename
    a.click()
  }, [state.activeFileId])

  const revokeAll = useCallback(() => {
    cacheRef.current.forEach(entry => revokeEntry(entry))
    cacheRef.current.clear()
    localFilesRef.current.clear()
  }, [revokeEntry])

  useEffect(() => () => revokeAll(), [revokeAll])

  return {
    state,
    fileList,
    openPreview,
    closePreview,
    retryPreview,
    registerLocalFile,
    prefetchFiles,
    downloadActive,
    revokeAll,
  }
}
