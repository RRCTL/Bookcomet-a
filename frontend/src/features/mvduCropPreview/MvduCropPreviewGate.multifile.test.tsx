import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CropPreviewGateNavFile, CropPreviewPayload } from './types'

vi.mock('../../services/api', () => ({
  taskApi: {
    downloadReceiptCrop: vi.fn(async () => new Blob([new Uint8Array([1, 2, 3])], { type: 'image/jpeg' })),
  },
}))

import { MvduCropPreviewGate } from './MvduCropPreviewGate'

function pagePreview(
  fileLabel: string,
  pages: Array<{ page: number; boxId: string }>,
): CropPreviewPayload {
  const entries = pages.map(({ page, boxId }) => ({
    page,
    status: 'pending' as const,
    image_width: 200,
    image_height: 200,
    boxes: [
      {
        id: boxId,
        index: 1,
        page,
        region_norm: { x: 0.1, y: 0.1, w: 0.3, h: 0.4 },
        rotation_deg: 0,
        extract_status: 'pending' as const,
      },
    ],
  }))
  const first = entries[0]!
  return {
    phase: 'preview',
    page: first.page,
    image_width: first.image_width,
    image_height: first.image_height,
    needs_split_review: false,
    boxes: first.boxes,
    pages: entries,
    message: fileLabel,
  }
}

function twoFileNav(): {
  files: CropPreviewGateNavFile[]
  fileA: CropPreviewGateNavFile
  fileB: CropPreviewGateNavFile
} {
  const fileA: CropPreviewGateNavFile = {
    taskFileId: 'file-a',
    filename: 'Receipt A.pdf',
    preview: pagePreview('A', [
      { page: 1, boxId: 'a1' },
      { page: 2, boxId: 'a2' },
    ]),
  }
  const fileB: CropPreviewGateNavFile = {
    taskFileId: 'file-b',
    filename: 'Receipt B.pdf',
    preview: pagePreview('B', [
      { page: 1, boxId: 'b1' },
      { page: 2, boxId: 'b2' },
    ]),
  }
  return { files: [fileA, fileB], fileA, fileB }
}

