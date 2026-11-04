import { describe, expect, it } from 'vitest'
import { accountCategoryUpdatesForExistingRows } from './accountCategoryUpdates'

const LEDGER_KEY = 'AP|V-1|2026-04-10|142.00'
const BANK_KEY = '2026-04-11|100.00|hsbc'

describe('accountCategoryUpdatesForExistingRows', () => {
  it('emits a ledger update when account_code differs from the stored category', () => {
    const updates = accountCategoryUpdatesForExistingRows({
      source: 'ledger',
      moduleRows: [{ key: LEDGER_KEY, account_code: '5000' }],
      dbRows: [{ id: 'lt-1', key: LEDGER_KEY, account_category: '2100' }],
    })
    expect(updates).toEqual([
      { source: 'ledger', txn_id: 'lt-1', account_category: '5000' },
    ])
  })

  it('skips when the CoA code is unchanged', () => {
    const updates = accountCategoryUpdatesForExistingRows({
      source: 'ledger',
      moduleRows: [{ key: LEDGER_KEY, account_code: '2100' }],
      dbRows: [{ id: 'lt-1', key: LEDGER_KEY, account_category: '2100' }],
    })
    expect(updates).toEqual([])
  })

  it('skips reconciled / locked module rows', () => {
    const updates = accountCategoryUpdatesForExistingRows({
      source: 'ledger',
      moduleRows: [{ key: LEDGER_KEY, account_code: '5000', locked: true }],
      dbRows: [{ id: 'lt-1', key: LEDGER_KEY, account_category: '2100' }],
    })
    expect(updates).toEqual([])
  })

  it('skips posted txn ids and rows with no matching db id', () => {
    const updates = accountCategoryUpdatesForExistingRows({
      source: 'bank',
      moduleRows: [
        { key: BANK_KEY, account_code: '1000' },
        { key: '2026-04-11|9.00|missing', account_code: '1000' },
      ],
      dbRows: [{ id: 'bt-1', key: BANK_KEY, account_category: '1010' }],
      postedIds: new Set(['bt-1']),
    })
    expect(updates).toEqual([])
  })
})
