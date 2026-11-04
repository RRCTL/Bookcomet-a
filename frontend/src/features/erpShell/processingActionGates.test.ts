import { describe, expect, it } from 'vitest'
import {
  ANOTHER_RUN_PROCESSING_HINT,
  anotherRunIsProcessing,
  canExecuteOnActiveRun,
  canReVlmOnActiveRun,
  canStopActiveRun,
  canUploadOnActiveRun,
  liveOutputShowsActiveBatch,
} from './processingActionGates'

describe('canStopActiveRun', () => {
  it('enables Stop while run is Processing even if a long HTTP busy flag would exist', () => {
    expect(
      canStopActiveRun({
        hasActiveRun: true,
        runStatus: 'executing',
        files: [
          { file_status: 'ok' },
          { file_status: 'running' },
          { file_status: 'pending' },
        ],
      }),
    ).toBe(true)
  })

  it('enables Stop when Live output is Processing/Queued mid-batch', () => {
    expect(
      liveOutputShowsActiveBatch(
        [{ file_status: 'running' }, { file_status: 'queued' }],
        true,
      ),
    ).toBe(true)
    expect(
      canStopActiveRun({
        hasActiveRun: true,
        runStatus: 'executing',
        files: [{ file_status: 'pending' }, { file_status: 'queued' }],
      }),
    ).toBe(true)
  })

  it('enables Stop when sidebar VLM node is spinning even if run_status lags', () => {
    expect(
      canStopActiveRun({
        hasActiveRun: true,
        runStatus: 'awaiting_review',
        files: [{ file_status: 'ok' }],
        nodeStates: { vlm: { status: 'running' } },
      }),
    ).toBe(true)
  })

  it('keeps Stop grey on a Draft with no mid-batch work', () => {
    expect(
      canStopActiveRun({
        hasActiveRun: true,
        runStatus: 'draft',
        files: [],
      }),
    ).toBe(false)
    expect(
      canStopActiveRun({
        hasActiveRun: true,
        runStatus: 'draft',
        files: [{ file_status: 'pending' }],
      }),
    ).toBe(false)
  })
})

describe('cross-run upload vs Run/Re-VLM gates', () => {
  const runs = [
    { id: 'run-a', run_status: 'executing' },
    { id: 'run-b', run_status: 'draft' },
  ]

  it('detects another run Processing', () => {
    expect(anotherRunIsProcessing(runs, 'run-b', null)).toBe(true)
    expect(anotherRunIsProcessing(runs, 'run-a', null)).toBe(false)
    expect(anotherRunIsProcessing(runs, 'run-b', 'run-a')).toBe(true)
    expect(anotherRunIsProcessing([{ id: 'run-b', run_status: 'draft' }], 'run-b', null)).toBe(
      false,
    )
  })

  it('allows Upload on a quiet open run while another is Processing', () => {
    expect(
      canUploadOnActiveRun({
        hasActiveRun: true,
        fileCount: 0,
        busyOnActiveRun: false,
      }),
    ).toBe(true)
  })

  it('blocks Upload only for this run busy or when files already staged', () => {
    expect(
      canUploadOnActiveRun({
        hasActiveRun: true,
        fileCount: 0,
        busyOnActiveRun: true,
      }),
    ).toBe(false)
    expect(
      canUploadOnActiveRun({
        hasActiveRun: true,
        fileCount: 2,
        busyOnActiveRun: false,
      }),
    ).toBe(false)
  })

  it('keeps Run and Re-VLM grey on a quiet run while another is Processing', () => {
    expect(
      canExecuteOnActiveRun({
        hasActiveRun: true,
        fileCount: 3,
        activeRunProcessing: false,
        busyOnActiveRun: false,
        anotherRunProcessing: true,
      }),
    ).toBe(false)
    expect(
      canReVlmOnActiveRun({
        hasActiveRun: true,
        hasProcessedFiles: true,
        activeRunProcessing: false,
        busyOnActiveRun: false,
        anotherRunProcessing: true,
        reVlmLocked: false,
      }),
    ).toBe(false)
  })

  it('unlocks Run on a quiet run once the other finishes', () => {
    expect(
      canExecuteOnActiveRun({
        hasActiveRun: true,
        fileCount: 3,
        activeRunProcessing: false,
        busyOnActiveRun: false,
        anotherRunProcessing: false,
      }),
    ).toBe(true)
  })

  it('exposes the exact cross-run hint copy', () => {
    expect(ANOTHER_RUN_PROCESSING_HINT).toContain('Another run is processing')
    expect(ANOTHER_RUN_PROCESSING_HINT).toContain('upload files now')
    expect(ANOTHER_RUN_PROCESSING_HINT).toContain('Run unlocks')
  })
})
