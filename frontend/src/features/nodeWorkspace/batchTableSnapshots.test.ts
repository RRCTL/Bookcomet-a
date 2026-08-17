import { describe, expect, it } from 'vitest'
import {
  batchSnapshotMessageId,
  batchPayloadsFromApprovedRun,
  batchesMissingTableRows,
  buildBatchTablePayloadsFromRun,
  combineBatchTablePayloads,
  frozenPresetForBatch,
  mapCombinedPayloadToBatches,
  mergeBatchTablePayloads,
  moveFileRowsBetweenBatches,
  moveRowsBetweenBatches,
  preferModuleAuthoritativeBatches,
  resolveBatchTablePayloadAfterVlm,
  reconcileBatchPayloadsWithRun,
  runHasLockedApprovedTable,
  tablePresetLabel,
} from './batchTableSnapshots'
import type { WorkflowRun } from './workflowApi'

function mockRun(files: WorkflowRun['files']): WorkflowRun {
  return {
    id: 'run-1',
    task_id: 'task-1',
    company_id: 'co-1',
    processing_mode: 'AP',
    title: 'Test',
    run_status: 'awaiting_review',
    graph_json: { nodes: [], edges: [] },
    files,
    created_at: '',
    updated_at: '',
  }
}

describe('batchSnapshotMessageId', () => {
  it('uses deterministic ocr-batch prefix', () => {
    expect(batchSnapshotMessageId('batch-a')).toBe('ocr-batch-batch-a')
  })
})

describe('resolveBatchTablePayloadAfterVlm', () => {
  it('builds batch table from merged_ocr workflow fallback', () => {
    const row = {
      voucher_no: 'AG000504564HK',
      transaction_type: 'AP',
      amount: '10.0',
      currency: 'HKD',
      date: '2022-01-08',
      payee: 'SAMPLEPAYEE',
    }
    const run: WorkflowRun = {
      ...mockRun([
        {
          id: 'rf-1',
          task_file_id: '510a9197-a78e-43ee-946e-8f3d6cbad250',
          file_status: 'ok',
          upload_batch_id: '90b2c82f-f79c-4142-b70d-9b9ee37edf59',
          batch_committed_at: '2026-06-05T08:56:16.316175Z',
          batch_table_preset: 'ap_table',
          original_filename: 'receipt.pdf',
        },
      ]),
      node_states_json: { merged_ocr: [row] },
    }
    const payload = resolveBatchTablePayloadAfterVlm(
      run,
      '90b2c82f-f79c-4142-b70d-9b9ee37edf59',
      {},
      ['510a9197-a78e-43ee-946e-8f3d6cbad250'],
    )
    expect((payload.arapTransactions as unknown[] | undefined)?.length).toBe(1)
    expect(buildBatchTablePayloadsFromRun(run)['90b2c82f-f79c-4142-b70d-9b9ee37edf59']).toBeDefined()
  })
})

describe('frozenPresetForBatch', () => {
  it('reads frozen preset from run files', () => {
    const run = mockRun([
      {
        id: 'rf-1',
        task_file_id: 'tf-1',
        file_status: 'ok',
        upload_batch_id: 'b1',
        batch_committed_at: '2026-01-01T00:00:00Z',
        batch_table_preset: 'ap_table',
      },
    ])
    expect(frozenPresetForBatch(run, 'b1')).toBe('ap_table')
  })
})

describe('tablePresetLabel', () => {
  it('labels AR default preset', () => {
    expect(tablePresetLabel('default', 'AR')).toBe('Standard AR columns')
  })

  it('labels AP ap_table preset', () => {
    expect(tablePresetLabel('ap_table', 'AP')).toBe('AP table')
  })
})

describe('combineBatchTablePayloads AR', () => {
  it('merges arapTransactions from committed AR batches', () => {
    const run: WorkflowRun = {
      ...mockRun([
        {
          id: 'rf-1',
          task_file_id: 'f1',
          file_status: 'ok',
          upload_batch_id: 'b1',
          uploaded_at: '2026-01-01T00:00:00Z',
          batch_committed_at: '2026-01-01T00:00:00Z',
        },
      ]),
      processing_mode: 'AR',
    }
    const combined = combineBatchTablePayloads(
      {
        b1: {
          spreadsheetData: [{ id: 'f1-r1' }],
          arapTransactions: [{ id_number: 'AR-1', transaction_type: 'AR', amount: 100 }],
        },
      },
      run,
    )
    expect((combined.arapTransactions as unknown[]).length).toBe(1)
  })
})

