import { describe, expect, it } from 'vitest'
import {
  applyCropConfirmOptimisticRun,
  dequeueCropExtract,
  enqueueCropExtract,
  extractingCropFileIds,
  isStaleCropExtracting,
  normalizeCropGateFile,
  normalizeRunCropGateFiles,
  removeCropExtractQueueItem,
  reopenCropGateAfterInterrupt,
  sealCompletedCropExtract,
  shouldQueueCropExtract,
} from './cropGateConfirm'

function fileWithPhase(
  phase: string,
  status: string,
  extras: Record<string, unknown> = {},
) {
  return {
    task_file_id: 'tf1',
    file_status: status,
    error_text: phase === 'preview' ? 'needs_crop_preview' : null,
    result_summary_json: {
      needs_crop_preview: phase === 'preview',
      crop_status: phase === 'preview' ? 'needs_crop_preview' : phase,
      crop_preview: {
        phase,
        page: 1,
        boxes: [
          {
            id: 'a',
            index: 1,
            page: 1,
            region_norm: { x: 0.1, y: 0.1, w: 0.2, h: 0.2 },
            extract_status: phase === 'extracting' ? 'running' : 'pending',
          },
        ],
      },
      ...extras,
    },
  }
}

describe('crop gate hang / desync helpers', () => {
  it('detects Stopped + phase=extracting as stale', () => {
    expect(isStaleCropExtracting(fileWithPhase('extracting', 'stopped'))).toBe(true)
    expect(isStaleCropExtracting(fileWithPhase('extracting', 'running'))).toBe(false)
  })

  it('reopens Needs-review after interrupt (clears extracting banner state)', () => {
    const next = reopenCropGateAfterInterrupt(fileWithPhase('extracting', 'stopped'))
    expect(next.file_status).toBe('warning')
    expect(next.error_text).toBe('needs_crop_preview')
    const summary = next.result_summary_json!
    expect(summary.needs_crop_preview).toBe(true)
    expect((summary.crop_preview as { phase: string }).phase).toBe('preview')
    expect(
      ((summary.crop_preview as { boxes: Array<{ extract_status: string }> }).boxes[0]!
        .extract_status),
    ).toBe('pending')
  })

  it('seals completed extract so ok file is not stuck extracting', () => {
    const next = sealCompletedCropExtract(fileWithPhase('extracting', 'ok'))
    expect(next.file_status).toBe('ok')
    const summary = next.result_summary_json!
    expect(summary.needs_crop_preview).toBe(false)
    expect((summary.crop_preview as { phase: string }).phase).toBe('done')
  })

  it('normalize: stopped+extracting → reopen; ok+extracting → seal', () => {
    const stopped = normalizeCropGateFile(fileWithPhase('extracting', 'stopped'))
    expect(stopped.file_status).toBe('warning')
    expect(stopped.error_text).toBe('needs_crop_preview')

    const sealed = normalizeCropGateFile(fileWithPhase('extracting', 'ok'))
    expect(sealed.file_status).toBe('ok')
    expect((sealed.result_summary_json!.crop_preview as { phase: string }).phase).toBe(
      'done',
    )
  })

  it('normalizeRunCropGateFiles heals mixed run without touching live running extract', () => {
    const run = {
      id: 'r1',
      files: [
        fileWithPhase('extracting', 'stopped'),
        {
          ...fileWithPhase('extracting', 'running'),
          task_file_id: 'tf-live',
        },
      ],
    }
    const next = normalizeRunCropGateFiles(run)
    expect(next.files[0]!.file_status).toBe('warning')
    expect(next.files[1]!.file_status).toBe('running')
    expect(
      (next.files[1]!.result_summary_json!.crop_preview as { phase: string }).phase,
    ).toBe('extracting')
  })

  it('applyCropConfirmOptimisticRun still marks executing for poll/WS', () => {
    const run = {
      id: 'run1',
      run_status: 'warning',
      files: [fileWithPhase('preview', 'warning')],
    }
    const next = applyCropConfirmOptimisticRun(run, 'tf1', { phase: 'extracting' })
    expect(next.run_status).toBe('executing')
    expect(next.files[0]!.file_status).toBe('running')
    expect((next.files[0]!.result_summary_json!.crop_preview as { phase: string }).phase).toBe(
      'extracting',
    )
  })
})

describe('crop extract queue helpers', () => {
  it('lists only live extracting file ids', () => {
    const files = [
      { ...fileWithPhase('extracting', 'running'), task_file_id: 'a' },
      { ...fileWithPhase('preview', 'warning'), task_file_id: 'b' },
      { ...fileWithPhase('extracting', 'stopped'), task_file_id: 'c' },
    ]
    expect(extractingCropFileIds(files)).toEqual(['a'])
  })

  it('queues confirm when a sibling is extracting or confirm HTTP is busy', () => {
    expect(
      shouldQueueCropExtract({
        cropGateBusy: false,
        extractingFileIds: ['file-a'],
        targetFileId: 'file-b',
      }),
    ).toBe(true)
    expect(
      shouldQueueCropExtract({
        cropGateBusy: true,
        extractingFileIds: [],
        targetFileId: 'file-b',
      }),
    ).toBe(true)
    expect(
      shouldQueueCropExtract({
        cropGateBusy: false,
        extractingFileIds: [],
        targetFileId: 'file-b',
      }),
    ).toBe(false)
    expect(
      shouldQueueCropExtract({
        cropGateBusy: false,
        extractingFileIds: ['file-b'],
        targetFileId: 'file-b',
      }),
    ).toBe(false)
  })

  it('enqueues latest job per file and dequeues FIFO', () => {
    let queue = enqueueCropExtract([], {
      taskFileId: 'b',
      action: 'confirm',
      boxes: [{ id: '1' }],
    })
    queue = enqueueCropExtract(queue, { taskFileId: 'c', action: 'skip' })
    queue = enqueueCropExtract(queue, {
      taskFileId: 'b',
      action: 'confirm',
      boxes: [{ id: '2' }],
    })
    expect(queue).toEqual([
      { taskFileId: 'c', action: 'skip' },
      { taskFileId: 'b', action: 'confirm', boxes: [{ id: '2' }] },
    ])
    const first = dequeueCropExtract(queue)
    expect(first.next).toEqual({ taskFileId: 'c', action: 'skip' })
    expect(first.rest).toEqual([
      { taskFileId: 'b', action: 'confirm', boxes: [{ id: '2' }] },
    ])
    expect(removeCropExtractQueueItem(queue, 'b')).toEqual([
      { taskFileId: 'c', action: 'skip' },
    ])
  })
})
