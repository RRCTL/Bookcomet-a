import { useEffect, useRef } from 'react'
import { workflowApi, workflowRunWsAuthMessage, workflowRunWsUrl, type WorkflowRun } from './workflowApi'

export type WorkflowRunEvent = {
  type: string
  run_id?: string
  node_id?: string
  run_status?: string
  node_states_json?: Record<string, unknown>
  console_line?: { ts: string; level: string; message: string }
}

type Options = {
  runId: string | null
  runStatus: string | undefined
  companyId: string | undefined
  accessToken: string | null
  enabled: boolean
  onEvent: (run: WorkflowRun) => void
  onError: (err: unknown) => void
}

export function useWorkflowRunEvents({
  runId,
  runStatus,
  companyId,
  accessToken,
  enabled,
  onEvent,
  onError,
}: Options) {
  const onEventRef = useRef(onEvent)
  const onErrorRef = useRef(onError)
  onEventRef.current = onEvent
  onErrorRef.current = onError

  useEffect(() => {
    if (!enabled || !runId || !companyId) return
    if (!runStatus || !['executing', 'coa_running'].includes(runStatus)) return

    let closed = false
    let ws: WebSocket | null = null
    let fallbackTimer: ReturnType<typeof setTimeout> | null = null

    const refreshOnce = () => {
      void workflowApi
        .getRun(companyId, runId)
        .then(run => {
          if (!closed) onEventRef.current(run)
        })
        .catch(err => {
          if (!closed) onErrorRef.current(err)
        })
    }

    try {
      ws = new WebSocket(workflowRunWsUrl(runId))
    } catch (err) {
      onErrorRef.current(err)
      refreshOnce()
      return
    }

    ws.onopen = () => {
      if (!accessToken) {
        ws?.close()
        return
      }
      ws?.send(workflowRunWsAuthMessage(accessToken))
    }

    ws.onmessage = () => {
      refreshOnce()
    }

    ws.onerror = () => {
      if (!closed) onErrorRef.current(new Error('Workflow connection error'))
    }

    ws.onclose = () => {
      if (closed) return
      fallbackTimer = setTimeout(refreshOnce, 1500)
    }

    refreshOnce()
    const pollInterval = window.setInterval(refreshOnce, 3000)

    return () => {
      closed = true
      window.clearInterval(pollInterval)
      if (fallbackTimer) clearTimeout(fallbackTimer)
      ws?.close()
    }
  }, [enabled, runId, runStatus, companyId, accessToken])
}
