import { describe, expect, it } from 'vitest'
import {
  bankAmountFromModuleTx,
  bankReconDedupKey,
  isModuleTxnLocked,
  ledgerReconDedupKey,
  ledgerVoucherFromModuleTx,
  normalizeReconCurrency,
  normalizeReconDate,
  selectKeepIdsForModuleSync,
  selectUnreconciledOrphanIds,
} from './moduleReconKeys'

describe('bankAmountFromModuleTx', () => {
  it('prefers deposit/withdrawal', () => {
    expect(bankAmountFromModuleTx({ deposit: 18000, withdrawal: 0 })).toBe(18000)
    expect(bankAmountFromModuleTx({ deposit: 0, withdrawal: 500 })).toBe(-500)
  })

  it('falls back to debit/credit when deposit/withdrawal absent', () => {
    expect(bankAmountFromModuleTx({ debit: 18000, credit: 0 })).toBe(18000)
    expect(bankAmountFromModuleTx({ debit: 0, credit: 389142 })).toBe(-389142)
  })
})

describe('isModuleTxnLocked', () => {
  it('locks when matched_id is set', () => {
    expect(isModuleTxnLocked({ matched_id: 'group-1' })).toBe(true)
    expect(isModuleTxnLocked({ matched_id: '' })).toBe(false)
    expect(isModuleTxnLocked({})).toBe(false)
  })
})

describe('selectUnreconciledOrphanIds', () => {
  it('purges unreconciled rows missing from modules', () => {
    const orphans = selectUnreconciledOrphanIds(
      [
        { id: 'keep', status: 'unreconciled', key: 'AP|V1|2025-01-02|10.00' },
        { id: 'gone', status: 'unreconciled', key: 'AP|OLD|2022-01-01|1.00' },
        { id: 'matched', status: 'matched', key: 'AP|OLD|2022-01-01|1.00' },
      ],
      new Set(['AP|V1|2025-01-02|10.00']),
    )
    expect(orphans).toEqual(['gone'])
  })

  it('purges duplicate unreconciled rows for the same module key', () => {
    const key = bankReconDedupKey('2025-12-12', 100, 'HKD CURRENT')
    const orphans = selectUnreconciledOrphanIds(
      [
        { id: 'a', status: 'unreconciled', key },
        { id: 'b', status: 'unreconciled', key },
      ],
      new Set([key]),
    )
    expect(orphans).toEqual(['b'])
  })

  it('does not purge when module key set is empty', () => {
    const orphans = selectUnreconciledOrphanIds(
      [{ id: 'a', status: 'unreconciled', key: 'AP|V1|2025-01-02|10.00' }],
      new Set(),
    )
    expect(orphans).toEqual([])
  })
})

describe('selectKeepIdsForModuleSync', () => {
  it('keeps unreconciled-in-module after cancel so rematch reuses the same id', () => {
    const key = 'AP|V1|2025-01-02|889.00'
    const kept = selectKeepIdsForModuleSync(
      [
        { id: 'just-cancelled', status: 'unreconciled', key },
        { id: 'orphan', status: 'unreconciled', key: 'AP|GONE|2020-01-01|1.00' },
        { id: 'still-matched', status: 'matched', key: 'AP|V2|2025-01-03|10.00' },
      ],
      new Set([key, 'AP|V2|2025-01-03|10.00']),
    )
    expect(kept).toEqual(['just-cancelled', 'still-matched'])
  })

  it('keeps only the first unreconciled duplicate for a module key', () => {
    const key = 'AP|V1|2025-01-02|10.00'
    const kept = selectKeepIdsForModuleSync(
      [
        { id: 'a', status: 'unreconciled', key },
        { id: 'b', status: 'unreconciled', key },
      ],
      new Set([key]),
    )
    expect(kept).toEqual(['a'])
  })
})

describe('ledgerVoucherFromModuleTx', () => {
  it('uses id_number when present', () => {
    expect(ledgerVoucherFromModuleTx({ id_number: 'AP-N10271', date: '2025-01-02', amount: 10 })).toBe(
      'AP-N10271',
    )
  })

  it('synthesizes voucher for manual Add Row without id', () => {
    const v = ledgerVoucherFromModuleTx({
      date: '2026-07-27',
      debit: 889,
      transaction_type: 'AP',
      upload_batch_id: 'batch-a',
      payee: 'SAMPLEPAYEE',
      manual_entry: true,
    })
    expect(v.startsWith('MANUAL-batch-a-2026-07-27-889.00')).toBe(true)
  })

  it('synthesizes a stable ref for blank-id row like the AP manual reconcile case', () => {
    const v = ledgerVoucherFromModuleTx({
      id_number: '',
      date: '2026-07-27',
      debit: 889,
      currency: 'HKD',
      source_file: 'SAMPLE-2411.pdf',
      upload_batch_id: 'batch-sample',
      manual_entry: true,
    })
    expect(v).toBe('MANUAL-batch-sample-2026-07-27-889.00-')
  })
})

describe('normalizeReconCurrency', () => {
  it('maps 港元 and HK$ to HKD', () => {
    expect(normalizeReconCurrency('港元')).toBe('HKD')
    expect(normalizeReconCurrency('HK$')).toBe('HKD')
    expect(normalizeReconCurrency('hkd')).toBe('HKD')
    expect(normalizeReconCurrency('USD')).toBe('USD')
  })
})

describe('normalizeReconDate', () => {
  it('unifies slash and ISO dates', () => {
    expect(normalizeReconDate('2025/1/2')).toBe('2025-01-02')
    expect(normalizeReconDate('2025-01-02T00:00:00')).toBe('2025-01-02')
    expect(normalizeReconDate('02/01/2025')).toBe('2025-01-02')
  })
})

describe('ledgerReconDedupKey', () => {
  it('normalizes module, date formats, and amount', () => {
    expect(ledgerReconDedupKey('ap', 'AP-1', '2025-01-02T00:00:00', 10)).toBe(
      'AP|AP-1|2025-01-02|10.00',
    )
    expect(ledgerReconDedupKey('AP', 'AP-1', '2025/01/02', 10)).toBe(
      ledgerReconDedupKey('AP', 'AP-1', '2025-01-02', 10),
    )
  })
})
