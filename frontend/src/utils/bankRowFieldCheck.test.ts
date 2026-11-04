import { describe, expect, it } from 'vitest'
import {
  BANK_ROW_CHECK_MESSAGE,
  bankRowNeedsPlacementCheck,
  bankRowPlacementIssues,
  countRowsNeedingPlacementCheck,
  isValidBankCurrencyCode,
  parseBankReviewDate,
  placementCheckBannerText,
} from './bankRowFieldCheck'

describe('bankRowFieldCheck', () => {
  it('parses DDMMMYY and ISO dates', () => {
    expect(parseBankReviewDate('05AUG20')).toBe('2020-08-05')
    expect(parseBankReviewDate('2020-08-11')).toBe('2020-08-11')
    expect(parseBankReviewDate('')).toBe('')
    expect(parseBankReviewDate('nope')).toBe('')
  })

  it('validates currency codes and rejects dates jammed into currency', () => {
    expect(isValidBankCurrencyCode('HKD')).toBe(true)
    expect(isValidBankCurrencyCode('港元')).toBe(true)
    expect(isValidBankCurrencyCode('2020-08-11')).toBe(false)
    expect(isValidBankCurrencyCode('AUG')).toBe(false)
  })

  it('flags empty date and misplaced currency', () => {
    const issues = bankRowPlacementIssues({
      date: '',
      currency: '2020-08-11',
      deposit: null,
      withdrawal: 4600,
      balance: null,
      description: 'fictional',
    })
    expect(issues).toContain('bank_date_missing_or_unparseable')
    expect(issues).toContain('bank_currency_misplaced')
    expect(bankRowNeedsPlacementCheck({ date: '', currency: '2020-08-11' })).toBe(true)
  })

  it('clears flag when date and currency are valid', () => {
    expect(
      bankRowNeedsPlacementCheck({
        date: '2020-08-11',
        currency: 'HKD',
        deposit: 100,
        withdrawal: null,
        balance: 200,
        description: 'ok',
      }),
    ).toBe(false)
    expect(bankRowPlacementIssues({ date: '2020-08-11', currency: 'HKD', description: 'ok' })).toEqual([])
  })

  it('counts flagged rows and builds banner text', () => {
    const rows = [
      { date: '', currency: '2020-08-11', description: 'a' },
      { date: '2020-08-12', currency: 'HKD', description: 'b' },
      { date: '', currency: 'HKD', description: 'c' },
    ]
    expect(countRowsNeedingPlacementCheck(rows)).toBe(2)
    expect(placementCheckBannerText(3)).toBe('3 rows need checking')
    expect(placementCheckBannerText(1)).toBe('1 row needs checking')
    expect(BANK_ROW_CHECK_MESSAGE).toContain('date or amount looks misplaced')
  })
})
