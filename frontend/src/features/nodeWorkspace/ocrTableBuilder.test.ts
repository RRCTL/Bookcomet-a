import { describe, expect, it } from 'vitest'
import { buildTablePayloadFromOcrByFile, getOcrByFileFromRun, rowsFromOcrPayload } from './ocrTableBuilder'
import type { WorkflowRun } from './workflowApi'

function makeRun(states: Record<string, unknown>, taskFileId = 'file-a'): WorkflowRun {
  return {
    id: 'run-1',
    task_id: 'task-1',
    company_id: 'co-1',
    processing_mode: 'AP',
    title: 'Test',
    run_status: 'awaiting_review',
    graph_json: { nodes: [], edges: [] },
    node_states_json: states,
    files: [
      {
        id: 'rf-1',
        task_file_id: taskFileId,
        file_status: 'ok',
        original_filename: 'receipt.pdf',
      },
    ],
    created_at: '',
    updated_at: '',
  }
}

describe('getOcrByFileFromRun', () => {
  it('uses per-file ocr_by_file when merged_ocr exists on linear path', () => {
    const row = { amount: '10.0', payee: 'SAMPLEPAYEE', date: '2022-01-08' }
    const run = makeRun({
      merged_ocr: [row],
      ocr_by_file: { 'file-a': [row] },
    })
    const ocr = getOcrByFileFromRun(run)
    expect(Object.keys(ocr)).toEqual(['file-a'])
    expect(ocr['file-a']).toHaveLength(1)
  })

  it('prefers per-file ocr_by_file over merge workflow blob', () => {
    const batch1 = { amount: '10.0', payee: 'Batch 1' }
    const batch2 = { amount: '20.0', payee: 'Batch 2' }
    const run: WorkflowRun = {
      ...makeRun({
        table_source: 'merge',
        merged_ocr: [batch2],
        ocr_by_file: {
          'file-a': [batch1],
          'file-b': [batch2],
        },
      }),
      files: [
        {
          id: 'rf-1',
          task_file_id: 'file-a',
          file_status: 'ok',
          original_filename: 'a.pdf',
        },
        {
          id: 'rf-2',
          task_file_id: 'file-b',
          file_status: 'ok',
          original_filename: 'b.pdf',
        },
      ],
    }
    const ocr = getOcrByFileFromRun(run)
    expect(Object.keys(ocr).sort()).toEqual(['file-a', 'file-b'])
  })

  it('uses workflow key when merge state has no per-file ocr', () => {
    const row = { amount: '10.0', payee: 'Vendor' }
    const run = makeRun({
      table_source: 'merge',
      merged_ocr: [row],
      ocr_by_file: { workflow: [row] },
    })
    const ocr = getOcrByFileFromRun(run)
    expect(Object.keys(ocr)).toEqual(['workflow'])
  })

  it('prefers manager-approved ocr_by_file on merge path over raw result_summary_json', () => {
    const cleaned = { amount: '99.0', payee: 'Manager Approved', date: '2024-01-01' }
    const stale = { amount: '10.0', payee: 'SAMPLEPAYEE' }
    const run = makeRun({
      table_source: 'merge',
      merged_ocr: [cleaned],
      ocr_by_file: { 'file-a': [cleaned] },
    })
    run.files[0]!.result_summary_json = { ai_enhanced: { tsv_rows: [stale] } }
    const ocr = getOcrByFileFromRun(run)
    expect(Object.keys(ocr)).toEqual(['file-a'])
    expect(ocr['file-a']?.[0]?.payee).toBe('Manager Approved')
  })

  it('falls back to result_summary_json when merge state is empty', () => {
    const row = { amount: '10.0', payee: 'SAMPLEPAYEE', date: '2022-01-08' }
    const run = makeRun({
      table_source: 'merge',
      merged_ocr: [],
      ocr_by_file: {},
    })
    run.files[0]!.result_summary_json = { ai_enhanced: { tsv_rows: [row] } }
    const ocr = getOcrByFileFromRun(run)
    expect(Object.keys(ocr)).toEqual(['file-a'])
    expect(ocr['file-a']?.[0]?.payee).toBe('SAMPLEPAYEE')
  })

  it('falls back to merged_ocr workflow key when merge ocr_by_file is absent', () => {
    const row = { amount: '10.0', payee: 'SAMPLEPAYEE' }
    const run = makeRun({ merged_ocr: [row] })
    const ocr = getOcrByFileFromRun(run)
    expect(Object.keys(ocr)).toEqual(['workflow'])
    expect(ocr.workflow).toHaveLength(1)
  })
})

