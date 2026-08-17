import { apiFetch } from './api'

export type ApiGatewayId = 'vlm' | 'llm' | 'ai_enhance' | 'bank_cross_vlm' | 'ap_cross_vlm'

export const API_GATEWAY_IDS: ApiGatewayId[] = [
  'vlm',
  'llm',
  'ai_enhance',
  'bank_cross_vlm',
  'ap_cross_vlm',
]

export type ApiGatewayStored = {
  api_url: string
  model: string
  api_key: string
  has_api_key: boolean
}

export type ApiGatewayEffective = {
  api_url?: string
  model?: string
  has_api_key?: boolean
}

export type ApiSettingsResponse = {
  gateways: Record<ApiGatewayId, ApiGatewayStored>
  effective?: Partial<Record<ApiGatewayId, ApiGatewayEffective>>
}

export type ApiGatewayUpdate = {
  api_url: string
  model: string
  api_key: string
}

export type PutApiSettingsResponse = {
  restart_required?: boolean
  gateways?: Record<ApiGatewayId, ApiGatewayStored>
  effective?: Partial<Record<ApiGatewayId, ApiGatewayEffective>>
  message?: string
}

export type TestApiSettingsResponse = {
  ok: boolean
  message: string
}

async function readError(res: Response): Promise<string> {
  const err = await res.json().catch(() => ({ detail: res.statusText }))
  const detail = (err as { detail?: unknown }).detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map(item => (typeof item === 'object' && item && 'msg' in item ? String((item as { msg: unknown }).msg) : String(item)))
      .join('; ')
  }
  return res.statusText || 'Request failed'
}

export async function getApiSettings(): Promise<ApiSettingsResponse> {
  const res = await apiFetch('/settings/api')
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

export async function putApiSettings(
  gateways: Partial<Record<ApiGatewayId, ApiGatewayUpdate>>,
): Promise<PutApiSettingsResponse> {
  const res = await apiFetch('/settings/api', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ gateways }),
  })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

export async function testApiSettings(body: {
  gateway: ApiGatewayId
  api_url?: string
  model?: string
  api_key?: string
}): Promise<TestApiSettingsResponse> {
  const res = await apiFetch('/settings/api/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    timeoutMs: 30_000,
  })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}
