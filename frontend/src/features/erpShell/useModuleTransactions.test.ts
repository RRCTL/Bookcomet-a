import { describe, expect, it } from 'vitest'
import type { WorkflowRun } from '../nodeWorkspace/workflowApi'
import {
  isManualModuleRunTitle,
  MANUAL_MODULE_RUN_TITLE,
  vlmFinishedAt,
} from './useModuleTransactions'

function run(nodeStates: Record<string, unknown> | null, processingMode = 'AP'): WorkflowRun {
  return {
    id: 'r1',
    task_id: 't1',
    company_id: 'c1',
    processing_mode: processingMode,
    title: 'Run',
    run_status: 'awaiting_review',
    graph_json: { nodes: [], edges: [] },
    node_states_json: nodeStates,
    files: [],
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
  }
}

describe('vlmFinishedAt', () => {
  it('prefers the merge node finished_at', () => {
    const r = run({
      vlm: { status: 'completed', finished_at: '2026-06-01T10:00:00Z' },
      merge: { status: 'completed', finished_at: '2026-06-01T11:00:00Z' },
    })
    expect(vlmFinishedAt(r)).toBe('2026-06-01T11:00:00Z')
  })

  it('falls back to the vlm node when no merge node', () => {
    const r = run({ vlm: { status: 'completed', finished_at: '2026-06-01T10:00:00Z' } })
    expect(vlmFinishedAt(r)).toBe('2026-06-01T10:00:00Z')
  })

  it('uses the latest finished_at across nodes when neither vlm nor merge', () => {
    const r = run({
      table: { status: 'completed', finished_at: '2026-06-01T09:00:00Z' },
      save: { status: 'completed', finished_at: '2026-06-01T12:00:00Z' },
      ocr_by_file: { fileA: [{ vendor: 'X' }] },
    })
    expect(vlmFinishedAt(r)).toBe('2026-06-01T12:00:00Z')
  })

  it('returns null when no finished timestamps exist', () => {
    expect(vlmFinishedAt(run(null))).toBeNull()
    expect(vlmFinishedAt(run({ vlm: { status: 'running' } }))).toBeNull()
  })

  it('works for AR runs the same as AP', () => {
    const r = run(
      { merge: { status: 'completed', finished_at: '2026-06-02T08:00:00Z' } },
      'AR',
    )
    expect(r.processing_mode).toBe('AR')
    expect(vlmFinishedAt(r)).toBe('2026-06-02T08:00:00Z')
  })
})

describe('isManualModuleRunTitle', () => {
  it('matches the Books manual / CSV container title', () => {
    expect(isManualModuleRunTitle(MANUAL_MODULE_RUN_TITLE)).toBe(true)
    expect(isManualModuleRunTitle(`  ${MANUAL_MODULE_RUN_TITLE}  `)).toBe(true)
  })

  it('rejects ordinary Processing run titles', () => {
    expect(isManualModuleRunTitle('Untitled')).toBe(false)
    expect(isManualModuleRunTitle('AP invoices')).toBe(false)
    expect(isManualModuleRunTitle('')).toBe(false)
    expect(isManualModuleRunTitle(null)).toBe(false)
  })
})
