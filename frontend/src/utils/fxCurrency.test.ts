import { describe, expect, it } from 'vitest'
import {
  applySavedRateToRow,
  convertReceiptToCompany,
  formatCurrencyAmount,
  fxPairKey,
  impliedRateFromPrinted,
  normalizeCurrencyCode,
  rowReadyForBooks,
  sameCurrency,
} from './fxCurrency'

describe('fxCurrency', () => {
  it('maps JYP and HKD aliases to ISO codes', () => {
    expect(normalizeCurrencyCode('JYP')).toBe('JPY')
    expect(normalizeCurrencyCode('港元')).toBe('HKD')
    expect(normalizeCurrencyCode('HK$')).toBe('HKD')
    expect(normalizeCurrencyCode('')).toBe('')
    expect(normalizeCurrencyCode(null)).toBe('')
  })

  it('quantizes rate to 4 decimals and money to 2', () => {
    expect(convertReceiptToCompany(1900, 0.05)).toBe(95)
    expect(impliedRateFromPrinted(1900, 94.98)).toBe(0.05)
  })

  it('keeps printed company amount (B1) instead of recalc', () => {
    const row = applySavedRateToRow(
      { currency: 'JPY', amount: 1900, printed_company_amount: 94.98 },
      'HKD',
      0.05,
    ) as { company_amount?: number; company_currency?: string; exchange_rate?: number }
    expect(row.company_amount).toBe(94.98)
    expect(row.company_currency).toBe('HKD')
    expect(row.exchange_rate).toBe(0.05)
  })

  it('same currency copies amounts with rate 1', () => {
    const row = applySavedRateToRow({ currency: 'HKD', amount: 100, tax_amount: 5 }, 'HKD', null) as {
      company_amount?: number
      company_tax_amount?: number
      exchange_rate?: number
    }
    expect(row.company_amount).toBe(100)
    expect(row.company_tax_amount).toBe(5)
    expect(row.exchange_rate).toBe(1)
    expect(sameCurrency('港元', 'HKD')).toBe(true)
  })

  it('formats display amounts', () => {
    expect(formatCurrencyAmount('JPY', 1900)).toBe('JPY 1,900.00')
    expect(fxPairKey('jyp', 'hkd')).toBe('JPY:HKD')
  })

  it('rowReadyForBooks blocks missing company amount', () => {
    expect(rowReadyForBooks({ currency: 'JPY', amount: 1900 }, 'HKD')).toBe(false)
    expect(rowReadyForBooks({ currency: 'JPY', amount: 1900, company_currency: 'HKD', company_amount: 94.98 })).toBe(true)
    expect(rowReadyForBooks({ currency: 'HKD', amount: 100 }, 'HKD')).toBe(true)
    expect(rowReadyForBooks({ currency: 'JPY', amount: null }, 'HKD')).toBe(true)
  })
})
