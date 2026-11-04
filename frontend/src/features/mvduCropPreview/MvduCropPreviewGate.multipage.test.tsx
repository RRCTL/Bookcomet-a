import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CropPreviewPayload } from './types'

vi.mock('../../services/api', () => ({
  taskApi: {
    downloadReceiptCrop: vi.fn(async () => new Blob([new Uint8Array([1, 2, 3])], { type: 'image/jpeg' })),
  },
}))

import { MvduCropPreviewGate } from './MvduCropPreviewGate'

function multipagePreview(): CropPreviewPayload {
  return {
    phase: 'preview',
    page: 1,
    image_width: 200,
    image_height: 200,
    needs_split_review: false,
    boxes: [{ id: 'a', index: 1, page: 1, region_norm: { x: 0.1, y: 0.1, w: 0.3, h: 0.4 }, rotation_deg: 0, extract_status: 'pending' }],
    pages: [
      {
        page: 1,
        status: 'pending',
        image_width: 200,
        image_height: 200,
        boxes: [
          {
            id: 'a',
            index: 1,
            page: 1,
            region_norm: { x: 0.1, y: 0.1, w: 0.3, h: 0.4 },
            rotation_deg: 0,
            extract_status: 'pending',
          },
        ],
      },
      {
        page: 2,
        status: 'pending',
        image_width: 200,
        image_height: 200,
        boxes: [
          {
            id: 'b',
            index: 1,
            page: 2,
            region_norm: { x: 0.2, y: 0.15, w: 0.35, h: 0.45 },
            rotation_deg: 0,
            extract_status: 'pending',
          },
        ],
      },
    ],
  }
}

describe('MvduCropPreviewGate multipage page control', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('switches raster page on PENDING thumb click without extracting', async () => {
    const onConfirm = vi.fn()
    const onSkip = vi.fn()
    const onCancel = vi.fn()
    render(
      <MvduCropPreviewGate
        open
        taskId="task-1"
        taskFileId="file-1"
        filename="Scanned Document 2.pdf"
        preview={multipagePreview()}
        onConfirm={onConfirm}
        onSkip={onSkip}
        onCancel={onCancel}
      />,
    )

    expect(screen.getByText(/page 1 of 2/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Confirm page/i })).toBeInTheDocument()

    const page2 = await screen.findByRole('button', { name: /Page 2/i })
    fireEvent.click(page2)

    await waitFor(() => {
      expect(screen.getByText(/page 2 of 2/i)).toBeInTheDocument()
    })
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('Confirm page advances to next PENDING; extract only on last page', async () => {
    const onConfirm = vi.fn()
    render(
      <MvduCropPreviewGate
        open
        taskId="task-1"
        taskFileId="file-1"
        filename="Scanned Document 2.pdf"
        preview={multipagePreview()}
        onConfirm={onConfirm}
        onSkip={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /Confirm page/i }))

    await waitFor(() => {
      expect(screen.getByText(/page 2 of 2/i)).toBeInTheDocument()
    })
    expect(onConfirm).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /Confirm crops & extract/i })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Confirm crops & extract/i }))

    await waitFor(() => {
      expect(onConfirm).toHaveBeenCalledTimes(1)
    })
    const sent = onConfirm.mock.calls[0]![0] as Array<{ page: number }>
    expect(sent.map(b => b.page).sort()).toEqual([1, 2])
  })

  it('single-page hides page rail and always shows extract primary', () => {
    render(
      <MvduCropPreviewGate
        open
        taskId="task-1"
        taskFileId="file-1"
        filename="one.png"
        preview={{
          phase: 'preview',
          page: 1,
          image_width: 100,
          image_height: 100,
          needs_split_review: false,
          boxes: [
            {
              id: 'a',
              index: 1,
              page: 1,
              region_norm: { x: 0.1, y: 0.1, w: 0.2, h: 0.2 },
              rotation_deg: 0,
              extract_status: 'pending',
            },
          ],
          pages: [
            {
              page: 1,
              status: 'pending',
              image_width: 100,
              image_height: 100,
              boxes: [
                {
                  id: 'a',
                  index: 1,
                  page: 1,
                  region_norm: { x: 0.1, y: 0.1, w: 0.2, h: 0.2 },
                  rotation_deg: 0,
                  extract_status: 'pending',
                },
              ],
            },
          ],
        }}
        onConfirm={vi.fn()}
        onSkip={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    expect(screen.queryByLabelText('Page control')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Confirm crops & extract/i })).toBeInTheDocument()
  })

  it('Skip asks for confirmation when more than one PENDING page', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const onSkip = vi.fn()
    render(
      <MvduCropPreviewGate
        open
        taskId="task-1"
        taskFileId="file-1"
        filename="Scanned Document 2.pdf"
        preview={multipagePreview()}
        onConfirm={vi.fn()}
        onSkip={onSkip}
        onCancel={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Skip preview/i }))
    expect(confirmSpy).toHaveBeenCalled()
    expect(String(confirmSpy.mock.calls[0]![0])).toMatch(/2 pages/i)
    expect(onSkip).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })
})
