import { describe, expect, it } from 'vitest'
import { assetSourceLabel, txSourceLabel } from './rowSourceLabel'

describe('txSourceLabel', () => {
  it('returns source_file with page suffix when already set', () => {
    expect(txSourceLabel({ source_file: 'stmt.pdf P4' }, 'other.pdf')).toBe('stmt.pdf P4')
  })

  it('appends page from _page when source_file has no suffix', () => {
    expect(txSourceLabel({ source_file: 'stmt.pdf', _page: 3 }, 'stmt.pdf')).toBe('stmt.pdf P3')
  })

  it('builds label from fallback filename and _page', () => {
    expect(txSourceLabel({ _page: 2 }, 'receipt.pdf')).toBe('receipt.pdf P2')
  })

  it('falls back to filename when no page', () => {
    expect(txSourceLabel({}, 'invoice.pdf')).toBe('invoice.pdf')
  })
})

describe('assetSourceLabel', () => {
  const filesById = new Map([
    ['f1', { task_file_id: 'f1', original_filename: 'loan.pdf' }],
  ])

  it('enriches filename with page from record', () => {
    expect(assetSourceLabel({ source_file_id: 'f1', _page: 5 }, filesById)).toBe('loan.pdf P5')
  })

  it('keeps existing source_file with page suffix', () => {
    expect(assetSourceLabel({ source_file: 'loan.pdf P2', source_file_id: 'f1' }, filesById)).toBe(
      'loan.pdf P2',
    )
  })
})
