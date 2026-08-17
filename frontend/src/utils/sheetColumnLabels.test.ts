import { describe, expect, it } from 'vitest'
import { sheetColumnLabel } from './sheetColumnLabels'

describe('sheetColumnLabel', () => {
  it('maps Chinese OCR keys to English chrome without renaming the key', () => {
    expect(sheetColumnLabel('憑證號')).toBe('Voucher no.')
    expect(sheetColumnLabel('存入')).toBe('Deposit')
    expect(sheetColumnLabel('提取')).toBe('Withdrawal')
    expect(sheetColumnLabel('匹配狀態')).toBe('Match status')
  })

  it('leaves unknown or English fields unchanged', () => {
    expect(sheetColumnLabel('categorise')).toBe('categorise')
    expect(sheetColumnLabel('Group ID')).toBe('Group ID')
  })
})
