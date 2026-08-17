import { useEffect, useState } from 'react'
import { api } from '../services/api'

export type TokenUsageMonth = { total_tokens: number; estimated_cost_usd: number }

/** Polls monthly token usage while authenticated; clears when logged out. */
export function useTokenUsageMonthly(accessToken: string | null | undefined, userPresent: boolean) {
  const [tokenUsage, setTokenUsage] = useState<TokenUsageMonth | null>(null)

  useEffect(() => {
    if (!accessToken || !userPresent) {
      setTokenUsage(null)
      return
    }
    const load = () => {
      api.getTokenUsage('month').then(d => setTokenUsage(d)).catch(() => {})
    }
    load()
    const interval = setInterval(load, 60_000)
    return () => clearInterval(interval)
  }, [accessToken, userPresent])

  return tokenUsage
}
