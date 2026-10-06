import { describe, expect, it } from 'vitest'
import {
  RUN_ROW_FOREIGN_HIDDEN_NOTICE,
  RUN_ROW_MISMATCH_MESSAGE,
  filterPayloadRowsToRunFiles,
  partitionRowsByRunOwnership,
  rowBelongsToRunFiles,
} from './runReviewRowOwnership'

const runAFiles = [{ task_file_id: 'tf-a', original_filename: 'A.pdf' }]
const runBFiles = [
  { task_file_id: 'tf-b1', original_filename: 'Jan.pdf' },
  { task_file_id: 'tf-b2', original_filename: 'Feb.pdf' },
]

describe('rowBelongsToRunFiles', () => {
  it('matches by source_file stem to run filenames', () => {
    expect(rowBelongsToRunFiles({ source_file: 'A.pdf P1' }, runAFiles)).toBe(true)
    expect(rowBelongsToRunFiles({ source_file: 'Jan.pdf P3' }, runAFiles)).toBe(false)
    expect(rowBelongsToRunFiles({ source_file: 'Jan.pdf P3' }, runBFiles)).toBe(true)
  })

  it('matches explicit task_file_id', () => {
    expect(rowBelongsToRunFiles({ task_file_id: 'tf-b2', source_file: 'x.pdf' }, runBFiles)).toBe(
      true,
    )
    expect(rowBelongsToRunFiles({ task_file_id: 'tf-other' }, runBFiles)).toBe(false)
  })

  it('allows unmarked rows only on single-file runs', () => {
    expect(rowBelongsToRunFiles({ deposit: 10 }, runAFiles)).toBe(true)
    expect(rowBelongsToRunFiles({ deposit: 10 }, runBFiles)).toBe(false)
  })
})

describe('partitionRowsByRunOwnership', () => {
  it('flags foreign rows and keeps owned ones for the notice + grid', () => {
    const rows = [
      { source_file: 'A.pdf P1', deposit: 1 },
      { source_file: 'Jan.pdf P1', deposit: 2 },
    ]
    const part = partitionRowsByRunOwnership(rows, runAFiles)
    expect(part.ok).toBe(false)
    expect(part.foreignCount).toBe(1)
    expect(part.ownedRows).toHaveLength(1)
    expect(part.foreignRows[0]?.source_file).toBe('Jan.pdf P1')
    expect(RUN_ROW_FOREIGN_HIDDEN_NOTICE).toBe(
      "Some rows came from files that aren't in this run and are hidden. Reload the run to fix this.",
    )
    expect(RUN_ROW_MISMATCH_MESSAGE).toContain("don't belong")
  })

  it('is ok when every row belongs to the run', () => {
    const rows = [{ source_file: 'Jan.pdf P1' }, { source_file: 'Feb.pdf P2' }]
    expect(partitionRowsByRunOwnership(rows, runBFiles).ok).toBe(true)
  })
})

describe('filterPayloadRowsToRunFiles (approve / resume / export body)', () => {
  it('strips foreign bank rows so they cannot reach the backend', () => {
    const payload = {
      bankTransactions: [
        { source_file: 'A.pdf P1', deposit: 13 },
        { source_file: 'Jan.pdf P1', deposit: 100 },
        { source_file: 'Jan.pdf P2', deposit: 200 },
      ],
      bank: 'Synthetic Bank',
    }
    const { payload: scoped, foreignCount, ownedCount } = filterPayloadRowsToRunFiles(
      payload,
      runAFiles,
      'BANK',
    )
    expect(foreignCount).toBe(2)
    expect(ownedCount).toBe(1)
    const rows = scoped.bankTransactions as { source_file: string; deposit: number }[]
    expect(rows).toHaveLength(1)
    expect(rows[0]?.source_file).toBe('A.pdf P1')
    expect(rows.every(r => r.source_file.startsWith('A.pdf'))).toBe(true)
    // Original payload must not be mutated.
    expect((payload.bankTransactions as unknown[]).length).toBe(3)
  })

  it('approve/resume request body contains only the current run rows', () => {
    const stickyBleedPayload = {
      bankTransactions: [
        { source_file: 'Jan.pdf P1', deposit: 1 },
        { source_file: 'Jan.pdf P2', deposit: 2 },
        { source_file: 'A.pdf P1', deposit: 13 },
      ],
    }
    // User is on Run A (A.pdf only) but sticky state still holds Run B rows.
    const { payload: requestBody, foreignCount } = filterPayloadRowsToRunFiles(
      stickyBleedPayload,
      runAFiles,
      'BANK',
    )
    expect(foreignCount).toBe(2)
    const bodyRows = requestBody.bankTransactions as { source_file: string }[]
    expect(bodyRows).toEqual([{ source_file: 'A.pdf P1', deposit: 13 }])
  })

  it('filters arapTransactions for AP/AR modes', () => {
    const payload = {
      arapTransactions: [
        { source_file: 'receipt-a.pdf P1', amount: 10 },
        { source_file: 'other.pdf P1', amount: 99 },
      ],
    }
    const files = [{ task_file_id: 'tf1', original_filename: 'receipt-a.pdf' }]
    const { payload: scoped, foreignCount } = filterPayloadRowsToRunFiles(payload, files, 'AP')
    expect(foreignCount).toBe(1)
    expect(scoped.arapTransactions).toEqual([{ source_file: 'receipt-a.pdf P1', amount: 10 }])
  })
})