describe('moveRowsBetweenBatches', () => {
  it('moves spreadsheet rows and rebuilds arap for target preset', () => {
    const run = mockRun([
      {
        id: 'rf-1',
        task_file_id: 'f1',
        file_status: 'ok',
        upload_batch_id: 'a',
        batch_committed_at: '2026-01-01T00:00:00Z',
        batch_table_preset: 'default',
      },
      {
        id: 'rf-2',
        task_file_id: 'f2',
        file_status: 'ok',
        upload_batch_id: 'b',
        batch_committed_at: '2026-01-01T00:00:00Z',
        batch_table_preset: 'ap_table',
      },
    ])
    const payloads = {
      a: {
        spreadsheetData: [{ id: 'f1-row1', amount: 10 }],
        arapTransactions: [{ amount: 10, transaction_type: 'AP' }],
        apVlmTablePreset: 'default',
      },
      b: {
        spreadsheetData: [],
        arapTransactions: [],
        apVlmTablePreset: 'ap_table',
      },
    }
    const next = moveRowsBetweenBatches('a', 'b', ['f1-row1'], payloads, run)
    expect((next.a?.spreadsheetData as unknown[]).length).toBe(0)
    expect((next.b?.spreadsheetData as unknown[]).length).toBe(1)
    expect(next.b?.apVlmTablePreset).toBe('ap_table')
  })
})

