import { describe, expect, it } from 'vitest'

import { ruleMemorySeenKey } from './Settings'

describe('Settings rule memory helpers', () => {
  it('scopes rule-memory seen markers by company and mode', () => {
    expect(ruleMemorySeenKey('company-a', 'AR')).toBe('rm_seen_company-a_AR')
    expect(ruleMemorySeenKey('company-b', 'AR')).toBe('rm_seen_company-b_AR')
    expect(ruleMemorySeenKey('company-a', 'AP')).toBe('rm_seen_company-a_AP')
  })

  it('keeps the local-dev fallback explicit', () => {
    expect(ruleMemorySeenKey(null, 'BANK')).toBe('rm_seen_default_BANK')
  })
})
