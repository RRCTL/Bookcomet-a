import { describe, expect, it } from 'vitest'
import { applyRunReVlmLocally, type WorkflowRun } from './workflowApi'

function baseRun(overrides?: Partial<WorkflowRun>): WorkflowRun {
  return {
    id: 'run-1',
    task_id: 'task-1',
    company_id: 'co-1',
    processing_mode: 'AP',
    title: 'Synthetic',
    run_status: 'awaiting_review',
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
      vlm: { status: 'completed' },
      review: { status: 'active' },
    },
    files: [
      {
        id: 'rf-1',
        task_file_id: 'file-a',
        file_status: 'ok',
        original_filename: 'synthetic.pdf',
      },
    ],
    created_at: '',
    updated_at: '',
    ...overrides,
  }
}

describe('applyRunReVlmLocally', () => {
  it('marks run executing, target files running, and VLM nodes running', () => {
    const next = applyRunReVlmLocally(baseRun(), ['file-a'], { rescanNote: 'synthetic recheck' })
    expect(next.run_status).toBe('executing')
    expect(next.files[0]?.file_status).toBe('running')
    expect((next.node_states_json?.vlm as { status?: string })?.status).toBe('running')
    expect((next.node_states_json?.files as { status?: string })?.status).toBe('pending')
    expect((next.node_states_json?.review as { status?: string })?.status).toBe('pending')
  })

  it('keeps non-target files unchanged', () => {
    const run = baseRun({
      files: [
        { id: 'rf-1', task_file_id: 'file-a', file_status: 'ok', original_filename: 'a.pdf' },
        { id: 'rf-2', task_file_id: 'file-b', file_status: 'ok', original_filename: 'b.pdf' },
      ],
    })
    const next = applyRunReVlmLocally(run, ['file-b'])
    expect(next.files.find(f => f.task_file_id === 'file-a')?.file_status).toBe('ok')
    expect(next.files.find(f => f.task_file_id === 'file-b')?.file_status).toBe('running')
  })
})