describe('MvduCropPreviewGate file-level nav', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('hides file control for a single gated file', () => {
    const { fileA } = twoFileNav()
    render(
      <MvduCropPreviewGate
        open
        taskId="task-1"
        taskFileId={fileA.taskFileId}
        filename={fileA.filename}
        preview={fileA.preview}
        gatedFiles={[fileA]}
        onConfirm={vi.fn()}
        onSkip={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    expect(screen.queryByLabelText('File control')).not.toBeInTheDocument()
    expect(screen.queryByTestId('mvdu-crop-gate-file-pos')).not.toBeInTheDocument()
  })

  it('shows file strip above page thumbs and switches active file without extracting', async () => {
    const { files, fileA, fileB } = twoFileNav()
    const onActiveFileChange = vi.fn()
    const onConfirm = vi.fn()
    const { rerender } = render(
      <MvduCropPreviewGate
        open
        taskId="task-1"
        taskFileId={fileA.taskFileId}
        filename={fileA.filename}
        preview={fileA.preview}
        gatedFiles={files}
        onActiveFileChange={onActiveFileChange}
        onConfirm={onConfirm}
        onSkip={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    expect(screen.getByLabelText('File control')).toBeInTheDocument()
    expect(screen.getByTestId('mvdu-crop-gate-file-pos')).toHaveTextContent('1/2')
    expect(screen.getByText(/file 1 of 2/i)).toBeInTheDocument()
    expect(screen.getByText(/page 1 of 2/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Page control')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('mvdu-crop-gate-file-next'))
    expect(onActiveFileChange).toHaveBeenCalledWith('file-b')
    expect(onConfirm).not.toHaveBeenCalled()

    rerender(
      <MvduCropPreviewGate
        open
        taskId="task-1"
        taskFileId={fileB.taskFileId}
        filename={fileB.filename}
        preview={fileB.preview}
        gatedFiles={files}
        onActiveFileChange={onActiveFileChange}
        onConfirm={onConfirm}
        onSkip={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('mvdu-crop-gate-file-pos')).toHaveTextContent('2/2')
    })
    expect(screen.getByText(/file 2 of 2/i)).toBeInTheDocument()
    expect(screen.getByText(/page 1 of 2/i)).toBeInTheDocument()
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('restores mid-gate CONFIRMED state when returning to a file', async () => {
    const { files, fileA, fileB } = twoFileNav()
    const onActiveFileChange = vi.fn()
    const onConfirm = vi.fn()
    const { rerender } = render(
      <MvduCropPreviewGate
        open
        taskId="task-1"
        taskFileId={fileA.taskFileId}
        filename={fileA.filename}
        preview={fileA.preview}
        gatedFiles={files}
        onActiveFileChange={onActiveFileChange}
        onConfirm={onConfirm}
        onSkip={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    // Confirm page 1 on file A → advances to page 2, does not extract.
    fireEvent.click(screen.getByRole('button', { name: /Confirm page/i }))
    await waitFor(() => {
      expect(screen.getByText(/page 2 of 2/i)).toBeInTheDocument()
    })
    expect(onConfirm).not.toHaveBeenCalled()
    expect(screen.getByText(/✓ CONFIRMED/i)).toBeInTheDocument()

    // Switch to file B.
    fireEvent.click(screen.getByTestId('mvdu-crop-gate-file-next'))
    expect(onActiveFileChange).toHaveBeenCalledWith('file-b')
    rerender(
      <MvduCropPreviewGate
        open
        taskId="task-1"
        taskFileId={fileB.taskFileId}
        filename={fileB.filename}
        preview={fileB.preview}
        gatedFiles={files}
        onActiveFileChange={onActiveFileChange}
        onConfirm={onConfirm}
        onSkip={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    await waitFor(() => {
      expect(screen.getByTestId('mvdu-crop-gate-file-pos')).toHaveTextContent('2/2')
    })
    expect(screen.getByText(/file 2 of 2/i)).toBeInTheDocument()
    expect(screen.queryByText(/✓ CONFIRMED/i)).not.toBeInTheDocument()

    // Return to file A — CONFIRMED page 1 restored, still on page 2.
    fireEvent.click(screen.getByTestId('mvdu-crop-gate-file-prev'))
    expect(onActiveFileChange).toHaveBeenCalledWith('file-a')
    rerender(
      <MvduCropPreviewGate
        open
        taskId="task-1"
        taskFileId={fileA.taskFileId}
        filename={fileA.filename}
        preview={fileA.preview}
        gatedFiles={files}
        onActiveFileChange={onActiveFileChange}
        onConfirm={onConfirm}
        onSkip={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    await waitFor(() => {
      expect(screen.getByText(/page 2 of 2/i)).toBeInTheDocument()
    })
    expect(screen.getByText(/✓ CONFIRMED/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Confirm crops & extract/i })).toBeInTheDocument()
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('extract sends only the active file CONFIRMED pages', async () => {
    const { files, fileA } = twoFileNav()
    const onConfirm = vi.fn()
    render(
      <MvduCropPreviewGate
        open
        taskId="task-1"
        taskFileId={fileA.taskFileId}
        filename={fileA.filename}
        preview={fileA.preview}
        gatedFiles={files}
        onConfirm={onConfirm}
        onSkip={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /Confirm page/i }))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Confirm crops & extract/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /Confirm crops & extract/i }))
    await waitFor(() => {
      expect(onConfirm).toHaveBeenCalledTimes(1)
    })
    const sent = onConfirm.mock.calls[0]![0] as Array<{ page: number; id: string }>
    expect(sent.map(b => b.page).sort()).toEqual([1, 2])
    expect(sent.every(b => b.id.startsWith('a'))).toBe(true)
  })
})