describe('rowsFromOcrPayload', () => {
  it('stamps _page from multi-page payload', () => {
    const rows = rowsFromOcrPayload({
      pages: [
        { page: 2, ai_enhanced: { tsv_rows: [{ 存入: '100' }] } },
        { page: 5, ai_enhanced: { transactions: [{ 提取: '50' }] } },
      ],
    })
    expect(rows).toHaveLength(2)
    expect(rows[0]?._page).toBe(2)
    expect(rows[1]?._page).toBe(5)
  })

  it('stamps page image_quality onto rows missing provenance', () => {
    const iq = { enabled: true, status: 'recoverable', ui_label: 'Auto-enhanced' }
    const rows = rowsFromOcrPayload({
      pages: [
        {
          page: 1,
          image_quality: iq,
          ai_enhanced: { tsv_rows: [{ amount: '10', payee: 'Synthetic' }] },
        },
      ],
    })
    expect(rows).toHaveLength(1)
    const prov = rows[0]?.extraction_provenance as { image_quality?: { status?: string } }
    expect(prov?.image_quality?.status).toBe('recoverable')
  })
})

describe('buildTablePayloadFromOcrByFile BANK source page', () => {
  it('includes PDF page in bankTransactions source_file', () => {
    const run: WorkflowRun = {
      id: 'run-1',
      task_id: 'task-1',
      company_id: 'co-1',
      processing_mode: 'BANK',
      title: 'Bank',
      run_status: 'awaiting_review',
      graph_json: { nodes: [], edges: [] },
      node_states_json: {},
      files: [
        {
          id: 'rf-1',
          task_file_id: 'file-a',
          file_status: 'ok',
          original_filename: 'SAMPLE-2501.pdf',
          result_summary_json: {
            pages: [
              { page: 1, ai_enhanced: { tsv_rows: [{ 存入: '100', 賬戶類型: 'HKD CURRENT' }] } },
              { page: 4, ai_enhanced: { tsv_rows: [{ 提取: '50', 賬戶類型: 'HKD STATEMENT SAVINGS' }] } },
            ],
          },
        },
      ],
      created_at: '',
      updated_at: '',
    }
    const payload = buildTablePayloadFromOcrByFile(run)
    const bank = (payload.bankTransactions as { source_file?: string }[]) ?? []
    expect(bank[0]?.source_file).toBe('SAMPLE-2501.pdf P1')
    expect(bank[1]?.source_file).toBe('SAMPLE-2501.pdf P4')
  })

  it('prefers result_summary rows with _page over stale node state rows', () => {
    const run: WorkflowRun = {
      id: 'run-2',
      task_id: 'task-2',
      company_id: 'co-1',
      processing_mode: 'BANK',
      title: 'Bank',
      run_status: 'awaiting_review',
      graph_json: { nodes: [], edges: [] },
      node_states_json: {
        ocr_by_file: {
          'file-a': [{ 存入: '100', 賬戶類型: 'HKD CURRENT' }],
        },
      },
      files: [
        {
          id: 'rf-1',
          task_file_id: 'file-a',
          file_status: 'ok',
          original_filename: 'SAMPLE-2501.pdf',
          result_summary_json: {
            pages: [
              { page: 2, ai_enhanced: { tsv_rows: [{ 存入: '100', 賬戶類型: 'HKD CURRENT' }] } },
            ],
          },
        },
      ],
      created_at: '',
      updated_at: '',
    }
    const payload = buildTablePayloadFromOcrByFile(run)
    const bank = (payload.bankTransactions as { source_file?: string }[]) ?? []
    expect(bank[0]?.source_file).toBe('SAMPLE-2501.pdf P2')
  })

  it('forward-fills account_type from section headers into bankTransactions', () => {
    const run: WorkflowRun = {
      id: 'run-3',
      task_id: 'task-3',
      company_id: 'co-1',
      processing_mode: 'BANK',
      title: 'Bank',
      run_status: 'awaiting_review',
      graph_json: { nodes: [], edges: [] },
      node_states_json: {},
      files: [
        {
          id: 'rf-1',
          task_file_id: 'file-a',
          file_status: 'ok',
          original_filename: 'SAMPLE-2504.pdf',
          result_summary_json: {
            pages: [
              {
                page: 1,
                ai_enhanced: {
                  tsv_rows: [
                    { 賬戶類型: 'HKD STATEMENT SAVINGS', 存入: '100' },
                    { 存入: '200' },
                    { 賬戶類型: 'HKD CURRENT', 提取: '50' },
                    { 提取: '25' },
                  ],
                },
              },
            ],
          },
        },
      ],
      created_at: '',
      updated_at: '',
    }
    const payload = buildTablePayloadFromOcrByFile(run)
    const bank = (payload.bankTransactions as { account_type?: string }[]) ?? []
    expect(bank).toHaveLength(4)
    expect(bank[0]?.account_type).toBe('HKD STATEMENT SAVINGS')
    expect(bank[1]?.account_type).toBe('HKD STATEMENT SAVINGS')
    expect(bank[2]?.account_type).toBe('HKD CURRENT')
    expect(bank[3]?.account_type).toBe('HKD CURRENT')
  })
})