describe('moveFileRowsBetweenBatches', () => {
  it('moves all rows for a file id prefix', () => {
    const run = mockRun([
      {
        id: 'rf-1',
        task_file_id: 'f1',
        file_status: 'ok',
        upload_batch_id: 'a',
        batch_committed_at: '2026-01-01T00:00:00Z',
      },
      {
        id: 'rf-2',
        task_file_id: 'f2',
        file_status: 'ok',
        upload_batch_id: 'b',
        batch_committed_at: '2026-01-01T00:00:00Z',
      },
    ])
    const payloads = {
      a: {
        spreadsheetData: [
          { id: 'f1-row1', amount: 1 },
          { id: 'f1-row2', amount: 2 },
          { id: 'f2-row1', amount: 3 },
        ],
        arapTransactions: [],
      },
      b: { spreadsheetData: [], arapTransactions: [] },
    }
    const { payloads: next } = moveFileRowsBetweenBatches('a', 'b', 'f1', payloads, run)
    expect((next.a?.spreadsheetData as { id: string }[]).map(r => r.id)).toEqual(['f2-row1'])
    expect((next.b?.spreadsheetData as unknown[]).length).toBe(2)
  })

  it('moves arap-only rows when spreadsheet is empty', () => {
    const run = mockRun([
      {
        id: 'rf-1',
        task_file_id: 'f1',
        original_filename: 'receipt.pdf',
        file_status: 'ok',
        upload_batch_id: 'a',
        batch_committed_at: '2026-01-01T00:00:00Z',
      },
      {
        id: 'rf-2',
        task_file_id: 'f2',
        file_status: 'ok',
        upload_batch_id: 'b',
        batch_committed_at: '2026-01-01T00:00:00Z',
      },
    ])
    const payloads = {
      a: {
        spreadsheetData: [],
        arapTransactions: [
          { amount: 1, transaction_type: 'AP', source_file: 'receipt.pdf' },
          { amount: 2, transaction_type: 'AP', source_file: 'other.pdf' },
        ],
      },
      b: { spreadsheetData: [], arapTransactions: [] },
    }
    const { payloads: next, moved } = moveFileRowsBetweenBatches('a', 'b', 'f1', payloads, run)
    expect(moved).toBe(1)
    expect((next.a?.arapTransactions as unknown[]).length).toBe(1)
    expect((next.b?.arapTransactions as unknown[]).length).toBe(1)
  })

  it('matches arap rows via file_position when source_file is empty', () => {
    const run = mockRun([
      {
        id: 'rf-1',
        task_file_id: 'f1',
        original_filename: 'receipt.pdf',
        file_status: 'ok',
        upload_batch_id: 'a',
        batch_committed_at: '2026-01-01T00:00:00Z',
      },
      {
        id: 'rf-2',
        task_file_id: 'f2',
        file_status: 'ok',
        upload_batch_id: 'b',
        batch_committed_at: '2026-01-01T00:00:00Z',
      },
    ])
    const payloads = {
      a: {
        spreadsheetData: [],
        arapTransactions: [
          { amount: 1, transaction_type: 'AP', file_position: 'receipt.pdf P1' },
          { amount: 2, transaction_type: 'AP', file_position: 'other.pdf P1' },
        ],
      },
      b: { spreadsheetData: [], arapTransactions: [] },
    }
    const { payloads: next, moved } = moveFileRowsBetweenBatches('a', 'b', 'f1', payloads, run)
    expect(moved).toBe(1)
    expect((next.b?.arapTransactions as { amount: number }[])[0]?.amount).toBe(1)
  })

  it('prefers arap rows over spreadsheet regeneration when both exist', () => {
    const run = mockRun([
      {
        id: 'rf-1',
        task_file_id: 'f1',
        original_filename: 'receipt.pdf',
        file_status: 'ok',
        upload_batch_id: 'a',
        batch_committed_at: '2026-01-01T00:00:00Z',
      },
      {
        id: 'rf-2',
        task_file_id: 'f2',
        file_status: 'ok',
        upload_batch_id: 'b',
        batch_committed_at: '2026-01-01T00:00:00Z',
      },
    ])
    const payloads = {
      a: {
        spreadsheetData: [{ id: 'f1-row1', amount: '1' }],
        arapTransactions: [
          {
            amount: 99,
            transaction_type: 'AP',
            source_file: 'receipt.pdf',
            memo: 'keep-rich-arap',
          },
        ],
      },
      b: { spreadsheetData: [], arapTransactions: [] },
    }
    const { payloads: next, moved } = moveFileRowsBetweenBatches('a', 'b', 'f1', payloads, run)
    expect(moved).toBe(1)
    expect((next.a?.arapTransactions as unknown[]).length).toBe(0)
    const targetArap = next.b?.arapTransactions as { amount: number; memo: string }[]
    expect(targetArap.length).toBe(1)
    expect(targetArap[0]?.amount).toBe(99)
    expect(targetArap[0]?.memo).toBe('keep-rich-arap')
    expect((next.b?.spreadsheetData as unknown[]).length).toBe(1)
  })

  it('preserves rich arap when moving spreadsheet rows by row id', () => {
    const run = mockRun([
      {
        id: 'rf-1',
        task_file_id: 'f1',
        file_status: 'ok',
        upload_batch_id: 'a',
        batch_committed_at: '2026-01-01T00:00:00Z',
      },
      {
        id: 'rf-2',
        task_file_id: 'f2',
        file_status: 'ok',
        upload_batch_id: 'b',
        batch_committed_at: '2026-01-01T00:00:00Z',
      },
    ])
    const payloads = {
      a: {
        spreadsheetData: [{ id: 'f1-row1', amount: '1' }],
        arapTransactions: [{ id: 'f1-row1', amount: 42, transaction_type: 'AP', memo: 'keep' }],
      },
      b: { spreadsheetData: [], arapTransactions: [] },
    }
    const next = moveRowsBetweenBatches('a', 'b', ['f1-row1'], payloads, run)
    const targetArap = next.b?.arapTransactions as { amount: number; memo: string }[]
    expect(targetArap.length).toBe(1)
    expect(targetArap[0]?.amount).toBe(42)
    expect(targetArap[0]?.memo).toBe('keep')
  })
})

