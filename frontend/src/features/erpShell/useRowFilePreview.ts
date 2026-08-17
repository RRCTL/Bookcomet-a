import { useCallback, useEffect, useRef, useState } from 'react'
import { taskApi } from '../../services/api'
import { guessMimeFromFilename } from '../../components/filePreview'

export type RowPreviewState = {
  open: boolean
  filename: string
  mimeType: string
  previewUrl: string | null
  loading: boolean
  error: string | null
}

const CLOSED: RowPreviewState = {
  open: false,
  filename: '',
  mimeType: '',
  previewUrl: null,
  loading: false,
  error: null,
}

/**
 * Lightweight preview controller for arbitrary (task, file) pairs across many
 * rows in an ERP grid. Downloads the source file on demand and feeds the shared
 * FilePreviewModal. Unlike useTaskFilePreview it is not bound to a single task.
 */
export function useRowFilePreview(companyId: string | null | undefined) {
  const urlRef = useRef<string | null>(null)
  const [state, setState] = useState<RowPreviewState>(CLOSED)

  const revoke = useCallback(() => {
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current)
      urlRef.current = null
    }
  }, [])

  const open = useCallback(
    async (taskId: string, fileId: string, filename: string) => {
      revoke()
      setState({ open: true, filename, mimeType: '', previewUrl: null, loading: true, error: null })
      try {
        const blob = await taskApi.downloadFile(taskId, fileId, companyId)
        const url = URL.createObjectURL(blob)
        urlRef.current = url
        setState({
          open: true,
          filename,
          mimeType: blob.type || guessMimeFromFilename(filename),
          previewUrl: url,
          loading: false,
          error: null,
        })
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'Could not load file preview.'
        setState({ open: true, filename, mimeType: '', previewUrl: null, loading: false, error: msg })
      }
    },
    [companyId, revoke],
  )

  const close = useCallback(() => {
    setState(CLOSED)
    revoke()
  }, [revoke])

  const download = useCallback(() => {
    if (!urlRef.current) return
    const a = document.createElement('a')
    a.href = urlRef.current
    a.download = state.filename
    a.click()
  }, [state.filename])

  useEffect(() => () => revoke(), [revoke])

  return { state, open, close, download }
}
