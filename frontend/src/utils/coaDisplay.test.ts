import { describe, expect, it } from 'vitest'
import {
  coaNameMapFromOptionLabels,
  coaOptionLabel,
  validCoaCode,
} from './coaDisplay'

describe('coaDisplay', () => {
  it('builds option labels with code and name', () => {
    expect(
      coaOptionLabel({
        code: '5100',
        name_en: 'Office Expense',
        name_zh: '辦公費用',
        category_type: 'expense',
        allowed_modes: ['AP'],
      }),
    ).toMatch(/^5100 /)
  })

  it('parses option label maps', () => {
    const map = coaNameMapFromOptionLabels(['5100 Office Expense', '1010 Cash'])
    expect(map.get('5100')).toBe('Office Expense')
    expect(map.get('1010')).toBe('Cash')
  })

  it('validates codes against the CoA set', () => {
    const codes = new Set(['5100', '1010'])
    expect(validCoaCode('5100', codes)).toBe('5100')
    expect(validCoaCode('9999', codes)).toBe('')
    expect(validCoaCode('  ', codes)).toBe('')
  })
})
