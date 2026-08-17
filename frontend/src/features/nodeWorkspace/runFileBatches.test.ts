import { describe, expect, it } from 'vitest'
import type { WorkflowRunFile } from './workflowApi'
import {
  committedTimelineBatches,
  composerStagingFiles,
  isComposerStagingFile,
  workflowQueueFiles,
} from './runFileBatches'

function file(partial: Partial<WorkflowRunFile> & Pick<WorkflowRunFile, 'task_file_id'>): WorkflowRunFile {
  return {
    id: partial.id ?? partial.task_file_id,
    task_file_id: partial.task_file_id,
    file_status: partial.file_status ?? 'pending',
    upload_batch_id: partial.upload_batch_id,
    uploaded_at: partial.uploaded_at,
    batch_committed_at: partial.batch_committed_at,
    original_filename: partial.original_filename,
  }
}

describe('runFileBatches', () => {
  it('composer shows only uncommitted staging files', () => {
    const files = [
      file({ task_file_id: 'a', file_status: 'ok', batch_committed_at: '2026-05-20T10:00:00Z' }),
      file({ task_file_id: 'b', file_status: 'pending', upload_batch_id: 'batch-2' }),
    ]
    expect(isComposerStagingFile(files[0]!)).toBe(false)
    expect(isComposerStagingFile(files[1]!)).toBe(true)
    expect(composerStagingFiles(files).map(f => f.task_file_id)).toEqual(['b'])
  })

  it('timeline groups committed files by upload batch', () => {
    const files = [
      file({
        task_file_id: 'a',
        upload_batch_id: 'batch-1',
        uploaded_at: '2026-05-20T10:00:00Z',
        batch_committed_at: '2026-05-20T10:01:00Z',
        file_status: 'ok',
      }),
      file({
        task_file_id: 'b',
        upload_batch_id: 'batch-1',
        uploaded_at: '2026-05-20T10:00:00Z',
        batch_committed_at: '2026-05-20T10:01:00Z',
        file_status: 'ok',
      }),
      file({
        task_file_id: 'c',
        upload_batch_id: 'batch-2',
        uploaded_at: '2026-05-20T11:00:00Z',
        batch_committed_at: '2026-05-20T11:01:00Z',
        file_status: 'running',
      }),
      file({
        task_file_id: 'd',
        upload_batch_id: 'batch-3',
        uploaded_at: '2026-05-20T12:00:00Z',
        file_status: 'pending',
      }),
    ]
    const batches = committedTimelineBatches(files)
    expect(batches).toHaveLength(2)
    expect(batches[0]!.uploadBatchId).toBe('batch-1')
    expect(batches[0]!.files).toHaveLength(2)
    expect(batches[1]!.uploadBatchId).toBe('batch-2')
    expect(batches[1]!.files.map(f => f.task_file_id)).toEqual(['c'])
  })

  it('workflow queue hides committed ok files unless retry or running', () => {
    const files = [
      file({
        task_file_id: 'a',
        file_status: 'ok',
        batch_committed_at: '2026-05-20T10:01:00Z',
      }),
      file({ task_file_id: 'b', file_status: 'pending', upload_batch_id: 'batch-2' }),
      file({
        task_file_id: 'c',
        file_status: 'running',
        batch_committed_at: '2026-05-20T11:00:00Z',
      }),
    ]
    expect(workflowQueueFiles(files).map(f => f.task_file_id).sort()).toEqual(['b', 'c'])
    expect(workflowQueueFiles(files, ['a']).map(f => f.task_file_id).sort()).toEqual(['a', 'b', 'c'])
  })
})
