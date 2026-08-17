import { useEffect, useState } from 'react'
import type { ProcessingMode } from '../components/ModeSelector'

/**
 * BANK mode anchors AI chat to a task id separate from `activeTaskId`.
 * Clears when leaving BANK mode.
 */
export function useBankChatTargetTaskId(processingMode: ProcessingMode) {
  const [bankChatTargetTaskId, setBankChatTargetTaskId] = useState<string | null>(null)

  useEffect(() => {
    if (processingMode !== 'BANK') setBankChatTargetTaskId(null)
  }, [processingMode])

  return { bankChatTargetTaskId, setBankChatTargetTaskId }
}