describe('mapCombinedPayloadToBatches', () => {
  it('maps combined payload to upload_batch_id for a single batch', () => {
    const run = mockRun([
      {
        id: 'rf-1',
        task_file_id: 'f1',
        file_status: 'ok',
        upload_batch_id: 'batch-abc',
        batch_committed_at: '2026-01-01T00:00:00Z',
        batch_table_preset: 'ap_table',
      },
    ])
    const payload = {
      spreadsheetData: [{ id: 'f1-r1' }],
      arapTransactions: [{ amount: 1, transaction_type: 'AP' }],
    }
    const mapped = mapCombinedPayloadToBatches(run, payload)
    expect(Object.keys(mapped)).toEqual(['batch-abc'])
    expect(mapped['batch-abc']?.apVlmTablePreset).toBe('ap_table')
    expect((mapped['batch-abc']?.arapTransactions as unknown[]).length).toBe(1)
  })

  it('splits combined payload across multiple batches by file id', () => {
    const run = mockRun([
      {
        id: 'rf-1',
        task_file_id: 'f1',
        file_status: 'ok',
        upload_batch_id: 'batch-a',
        batch_committed_at: '2026-01-01T00:00:00Z',
      },
      {
        id: 'rf-2',
        task_file_id: 'f2',
        file_status: 'ok',
        upload_batch_id: 'batch-b',
        batch_committed_at: '2026-01-02T00:00:00Z',
      },
    ])
    const payload = {
      spreadsheetData: [
        { id: 'f1-r1', amount: 1 },
        { id: 'f2-r1', amount: 2 },
      ],
      arapTransactions: [
        { amount: 1, transaction_type: 'AP', source_file: 'a.pdf' },
        { amount: 2, transaction_type: 'AP', source_file: 'b.pdf' },
      ],
    }
    const mapped = mapCombinedPayloadToBatches(run, payload)
    expect((mapped['batch-a']?.spreadsheetData as unknown[]).length).toBe(1)
    expect((mapped['batch-b']?.spreadsheetData as unknown[]).length).toBe(1)
  })

  it('keeps multi-batch manual Add Rows via upload_batch_id', () => {
    const run = mockRun([
      {
        id: 'rf-1',
        task_file_id: 'f1',
        original_filename: 'a.pdf',
        file_status: 'ok',
        upload_batch_id: 'batch-a',
        batch_committed_at: '2026-01-01T00:00:00Z',
      },
      {
        id: 'rf-2',
        task_file_id: 'f2',
        original_filename: 'b.pdf',
        file_status: 'ok',
        upload_batch_id: 'batch-b',
        batch_committed_at: '2026-01-02T00:00:00Z',
      },
    ])
    const payload = {
      spreadsheetData: [],
      arapTransactions: [
        { amount: 1, transaction_type: 'AP', source_file: 'a.pdf' },
        { amount: 2, transaction_type: 'AP', source_file: 'b.pdf' },
        {
          amount: 50,
          transaction_type: 'AP',
          source_file: '',
          manual_entry: true,
          upload_batch_id: 'batch-b',
        },
      ],
    }
    const mapped = mapCombinedPayloadToBatches(run, payload)
    expect((mapped['batch-a']?.arapTransactions as unknown[]).length).toBe(1)
    const batchB = mapped['batch-b']?.arapTransactions as { manual_entry?: boolean; amount?: number }[]
    expect(batchB.length).toBe(2)
    expect(batchB.some(r => r.manual_entry === true && r.amount === 50)).toBe(true)
  })
})

describe('mergeBatchTablePayloads', () => {
  it('fills missing batches from fallback payloads', () => {
    const run = mockRun([
      {
        id: 'rf-1',
        task_file_id: 'f1',
        file_status: 'ok',
        upload_batch_id: 'batch-a',
        batch_committed_at: '2026-01-01T00:00:00Z',
      },
      {
        id: 'rf-2',
        task_file_id: 'f2',
        file_status: 'ok',
        upload_batch_id: 'batch-b',
        batch_committed_at: '2026-01-02T00:00:00Z',
      },
    ])
    const primary = {
      'batch-a': { arapTransactions: [{ amount: 1, transaction_type: 'AP' }] },
    }
    const fallback = {
      'batch-b': { arapTransactions: [{ amount: 2, transaction_type: 'AP' }] },
    }
    const merged = mergeBatchTablePayloads(run, primary, fallback)
    expect(batchesMissingTableRows(run, merged)).toBe(false)
    expect((merged['batch-a']?.arapTransactions as unknown[]).length).toBe(1)
    expect((merged['batch-b']?.arapTransactions as unknown[]).length).toBe(1)
  })
})

describe('combineBatchTablePayloads', () => {
  it('merges rows from multiple batches in upload order', () => {
    const run = mockRun([
      {
        id: 'rf-1',
        task_file_id: 'f1',
        file_status: 'ok',
        upload_batch_id: 'a',
        uploaded_at: '2026-01-01T00:00:00Z',
        batch_committed_at: '2026-01-01T00:00:00Z',
      },
      {
        id: 'rf-2',
        task_file_id: 'f2',
        file_status: 'ok',
        upload_batch_id: 'b',
        uploaded_at: '2026-01-02T00:00:00Z',
        batch_committed_at: '2026-01-02T00:00:00Z',
      },
    ])
    const combined = combineBatchTablePayloads(
      {
        a: {
          spreadsheetData: [{ id: 'f1-r1' }],
          arapTransactions: [{ amount: 1, transaction_type: 'AP' }],
        },
        b: {
          spreadsheetData: [{ id: 'f2-r1' }],
          arapTransactions: [{ amount: 2, transaction_type: 'AP' }],
        },
      },
      run,
    )
    expect((combined.spreadsheetData as unknown[]).length).toBe(2)
    expect((combined.arapTransactions as unknown[]).length).toBe(2)
  })
})

