import { describe, expect, it } from 'vitest'
import type { FlatRow } from './useModuleTransactions'
import {
  bankAccountPartitionKey,
  bankAccountTypeOf,
  bankBatchPartitionKey,
  bankBatchPartitionLabel,
  bankModuleSectionHeaders,
  bankReconStatusOf,
  bankSortGroupKey,
  deriveBankAccountOptions,
  deriveBankBatchOptions,
  filterBankModuleRows,
} from './bankModuleRowFilters'

function row(
  tx: Record<string, unknown>,
  key = 'r1',
  overrides: Partial<FlatRow> = {},
): FlatRow {
  return {
    key,
    runId: 'run-1',
    batchId: 'batch-1',
    runTitle: 'Test',
    vlmAt: null,
    runStatus: 'completed',
    taskId: 'task-1',
    fileId: null,
    filename: 'stmt.pdf',
    tx,
    ...overrides,
  }
}

describe('bankAccountTypeOf', () => {
  it('reads account_type and Chinese aliases', () => {
    expect(bankAccountTypeOf({ account_type: 'HKD CURRENT' })).toBe('HKD CURRENT')
    expect(bankAccountTypeOf({ 賬戶類型: 'HKD STATEMENT SAVINGS' })).toBe('HKD STATEMENT SAVINGS')
  })
})

describe('bankReconStatusOf', () => {
  it('classifies reconciled, open, and unreconciled', () => {
    expect(bankReconStatusOf({ matched_id: 'grp-1' })).toBe('reconciled')
    expect(bankReconStatusOf({ needs_review: true })).toBe('open')
    expect(bankReconStatusOf({})).toBe('unreconciled')
  })
})

describe('deriveBankAccountOptions', () => {
  it('returns sorted unique account types', () => {
    const options = deriveBankAccountOptions([
      row({ account_type: 'HKD CURRENT' }, 'a'),
      row({ 賬戶類型: 'HKD STATEMENT SAVINGS' }, 'b'),
      row({ account_type: 'HKD CURRENT' }, 'c'),
    ])
    expect(options).toEqual(['HKD CURRENT', 'HKD STATEMENT SAVINGS'])
  })
})

describe('batch partition helpers', () => {
  it('builds batch key and label from run + source file', () => {
    const r = row(
      { account_type: 'HKD CURRENT', source_file: 'HSBC_Jan.pdf P1' },
      'a',
      { runId: 'run-a', batchId: 'b1', runTitle: 'HSBC2501' },
    )
    expect(bankBatchPartitionKey(r)).toBe('run-a::b1')
    expect(bankBatchPartitionLabel(r)).toBe('HSBC2501 · HSBC_Jan.pdf')
  })

  it('derives sorted batch options', () => {
    const rows = [
      row({}, 'a', { runId: 'r2', batchId: 'b2', runTitle: 'OCBC 2025' }),
      row({}, 'b', { runId: 'r1', batchId: 'b1', runTitle: 'HSBC2501' }),
    ]
    const options = deriveBankBatchOptions(rows)
    expect(options).toHaveLength(2)
    expect(options.map(o => o.key)).toEqual(['r1::b1', 'r2::b2'])
  })

  it('uses distinct sort groups per batch and account', () => {
    const hsbc = row({ account_type: 'HKD CURRENT' }, 'a', { runId: 'r1', batchId: 'b1' })
    const ocbc = row({ account_type: 'HKD CURRENT' }, 'b', { runId: 'r2', batchId: 'b2' })
    expect(bankSortGroupKey(hsbc)).not.toBe(bankSortGroupKey(ocbc))
    expect(bankAccountPartitionKey(hsbc)).toBe('HKD CURRENT|')
  })

  it('emits file and account section headers on boundaries', () => {
    const first = row({ account_type: 'HKD CURRENT', account_number: '111' }, 'a', {
      runId: 'r1',
      batchId: 'b1',
      runTitle: 'HSBC2501',
    })
    const sameAccount = row({ account_type: 'HKD CURRENT', account_number: '111' }, 'b', {
      runId: 'r1',
      batchId: 'b1',
      runTitle: 'HSBC2501',
    })
    const newAccount = row({ account_type: 'HKD STATEMENT SAVINGS' }, 'c', {
      runId: 'r1',
      batchId: 'b1',
      runTitle: 'HSBC2501',
    })
    expect(bankModuleSectionHeaders(first, null).fileHeader).toBeTruthy()
    expect(bankModuleSectionHeaders(sameAccount, first).fileHeader).toBeNull()
    expect(bankModuleSectionHeaders(sameAccount, first).accountHeader).toBeNull()
    expect(bankModuleSectionHeaders(newAccount, sameAccount).accountHeader).toBeTruthy()
  })
})

describe('filterBankModuleRows', () => {
  const rows = [
    row(
      {
        account_type: 'HKD CURRENT',
        particulars: 'Wire In - Acme',
        date: '2026-06-14',
        matched_id: 'g1',
      },
      'a',
      { runId: 'run-hsbc', batchId: 'batch-hsbc', runTitle: 'HSBC2501' },
    ),
    row(
      {
        賬戶類型: 'HKD STATEMENT SAVINGS',
        description: 'Card Settlement',
        transaction_date: '2026-06-10',
        needs_review: true,
      },
      'b',
      { runId: 'run-ocbc', batchId: 'batch-ocbc', runTitle: 'OCBC 2025' },
    ),
    row(
      {
        account_type: 'HKD CURRENT',
        particulars: 'Rent',
        date: '2026-05-01',
      },
      'c',
      { runId: 'run-hsbc', batchId: 'batch-hsbc', runTitle: 'HSBC2501' },
    ),
  ]

  const baseFilters = {
    account: '',
    batch: '',
    description: '',
    status: 'all' as const,
    dateFrom: '',
    dateTo: '',
  }

  it('filters by account', () => {
    const out = filterBankModuleRows(rows, { ...baseFilters, account: 'HKD CURRENT' })
    expect(out.map(r => r.key)).toEqual(['a', 'c'])
  })

  it('filters by batch partition key', () => {
    const out = filterBankModuleRows(rows, {
      ...baseFilters,
      batch: 'run-ocbc::batch-ocbc',
    })
    expect(out.map(r => r.key)).toEqual(['b'])
  })

  it('filters by description substring', () => {
    const out = filterBankModuleRows(rows, { ...baseFilters, description: 'card' })
    expect(out.map(r => r.key)).toEqual(['b'])
  })

  it('filters by recon status', () => {
    const reconciled = filterBankModuleRows(rows, { ...baseFilters, status: 'reconciled' })
    expect(reconciled.map(r => r.key)).toEqual(['a'])

    const open = filterBankModuleRows(rows, { ...baseFilters, status: 'open' })
    expect(open.map(r => r.key)).toEqual(['b'])
  })

  it('filters by inclusive date range', () => {
    const out = filterBankModuleRows(rows, {
      ...baseFilters,
      dateFrom: '2026-06-01',
      dateTo: '2026-06-14',
    })
    expect(out.map(r => r.key)).toEqual(['a', 'b'])
  })
})
