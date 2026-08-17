import { useCallback, useEffect, useState } from 'react'
import {
  API_GATEWAY_IDS,
  getApiSettings,
  putApiSettings,
  testApiSettings,
  type ApiGatewayEffective,
  type ApiGatewayId,
  type ApiGatewayStored,
} from '../../services/apiSettings'

type DraftGateway = {
  api_url: string
  model: string
  api_key: string
  has_api_key: boolean
}

const GATEWAY_META: Record<
  ApiGatewayId,
  { title: string; description: string; required?: boolean }
> = {
  vlm: {
    title: 'VLM',
    description: 'Primary vision model used for document OCR and structured extraction.',
    required: true,
  },
  llm: {
    title: 'LLM',
    description: 'Text model for chat and reasoning. Leave blank to use VLM settings.',
  },
  ai_enhance: {
    title: 'AI Enhance',
    description: 'Optional enhance gateway. Leave blank to use VLM settings.',
  },
  bank_cross_vlm: {
    title: 'Bank Cross-VLM',
    description: 'Second-pass bank statement verification. Leave blank to use VLM settings.',
  },
  ap_cross_vlm: {
    title: 'AP Cross-VLM',
    description: 'Second-pass AP receipt verification. Leave blank to use VLM settings.',
  },
}

function emptyDraft(): Record<ApiGatewayId, DraftGateway> {
  return {
    vlm: { api_url: '', model: '', api_key: '', has_api_key: false },
    llm: { api_url: '', model: '', api_key: '', has_api_key: false },
    ai_enhance: { api_url: '', model: '', api_key: '', has_api_key: false },
    bank_cross_vlm: { api_url: '', model: '', api_key: '', has_api_key: false },
    ap_cross_vlm: { api_url: '', model: '', api_key: '', has_api_key: false },
  }
}

function toDraft(stored: ApiGatewayStored): DraftGateway {
  return {
    api_url: stored.api_url ?? '',
    model: stored.model ?? '',
    api_key: stored.api_key ?? '',
    has_api_key: Boolean(stored.has_api_key),
  }
}

function effectiveHint(stored: DraftGateway, effective?: ApiGatewayEffective): string | null {
  if (!effective) return null
  const parts: string[] = []
  if (effective.api_url && !stored.api_url.trim() && effective.api_url !== stored.api_url) {
    parts.push(`URL → ${effective.api_url}`)
  }
  if (effective.model && !stored.model.trim() && effective.model !== stored.model) {
    parts.push(`Model → ${effective.model}`)
  }
  if (effective.has_api_key && !stored.has_api_key && !stored.api_key.trim()) {
    parts.push('API key → from VLM')
  }
  if (parts.length === 0) return null
  return `Effective at runtime: ${parts.join(' · ')}`
}

