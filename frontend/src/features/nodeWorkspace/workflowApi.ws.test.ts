import { describe, expect, it } from 'vitest'
import { workflowRunWsAuthMessage, workflowRunWsUrl } from './workflowApi'

describe('workflow WebSocket auth', () => {
  it('does not put the access token in the URL', () => {
    const url = workflowRunWsUrl('run-123')
    expect(url).toContain('/api/workflows/runs/run-123/ws')
    expect(url).not.toContain('token=')
    expect(url).not.toContain('secret-token')
  })

  it('sends the token in a first-message auth payload', () => {
    const body = workflowRunWsAuthMessage('secret-token')
    expect(JSON.parse(body)).toEqual({ type: 'auth', token: 'secret-token' })
  })
})
