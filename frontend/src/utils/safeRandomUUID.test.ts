import { afterEach, describe, expect, it, vi } from 'vitest'
import { safeRandomUUID } from './safeRandomUUID'

describe('safeRandomUUID', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('uses crypto.randomUUID when available', () => {
    vi.stubGlobal('crypto', {
      randomUUID: () => '11111111-2222-3333-4444-555555555555',
    })
    expect(safeRandomUUID()).toBe('11111111-2222-3333-4444-555555555555')
  })

  it('falls back to getRandomValues when randomUUID missing', () => {
    const fixed = new Uint8Array(16)
    for (let i = 0; i < 16; i++) fixed[i] = i
    vi.stubGlobal('crypto', {
      getRandomValues: (buf: Uint8Array) => {
        buf.set(fixed)
        return buf
      },
    })
    const id = safeRandomUUID()
    expect(id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    )
  })

  it('falls back without crypto', () => {
    vi.stubGlobal('crypto', undefined)
    const id = safeRandomUUID()
    expect(id.startsWith('fallback-')).toBe(true)
    expect(id.length).toBeGreaterThan(16)
  })
})
