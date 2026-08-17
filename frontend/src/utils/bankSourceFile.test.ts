import { describe, expect, it } from 'vitest'
import { formatBankSourceFile, hasBankSourcePageSuffix } from './bankSourceFile'

describe('formatBankSourceFile', () => {
  it('appends page to filename when _page is set', () => {
    expect(formatBankSourceFile('sample-bank-a.pdf', 3)).toBe('sample-bank-a.pdf P3')
  })

  it('appends page to existing source_file without suffix', () => {
    expect(formatBankSourceFile('sample-bank-a.pdf', 2, 'sample-bank-a.pdf')).toBe(
      'sample-bank-a.pdf P2',
    )
  })

  it('keeps source_file that already has a page suffix', () => {
    expect(formatBankSourceFile('other.pdf', 9, 'sample-bank-a.pdf P4')).toBe('sample-bank-a.pdf P4')
  })
})

describe('hasBankSourcePageSuffix', () => {
  it('detects page suffix', () => {
    expect(hasBankSourcePageSuffix('sample-bank-a.pdf P4')).toBe(true)
    expect(hasBankSourcePageSuffix('sample-bank-a.pdf')).toBe(false)
  })
})