describe('preferModuleAuthoritativeBatches', () => {
  it('keeps module-saved Add Row over frozen approved_payload (AP)', () => {
    const approved = {
      'batch-a': {
        arapTransactions: [
          { id_number: 'AP-1', amount: 100, transaction_type: 'AP', source_file: 'a.pdf' },
        ],
      },
    }
    const snapshots = {
      'batch-a': {
        moduleSavedAt: '2026-07-27T03:00:00.000Z',
        arapTransactions: [
          { id_number: 'AP-1', amount: 100, transaction_type: 'AP', source_file: 'a.pdf' },
          {
            id_number: '',
            amount: 50,
            transaction_type: 'AP',
            source_file: '',
            manual_entry: true,
            upload_batch_id: 'batch-a',
          },
        ],
      },
    }
    const out = preferModuleAuthoritativeBatches(approved, snapshots)
    const rows = out['batch-a']?.arapTransactions as { manual_entry?: boolean }[]
    expect(rows.length).toBe(2)
    expect(rows[1]?.manual_entry).toBe(true)
  })

  it('keeps module-saved Add Row over frozen approved_payload (AR)', () => {
    const approved = {
      'batch-a': {
        arapTransactions: [
          { id_number: 'AR-1', amount: 200, transaction_type: 'AR', source_file: 'inv.pdf' },
        ],
      },
    }
    const snapshots = {
      'batch-a': {
        moduleSavedAt: '2026-07-27T03:00:00.000Z',
        arapTransactions: [
          { id_number: 'AR-1', amount: 200, transaction_type: 'AR', source_file: 'inv.pdf' },
          {
            id_number: '',
            amount: 80,
            transaction_type: 'AR',
            source_file: '',
            manual_entry: true,
            upload_batch_id: 'batch-a',
          },
        ],
      },
    }
    const out = preferModuleAuthoritativeBatches(approved, snapshots)
    expect((out['batch-a']?.arapTransactions as unknown[]).length).toBe(2)
  })

  it('keeps module-saved Add Row over frozen approved_payload (BANK)', () => {
    const approved = {
      'batch-a': {
        bankTransactions: [{ source_file: 'stmt.pdf', deposit: 100 }],
      },
    }
    const snapshots = {
      'batch-a': {
        moduleSavedAt: '2026-07-27T03:00:00.000Z',
        bankTransactions: [
          { source_file: 'stmt.pdf', deposit: 100 },
          {
            source_file: '',
            deposit: 25,
            manual_entry: true,
            upload_batch_id: 'batch-a',
          },
        ],
      },
    }
    const out = preferModuleAuthoritativeBatches(approved, snapshots)
    expect((out['batch-a']?.bankTransactions as unknown[]).length).toBe(2)
  })

  it('does not overlay OCR snapshots without moduleSavedAt', () => {
    const approved = {
      'batch-a': {
        arapTransactions: [{ id_number: 'AP-1', amount: 100, transaction_type: 'AP' }],
      },
    }
    const snapshots = {
      'batch-a': {
        arapTransactions: [
          { id_number: 'AP-1', amount: 100, transaction_type: 'AP' },
          { id_number: 'AP-stale', amount: 9, transaction_type: 'AP' },
        ],
      },
    }
    const out = preferModuleAuthoritativeBatches(approved, snapshots)
    expect((out['batch-a']?.arapTransactions as unknown[]).length).toBe(1)
  })
})

