import { describe, expect, it } from 'vitest'
import type { SpreadsheetRow } from '../../components/EditableSpreadsheet'
import { mergeTablePayload, tablePayloadHasRows } from './tablePayloadMerge'
import type { WorkflowRun } from './workflowApi'

function makeRun(ocrByFile: Record<string, Record<string, unknown>[]>): WorkflowRun {
  return {
    id: 'run-1',
    task_id: 'task-1',
    company_id: 'co-1',
    processing_mode: 'AR',
    title: 'Test',
    run_status: 'awaiting_review',
    graph_json: { nodes: [], edges: [] },
    node_states_json: { ocr_by_file: ocrByFile },
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
    created_at: '',
    updated_at: '',
  }
}

describe('mergeTablePayload', () => {
  it('keeps file-a rows when merging file-b', () => {
    const run = makeRun({
      'file-a': [{ voucher_no: 'A1', amount: '10', date: '2022-01-01' }],
      'file-b': [{ voucher_no: 'B1', amount: '20', date: '2022-01-02' }],
    })
    const base = {
      spreadsheetData: [{ id: 'file-a-page1-r0', voucher_no: 'A1' }] as SpreadsheetRow[],
      arapTransactions: [{ id_number: 'A1' }],
      arapFilename: 'a.pdf',
      fileRefs: [{ id: 'file-a', name: 'a.pdf' }],
    }
    const incoming = {
      spreadsheetData: [{ id: 'file-b-page1-r0', voucher_no: 'B1' }] as SpreadsheetRow[],
      arapTransactions: [{ id_number: 'B1' }],
      arapFilename: 'b.pdf',
      fileRefs: [{ id: 'file-b', name: 'b.pdf' }],
    }
    const merged = mergeTablePayload(base, incoming, ['file-b'], run)
    const sheet = merged.spreadsheetData as SpreadsheetRow[]
    expect(sheet.some(r => String(r.id).startsWith('file-a-'))).toBe(true)
    expect(sheet.some(r => String(r.id).startsWith('file-b-'))).toBe(true)
    expect((merged.arapTransactions as unknown[]).length).toBeGreaterThanOrEqual(2)
  })

  it('replaces only re-processed file rows', () => {
    const run = makeRun({
      'file-a': [{ voucher_no: 'A2', amount: '99', date: '2022-01-01' }],
    })
    const base = {
      spreadsheetData: [
        { id: 'file-a-page1-r0', voucher_no: 'A1' },
        { id: 'file-b-page1-r0', voucher_no: 'B1' },
      ] as SpreadsheetRow[],
      arapTransactions: [{ id_number: 'A1' }, { id_number: 'B1' }],
      fileRefs: [
        { id: 'file-a', name: 'a.pdf' },
        { id: 'file-b', name: 'b.pdf' },
      ],
    }
    const incoming = {
      spreadsheetData: [{ id: 'file-a-page1-r0', voucher_no: 'A2' }] as SpreadsheetRow[],
      arapTransactions: [{ id_number: 'A2' }],
      fileRefs: [{ id: 'file-a', name: 'a.pdf' }],
    }
    const merged = mergeTablePayload(base, incoming, ['file-a'], run)
    const sheet = merged.spreadsheetData as SpreadsheetRow[]
    expect(sheet.filter(r => String(r.id).startsWith('file-b-'))).toHaveLength(1)
    expect(sheet.filter(r => String(r.id).startsWith('file-a-'))).toHaveLength(1)
  })

  it('preserves account_code from base arapTransactions when merging VLM rows', () => {
    const run = makeRun({
      'file-a': [{ voucher_no: 'A1', amount: '10', date: '2022-01-01' }],
    })
    const base = {
      spreadsheetData: [{ id: 'file-a-page1-r0', voucher_no: 'A1' }] as SpreadsheetRow[],
      arapTransactions: [{ id_number: 'AR-A1', account_code: '5100', category: 'Purchases' }],
      fileRefs: [{ id: 'file-a', name: 'a.pdf' }],
    }
    const incoming = {
      spreadsheetData: [{ id: 'file-a-page1-r0', voucher_no: 'A1' }] as SpreadsheetRow[],
      arapTransactions: [{ id_number: 'A1' }],
      fileRefs: [{ id: 'file-a', name: 'a.pdf' }],
    }
    const merged = mergeTablePayload(base, incoming, ['file-a'], run)
    const arap = merged.arapTransactions as { id_number?: string; account_code?: string; category?: string }[]
    expect(arap[0]?.account_code).toBe('5100')
    expect(arap[0]?.category).toBe('Purchases')
  })
})

describe('tablePayloadHasRows', () => {
  it('detects bank transactions', () => {
    expect(tablePayloadHasRows({ bankTransactions: [{ id_number: '1' }] }, 'BANK')).toBe(true)
    expect(tablePayloadHasRows({ arapTransactions: [] }, 'BANK')).toBe(false)
  })
})
