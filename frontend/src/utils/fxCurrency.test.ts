import { describe, expect, it } from 'vitest'
import {
  applyManualCompanyOverride,
  applySavedRateToRow,
  convertReceiptToCompany,
  formatCurrencyAmount,
  fxPairKey,
  impliedRateFromPrinted,
  isMoneyDraft,
  normalizeCurrencyCode,
  parseMoney2dp,
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

  it('isMoneyDraft allows empty, digits, and two decimals', () => {
    expect(isMoneyDraft('')).toBe(true)
    expect(isMoneyDraft('80')).toBe(true)
    expect(isMoneyDraft('80.5')).toBe(true)
    expect(isMoneyDraft('80.50')).toBe(true)
    expect(isMoneyDraft('80.')).toBe(true)
    expect(isMoneyDraft('-12.3')).toBe(true)
    expect(isMoneyDraft('80.505')).toBe(false)
    expect(isMoneyDraft('A32')).toBe(false)
    expect(isMoneyDraft('32.0 3')).toBe(false)
    expect(isMoneyDraft('HKD 85.00')).toBe(false)
    expect(parseMoney2dp('')).toBeNull()
    expect(parseMoney2dp('80.5')).toBe(80.5)
    expect(parseMoney2dp('80')).toBe(80)
  })

  it('applyManualCompanyOverride keeps a typed amount and does not reapply the book rate', () => {
    const row = applyManualCompanyOverride(
      { currency: 'JPY', amount: 1700, company_currency: 'HKD', company_amount: 85, exchange_rate: 0.05 },
      { company_amount: 80 },
    )
    expect(row.company_amount).toBe(80)
    expect(row.company_currency).toBe('HKD')
    expect(row.exchange_rate).toBe(impliedRateFromPrinted(1700, 80))
    expect(row.exchange_rate).not.toBe(0.05)
  })

  it('applyManualCompanyOverride clears amount without restoring the previous number', () => {
    const row = applyManualCompanyOverride(
      { currency: 'JPY', amount: 1700, company_currency: 'HKD', company_amount: 85, exchange_rate: 0.05 },
      { company_amount: null },
    )
    expect(row.company_amount).toBeNull()
    expect(row.exchange_rate).toBeNull()
    expect(row.company_currency).toBe('HKD')
  })

  it('applyManualCompanyOverride changes code only and leaves the amount', () => {
    const row = applyManualCompanyOverride(
      { currency: 'JPY', amount: 1700, company_currency: 'HKD', company_amount: 85, exchange_rate: 0.05 },
      { company_currency: 'USD' },
    )
    expect(row.company_currency).toBe('USD')
    expect(row.company_amount).toBe(85)
    expect(row.exchange_rate).toBe(impliedRateFromPrinted(1700, 85))
  })
})