describe('reconcileBatchPayloadsWithRun', () => {
  it('does not replace module-saved batch snapshots when VLM has more rows', () => {
    const run = mockRun([
      {
        id: 'rf-1',
        task_file_id: 'f1',
        file_status: 'ok',
        upload_batch_id: 'batch-a',
        batch_committed_at: '2026-01-01T00:00:00Z',
        result_summary_json: {
          tsv_rows: [
            { id_number: 'AP-1', amount: 1, transaction_type: 'AP' },
            { id_number: 'AP-2', amount: 2, transaction_type: 'AP' },
          ],
        },
      },
    ])
    run.node_states_json = {
      ocr_by_file: {
        f1: [
          { id_number: 'AP-1', amount: 1, transaction_type: 'AP' },
          { id_number: 'AP-2', amount: 2, transaction_type: 'AP' },
        ],
      },
    }
    const loaded = {
      'batch-a': {
        moduleSavedAt: '2026-06-19T12:00:00.000Z',
        arapTransactions: [{ id_number: 'AP-1', amount: 1, account_code: '5100', transaction_type: 'AP' }],
      },
    }
    const out = reconcileBatchPayloadsWithRun(run, loaded)
    expect((out['batch-a']?.arapTransactions as { account_code?: string }[])?.[0]?.account_code).toBe('5100')
    expect((out['batch-a']?.arapTransactions as unknown[])?.length).toBe(1)
  })

  it('replaces stale BANK snapshot when rebuilt rows have source page labels', () => {
    const run = mockRun([
      {
        id: 'rf-1',
        task_file_id: 'f1',
        file_status: 'ok',
        upload_batch_id: 'batch-a',
        batch_committed_at: '2026-01-01T00:00:00Z',
        original_filename: 'SAMPLE-2501.pdf',
        result_summary_json: {
          pages: [
            { page: 1, ai_enhanced: { tsv_rows: [{ 存入: '100', 賬戶類型: 'HKD CURRENT' }] } },
            { page: 3, ai_enhanced: { tsv_rows: [{ 提取: '50', 賬戶類型: 'HKD CURRENT' }] } },
          ],
        },
      },
    ])
    run.processing_mode = 'BANK'
    run.node_states_json = {
      ocr_by_file: {
        f1: [
          { 存入: '100', 賬戶類型: 'HKD CURRENT', _page: 1 },
          { 提取: '50', 賬戶類型: 'HKD CURRENT', _page: 3 },
        ],
      },
    }
    const loaded = {
      'batch-a': {
        bankTransactions: [
          { source_file: 'SAMPLE-2501.pdf', deposit: 100 },
          { source_file: 'SAMPLE-2501.pdf', withdrawal: 50 },
        ],
      },
    }
    const out = reconcileBatchPayloadsWithRun(run, loaded)
    const bank = (out['batch-a']?.bankTransactions as { source_file?: string }[]) ?? []
    expect(bank[0]?.source_file).toBe('SAMPLE-2501.pdf P1')
    expect(bank[1]?.source_file).toBe('SAMPLE-2501.pdf P3')
  })

  it('does not restore OCR rows over approved payload with user deletions', () => {
    const run = mockRun([
      {
        id: 'rf-1',
        task_file_id: 'f1',
        file_status: 'ok',
        upload_batch_id: 'batch-a',
        batch_committed_at: '2026-01-01T00:00:00Z',
        original_filename: 'SAMPLE-2501.pdf',
        result_summary_json: {
          pages: [
            { page: 1, ai_enhanced: { tsv_rows: [{ 存入: '100', 賬戶類型: 'HKD CURRENT' }] } },
            { page: 2, ai_enhanced: { tsv_rows: [{ 存入: '200', 賬戶類型: 'HKD CURRENT' }] } },
          ],
        },
      },
    ])
    run.processing_mode = 'BANK'
    run.run_status = 'completed'
    run.node_states_json = {
      ocr_by_file: {
        f1: [
          { 存入: '100', 賬戶類型: 'HKD CURRENT', _page: 1 },
          { 存入: '200', 賬戶類型: 'HKD CURRENT', _page: 2 },
        ],
      },
      approved_payload: {
        bankTransactions: [{ 存入: '100', 賬戶類型: 'HKD CURRENT', source_file: 'SAMPLE-2501.pdf P1' }],
      },
    }
    expect(runHasLockedApprovedTable(run)).toBe(true)
    const approved = batchPayloadsFromApprovedRun(run)
    expect((approved?.['batch-a']?.bankTransactions as unknown[])?.length).toBe(1)
    const loaded = approved!
    const out = reconcileBatchPayloadsWithRun(run, loaded)
    expect((out['batch-a']?.bankTransactions as unknown[])?.length).toBe(1)
  })
})
