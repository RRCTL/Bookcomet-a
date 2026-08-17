import { describe, expect, it } from 'vitest'
import { formatTimeAgo } from './timeAgo'

describe('formatTimeAgo', () => {
  it('returns empty for invalid input', () => {
    expect(formatTimeAgo(null)).toBe('')
    expect(formatTimeAgo('')).toBe('')
  })

  it('formats recent timestamps', () => {
    const recent = new Date(Date.now() - 5 * 60 * 1000).toISOString()
    expect(formatTimeAgo(recent)).toBe('5m')
  })
})
