import { describe, expect, it } from 'vitest'
import { applyDebitCreditSide, hydrateDebitCredit } from './arapDebitCredit'

describe('arapDebitCredit', () => {
  it('hydrates AP amount into Debit', () => {
    expect(hydrateDebitCredit({ amount: 100, transaction_type: 'AP' }, 'AP')).toEqual({
      debit: 100,
      credit: null,
      amount: 100,
      dr_cr: 'Dr',
    })
  })

  it('hydrates AR amount into Credit', () => {
    expect(hydrateDebitCredit({ amount: 50, transaction_type: 'AR' }, 'AR')).toEqual({
      debit: null,
      credit: 50,
      amount: 50,
      dr_cr: 'Cr',
    })
  })

  it('edit Credit clears Debit and sets dr_cr', () => {
    expect(applyDebitCreditSide('credit', 80)).toEqual({
      debit: null,
      credit: 80,
      amount: 80,
      dr_cr: 'Cr',
    })
  })

  it('honors stored dr_cr when hydrating amount-only', () => {
    expect(hydrateDebitCredit({ amount: 20, dr_cr: 'Cr', transaction_type: 'AP' }, 'AP')).toEqual({
      debit: null,
      credit: 20,
      amount: 20,
      dr_cr: 'Cr',
    })
  })
})
