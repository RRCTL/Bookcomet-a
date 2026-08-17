import { describe, expect, it } from 'vitest'
import { formatFilePageCount } from './filePageLabel'

describe('formatFilePageCount', () => {
  it('formats single and multi page counts', () => {
    expect(formatFilePageCount(1)).toBe('1 page')
    expect(formatFilePageCount(12)).toBe('12 pages')
  })

  it('returns null for missing or invalid counts', () => {
    expect(formatFilePageCount(null)).toBeNull()
    expect(formatFilePageCount(undefined)).toBeNull()
    expect(formatFilePageCount(0)).toBeNull()
  })
})
