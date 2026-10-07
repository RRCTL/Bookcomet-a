import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PDF_NOT_AVAILABLE_FOR_RUN } from './pdfPreviewSrc'
import { useTaskFilePreview } from './useTaskFilePreview'

const downloadFile = vi.fn()

vi.mock('../../services/api', () => ({
  taskApi: {
    downloadFile: (...args: unknown[]) => downloadFile(...args),
  },
}))

describe('useTaskFilePreview run-scoped PDF preview', () => {
  beforeEach(() => {
    downloadFile.mockReset()
    downloadFile.mockResolvedValue(new Blob(['%PDF-fictional'], { type: 'application/pdf' }))
  })

  it('opens the correct task_file_id for this run and keeps page', async () => {
    const { result } = renderHook(() =>
      useTaskFilePreview('task-fictional', 'co-fictional', [
        { taskFileId: 'tf-fictional', originalFilename: 'Fictional-Statement.pdf' },
      ]),
    )
    await act(async () => {
      await result.current.openPreview('tf-fictional', { page: 2 })
    })
    expect(downloadFile).toHaveBeenCalledWith('task-fictional', 'tf-fictional', 'co-fictional')
    expect(result.current.state.open).toBe(true)
    expect(result.current.state.activeFileId).toBe('tf-fictional')
    expect(result.current.state.page).toBe(2)
    expect(result.current.state.error).toBeNull()
    expect(result.current.state.previewUrl).toBeTruthy()
  })

  it('shows PDF not available for this run when file id is foreign', async () => {
    const { result } = renderHook(() =>
      useTaskFilePreview('task-fictional', 'co-fictional', [
        { taskFileId: 'tf-fictional', originalFilename: 'Fictional-Statement.pdf' },
      ]),
    )
    await act(async () => {
      await result.current.openPreview('tf-foreign-other-run', { page: 1 })
    })
    expect(downloadFile).not.toHaveBeenCalled()
    expect(result.current.state.error).toBe(PDF_NOT_AVAILABLE_FOR_RUN)
    expect(result.current.state.previewUrl).toBeNull()
  })

  it('shows PDF not available for this run when download fails', async () => {
    downloadFile.mockRejectedValueOnce(new Error('File download failed: Not Found'))
    const { result } = renderHook(() =>
      useTaskFilePreview('task-fictional', 'co-fictional', [
        { taskFileId: 'tf-fictional', originalFilename: 'Fictional-Statement.pdf' },
      ]),
    )
    await act(async () => {
      await result.current.openPreview('tf-fictional')
    })
    expect(result.current.state.error).toBe(PDF_NOT_AVAILABLE_FOR_RUN)
  })
})
