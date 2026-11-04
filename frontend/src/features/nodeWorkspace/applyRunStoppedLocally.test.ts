import { describe, expect, it } from 'vitest'
import { applyRunStoppedLocally, type WorkflowRun } from './workflowApi'

function baseRun(overrides?: Partial<WorkflowRun>): WorkflowRun {
  return {
    id: 'run-stop-1',
    task_id: 'task-stop-1',
    company_id: 'co-fiction',
    processing_mode: 'BANK',
    title: 'Synthetic stop batch',
    run_status: 'executing',
    graph_json: {
      nodes: [
        { id: 'files', type: 'Files', position: { x: 0, y: 0 }, data: { label: 'Files' } },
        { id: 'vlm', type: 'VLM_API', position: { x: 0, y: 1 }, data: { label: 'VLM' } },
        { id: 'review', type: 'TableReview', position: { x: 0, y: 2 }, data: { label: 'Table Review' } },
      ],
      edges: [],
    },
    node_states_json: {
      files: { status: 'completed' },
      vlm: { status: 'running' },
      review: { status: 'pending' },
    },
    files: [
      {
        id: 'rf-1',
        task_file_id: 'file-done',
        file_status: 'ok',
        original_filename: 'synthetic-done.pdf',
      },
      {
        id: 'rf-2',
        task_file_id: 'file-mid',
        file_status: 'running',
        original_filename: 'synthetic-mid.pdf',
      },
      {
        id: 'rf-3',
        task_file_id: 'file-queued',
        file_status: 'pending',
        original_filename: 'synthetic-queued.pdf',
      },
      {
        id: 'rf-4',
        task_file_id: 'file-q2',
        file_status: 'queued',
        original_filename: 'synthetic-q2.pdf',
      },
    ],
    created_at: '',
    updated_at: '',
    ...overrides,
  }
}

describe('applyRunStoppedLocally', () => {
  it('keeps finished pages, marks interrupted Stopped and queued Cancelled, clears VLM spinner', () => {
    const next = applyRunStoppedLocally(baseRun())
    expect(next.files.find(f => f.task_file_id === 'file-done')?.file_status).toBe('ok')
    expect(next.files.find(f => f.task_file_id === 'file-mid')?.file_status).toBe('stopped')
    expect(next.files.find(f => f.task_file_id === 'file-queued')?.file_status).toBe('cancelled')
    expect(next.files.find(f => f.task_file_id === 'file-q2')?.file_status).toBe('cancelled')
    expect((next.node_states_json?.vlm as { status?: string })?.status).toBe('cancelled')
    expect(next.run_status).toBe('awaiting_review')
  })

  it('does not clear the file list / grid membership on Stop', () => {
    const before = baseRun()
    const next = applyRunStoppedLocally(before)
    expect(next.files).toHaveLength(before.files.length)
    expect(next.files.map(f => f.task_file_id)).toEqual(before.files.map(f => f.task_file_id))
  })

  it('returns draft when nothing reviewable remains', () => {
    const next = applyRunStoppedLocally(
      baseRun({
        files: [
          { id: 'rf-1', task_file_id: 'a', file_status: 'running', original_filename: 'a.pdf' },
          { id: 'rf-2', task_file_id: 'b', file_status: 'pending', original_filename: 'b.pdf' },
        ],
      }),
    )
    expect(next.run_status).toBe('draft')
  })
})