export function ApiSettingsPanel() {
  const [drafts, setDrafts] = useState<Record<ApiGatewayId, DraftGateway>>(emptyDraft)
  const [effective, setEffective] = useState<Partial<Record<ApiGatewayId, ApiGatewayEffective>>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testingId, setTestingId] = useState<ApiGatewayId | null>(null)
  const [status, setStatus] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [testStatus, setTestStatus] = useState<Partial<Record<ApiGatewayId, { ok: boolean; text: string }>>>({})

  const applyResponse = useCallback(
    (gateways: Record<ApiGatewayId, ApiGatewayStored>, nextEffective?: Partial<Record<ApiGatewayId, ApiGatewayEffective>>) => {
      const next = emptyDraft()
      for (const id of API_GATEWAY_IDS) {
        if (gateways[id]) next[id] = toDraft(gateways[id])
      }
      setDrafts(next)
      setEffective(nextEffective ?? {})
    },
    [],
  )

  const load = useCallback(async () => {
    setLoading(true)
    setStatus(null)
    try {
      const data = await getApiSettings()
      applyResponse(data.gateways, data.effective)
    } catch (err) {
      setStatus({
        type: 'error',
        text: err instanceof Error ? err.message : 'Failed to load API settings',
      })
    } finally {
      setLoading(false)
    }
  }, [applyResponse])

  useEffect(() => {
    void load()
  }, [load])

  const updateField = (id: ApiGatewayId, field: 'api_url' | 'model' | 'api_key', value: string) => {
    setDrafts(prev => ({
      ...prev,
      [id]: { ...prev[id], [field]: value },
    }))
  }

  const handleSave = async () => {
    setSaving(true)
    setStatus(null)
    try {
      const gateways = Object.fromEntries(
        API_GATEWAY_IDS.map(id => [
          id,
          {
            api_url: drafts[id].api_url,
            model: drafts[id].model,
            api_key: drafts[id].api_key,
          },
        ]),
      ) as Record<ApiGatewayId, { api_url: string; model: string; api_key: string }>
      const result = await putApiSettings(gateways)
      if (result.gateways) {
        applyResponse(result.gateways, result.effective)
      } else {
        await load()
      }
      setStatus({
        type: 'success',
        text: result.message || 'API settings saved. Changes apply without restart.',
      })
    } catch (err) {
      setStatus({
        type: 'error',
        text: err instanceof Error ? err.message : 'Failed to save API settings',
      })
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async (id: ApiGatewayId) => {
    setTestingId(id)
    setTestStatus(prev => ({ ...prev, [id]: undefined }))
    try {
      const draft = drafts[id]
      const body: {
        gateway: ApiGatewayId
        api_url?: string
        model?: string
        api_key?: string
      } = { gateway: id }
      if (draft.api_url.trim()) body.api_url = draft.api_url.trim()
      if (draft.model.trim()) body.model = draft.model.trim()
      if (draft.api_key.trim() && draft.api_key !== '***') body.api_key = draft.api_key
      const result = await testApiSettings(body)
      setTestStatus(prev => ({
        ...prev,
        [id]: { ok: result.ok, text: result.message || (result.ok ? 'Connection OK' : 'Test failed') },
      }))
    } catch (err) {
      setTestStatus(prev => ({
        ...prev,
        [id]: {
          ok: false,
          text: err instanceof Error ? err.message : 'Test failed',
        },
      }))
    } finally {
      setTestingId(null)
    }
  }

  if (loading) {
    return (
      <div className="settings-section api-settings-panel">
        <p className="settings-description">Loading API settings…</p>
      </div>
    )
  }

  return (
    <div className="settings-section api-settings-panel">
      <div className="skills-surface-header">
        <h3 className="manus-page-title">API</h3>
        <p className="manus-page-subtitle">
          Shared OpenAI-compatible gateways for this server. Optional gateways inherit blank fields from VLM at runtime.
        </p>
        <p className="api-cloud-ai-notice" role="note">
          When using cloud OCR / AI, uploaded document images and some company information will be sent to
          your configured AI service provider. Select a local endpoint to keep data on this device.
        </p>
      </div>

      {status && (
        <div className={`settings-status ${status.type}`} role="status">
          {status.text}
        </div>
      )}

      <div className="api-gateway-list">
        {API_GATEWAY_IDS.map(id => {
          const meta = GATEWAY_META[id]
          const draft = drafts[id]
          const hint = meta.required ? null : effectiveHint(draft, effective[id])
          const test = testStatus[id]
          return (
            <div key={id} className="api-gateway-card">
              <div className="api-gateway-card-header">
                <div>
                  <h4 className="rules-section-title">
                    {meta.title}
                    {meta.required ? <span className="api-gateway-required"> Required</span> : null}
                  </h4>
                  <p className="settings-description">{meta.description}</p>
                </div>
                <button
                  type="button"
                  className="settings-save-btn secondary"
                  disabled={testingId === id || saving}
                  onClick={() => void handleTest(id)}
                >
                  {testingId === id ? 'Testing…' : 'Test'}
                </button>
              </div>

              <div className="settings-field">
                <label htmlFor={`api-url-${id}`}>API URL</label>
                <input
                  id={`api-url-${id}`}
                  type="text"
                  className="settings-input"
                  value={draft.api_url}
                  placeholder={meta.required ? 'https://api.example.com/v1' : 'Leave blank to use VLM'}
                  onChange={e => updateField(id, 'api_url', e.target.value)}
                  autoComplete="off"
                />
              </div>

              <div className="settings-field">
                <label htmlFor={`api-model-${id}`}>Model</label>
                <input
                  id={`api-model-${id}`}
                  type="text"
                  className="settings-input"
                  value={draft.model}
                  placeholder={meta.required ? 'Model name' : 'Leave blank to use VLM'}
                  onChange={e => updateField(id, 'model', e.target.value)}
                  autoComplete="off"
                />
              </div>

              <div className="settings-field">
                <label htmlFor={`api-key-${id}`}>API Key</label>
                <input
                  id={`api-key-${id}`}
                  type="password"
                  className="settings-input"
                  value={draft.api_key}
                  placeholder={draft.has_api_key ? 'Saved (enter a new key to replace)' : meta.required ? 'API key' : 'Leave blank to use VLM'}
                  onChange={e => updateField(id, 'api_key', e.target.value)}
                  autoComplete="new-password"
                />
                {draft.has_api_key && draft.api_key === '***' ? (
                  <small>A key is saved. Leave *** unchanged, or paste a new key to replace it.</small>
                ) : null}
              </div>

              {hint ? <p className="settings-hint">{hint}</p> : null}
              {test ? (
                <p className={`settings-status ${test.ok ? 'success' : 'error'}`} role="status">
                  {test.text}
                </p>
              ) : null}
            </div>
          )
        })}
      </div>

      <div className="settings-actions">
        <button type="button" className="settings-save-btn secondary" disabled={saving} onClick={() => void load()}>
          Reload
        </button>
        <button type="button" className="settings-save-btn primary" disabled={saving} onClick={() => void handleSave()}>
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  )
}
