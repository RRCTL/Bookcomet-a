import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  clearAuthRefreshCooldown,
  markAuthRefreshCooldown,
  notifyApiRefreshFailure,
  refreshAccessTokenShared,
  setApiRefreshFailureHandler,
  setTokenRefresher,
  _isAuthRefreshCoolingDown,
} from './api'

afterEach(() => {
  clearAuthRefreshCooldown()
  setTokenRefresher(async () => {
    throw new Error('no refresher in test cleanup')
  })
  setApiRefreshFailureHandler(null)
})

describe('refreshAccessTokenShared', () => {
  it('single-flights concurrent refresh callers (fetch + axios race)', async () => {
    let calls = 0
    setTokenRefresher(async () => {
      calls += 1
      await new Promise(r => setTimeout(r, 20))
      return `token-${calls}`
    })

    const [a, b, c] = await Promise.all([
      refreshAccessTokenShared(),
      refreshAccessTokenShared(),
      refreshAccessTokenShared(),
    ])

    expect(calls).toBe(1)
    expect(a).toBe('token-1')
    expect(b).toBe('token-1')
    expect(c).toBe('token-1')
  })

  it('marks cooldown and rejects all waiters when refresh fails', async () => {
    setTokenRefresher(async () => {
      await new Promise(r => setTimeout(r, 5))
      throw new Error('refresh 401')
    })

    const results = await Promise.allSettled([
      refreshAccessTokenShared(),
      refreshAccessTokenShared(),
    ])

    expect(results.every(r => r.status === 'rejected')).toBe(true)
    expect(_isAuthRefreshCoolingDown()).toBe(true)

    await expect(refreshAccessTokenShared()).rejects.toThrow(/Session expired/)
  })

  it('rejects immediately while cooldown is active (no second /auth/refresh)', async () => {
    let calls = 0
    setTokenRefresher(async () => {
      calls += 1
      return 'tok'
    })
    markAuthRefreshCooldown(60_000)

    await expect(refreshAccessTokenShared()).rejects.toThrow(/Session expired/)
    expect(calls).toBe(0)
  })

  it('notifyApiRefreshFailure invokes registered handler once for clear recovery', () => {
    const handler = vi.fn()
    setApiRefreshFailureHandler(handler)
    notifyApiRefreshFailure()
    notifyApiRefreshFailure()
    expect(handler).toHaveBeenCalledTimes(2)
  })

  it('allows a new refresh after cooldown is cleared (post re-login)', async () => {
    let calls = 0
    setTokenRefresher(async () => {
      calls += 1
      if (calls === 1) throw new Error('first fail')
      return 'ok-token'
    })

    await expect(refreshAccessTokenShared()).rejects.toThrow('first fail')
    expect(_isAuthRefreshCoolingDown()).toBe(true)

    clearAuthRefreshCooldown()
    await expect(refreshAccessTokenShared()).resolves.toBe('ok-token')
    expect(calls).toBe(2)
  })
})
