import type { ServerTaskMessage } from '../../services/api'
import type { Message, QueuedFile } from './types'

/** Defensive: `{}` persisted for list payloads is truthy but breaks `.map()` in reviewers. */
function stripMalformedListFieldsFromPayload(p: Record<string, unknown>): void {
  const keys = [
    'arapTransactions',
    'spreadsheetData',
    'bankTransactions',
    'uploadedFiles',
    'fileRefs',
  ] as const
  for (const k of keys) {
    const v = p[k]
    if (v != null && !Array.isArray(v)) delete p[k]
  }
}

function coerceUploadedFilesFromPayload(raw: unknown): Message['uploadedFiles'] {
  if (!Array.isArray(raw)) return undefined
  const out: QueuedFile[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const o = item as Record<string, unknown>
    const id = typeof o.id === 'string' ? o.id : ''
    if (!id) continue
    const st = o.status
    const status: QueuedFile['status'] =
      st === 'pending' || st === 'processing' || st === 'completed' || st === 'failed' || st === 'cancelled'
        ? st
        : 'completed'
    let name =
      (typeof o.storedFileName === 'string' && o.storedFileName.trim()) ||
      (typeof o.fileName === 'string' && o.fileName.trim()) ||
      ''
    const fr = o.file
    if (!name && fr && typeof fr === 'object' && typeof (fr as { name?: unknown }).name === 'string')
      name = String((fr as { name: string }).name).trim()
    if (!name) name = 'file'
    const file = fr instanceof File ? fr : new File([], name)
    out.push({ ...(o as QueuedFile), id, status, file })
  }
  return out.length ? out : undefined
}

function coerceFileRefsFromPayload(raw: unknown): Message['fileRefs'] {
  if (!Array.isArray(raw)) return undefined
  const out: { id: string; name: string }[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const o = item as Record<string, unknown>
    const id = typeof o.id === 'string' ? o.id : ''
    if (!id) continue
    let name = typeof o.name === 'string' ? o.name.trim() : ''
    if (!name && typeof o.file_name === 'string') name = o.file_name.trim()
    out.push({ id, name: name || 'file' })
  }
  return out.length ? out : undefined
}

function normalizePersistedListPayloadFields(p: Record<string, unknown>): void {
  if ('uploadedFiles' in p) {
    const coerced = coerceUploadedFilesFromPayload(p.uploadedFiles)
    if (coerced) p.uploadedFiles = coerced
    else delete p.uploadedFiles
  }
  if ('fileRefs' in p) {
    const coerced = coerceFileRefsFromPayload(p.fileRefs)
    if (coerced) p.fileRefs = coerced
    else delete p.fileRefs
  }
}

export function mapServerTaskMessagesToClient(serverMsgs: ServerTaskMessage[]): Message[] {
  return serverMsgs.map(sm => {
    const p =
      sm.payload_json && typeof sm.payload_json === 'object'
        ? { ...(sm.payload_json as Record<string, unknown>) }
        : {}
    stripMalformedListFieldsFromPayload(p)
    normalizePersistedListPayloadFields(p)
    const rr = (p.recon_redirect ?? p.reconRedirect) as Message['reconRedirect'] | undefined
    delete p.recon_redirect
    delete p.reconRedirect
    return {
      id: sm.id,
      role: sm.role as 'user' | 'assistant',
      content: sm.content_text,
      contentType: sm.content_type,
      ...p,
      ...(rr && typeof rr === 'object' ? { reconRedirect: rr } : {}),
    }
  })
}

export function hydrateMessagesWithReconIdMap(messages: Message[], idMapSnapshot: Record<string, string>): Message[] {
  if (!Object.keys(idMapSnapshot).length) return messages
  return messages.map((msg: any) => {
    let changed = false
    const btArr = msg.bankTransactions
    const nextBank =
      Array.isArray(btArr)
        ? btArr.map((bt: any) => {
          const key = bt.bank_txn_id || bt.id_number || ''
          const gid = idMapSnapshot[key]
          if (gid && bt.matched_id !== gid) {
            changed = true
            return { ...bt, matched_id: gid }
          }
          return bt
        })
        : msg.bankTransactions

    const arArr = msg.arapTransactions
    const nextArap =
      Array.isArray(arArr)
        ? arArr.map((at: any) => {
          const key = at.ledger_txn_id || at.id_number || ''
          const baseKey = key.replace(/^(AR|AP)-/, '')
          const gid = idMapSnapshot[key] || idMapSnapshot[baseKey]
          if (gid && at.matched_id !== gid) {
            changed = true
            return { ...at, matched_id: gid }
          }
          return at
        })
        : msg.arapTransactions

    if (!changed) return msg
    return { ...msg, bankTransactions: nextBank, arapTransactions: nextArap }
  })
}
