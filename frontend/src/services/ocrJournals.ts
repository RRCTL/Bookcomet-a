/**
 * Client for /ocr-journals — draft double-entry per bank or ledger OCR row.
 */
import { apiFetch } from './api'

export type OcrJournalSource = 'bank' | 'ledger'

export interface OcrJournalLine {
  id: string
  line_no: number
  account_code: string
  debit: number
  credit: number
  memo?: string | null
}

export interface OcrJournalRecord {
  id: string
  company_id: string
  task_id: string | null
  source: string
  source_txn_id: string
  status: string
  journal_date: string | null
  currency: string | null
  voucher_no: string | null
  narration: string | null
  created_at: string | null
  updated_at: string | null
  lines: OcrJournalLine[]
}

export interface OcrJournalLineIn {
  account_code: string
  debit?: number
  credit?: number
  memo?: string | null
}

export interface OcrJournalUpsertBody {
  task_id?: string | null
  journal_date: string
  narration?: string | null
  voucher_no?: string | null
  currency?: string | null
  lines: OcrJournalLineIn[]
}

async function readError(response: Response, fallback: string): Promise<string> {
  const body = await response.json().catch(() => null)
  if (body && typeof body === 'object' && 'detail' in body) {
    const d = (body as { detail: unknown }).detail
    if (typeof d === 'string') return d
  }
  return fallback
}

export const ocrJournalApi = {
  async get(source: OcrJournalSource, sourceTxnId: string): Promise<OcrJournalRecord> {
    const response = await apiFetch(
      `/ocr-journals/${source}/${encodeURIComponent(sourceTxnId)}`,
    )
    if (!response.ok) {
      throw new Error(await readError(response, `Failed to load OCR journal (${response.status})`))
    }
    return response.json()
  },

  async upsert(
    source: OcrJournalSource,
    sourceTxnId: string,
    body: OcrJournalUpsertBody,
  ): Promise<OcrJournalRecord> {
    const response = await apiFetch(
      `/ocr-journals/${source}/${encodeURIComponent(sourceTxnId)}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    )
    if (!response.ok) {
      throw new Error(await readError(response, `Failed to save OCR journal (${response.status})`))
    }
    return response.json()
  },

  async remove(source: OcrJournalSource, sourceTxnId: string): Promise<void> {
    const response = await apiFetch(
      `/ocr-journals/${source}/${encodeURIComponent(sourceTxnId)}`,
      { method: 'DELETE' },
    )
    if (!response.ok && response.status !== 404) {
      throw new Error(await readError(response, `Failed to delete OCR journal (${response.status})`))
    }
  },

  async listByTask(taskId: string): Promise<OcrJournalRecord[]> {
    const response = await apiFetch(`/ocr-journals/by-task/${encodeURIComponent(taskId)}`)
    if (!response.ok) {
      throw new Error(await readError(response, `Failed to list OCR journals (${response.status})`))
    }
    const data = await response.json()
    return Array.isArray(data?.journals) ? data.journals : []
  },

  async exportJson(params: {
    task_id?: string
    date_from?: string
    date_to?: string
  }): Promise<{ journals: OcrJournalRecord[] }> {
    const q = new URLSearchParams({ format: 'json' })
    if (params.task_id) q.set('task_id', params.task_id)
    if (params.date_from) q.set('date_from', params.date_from)
    if (params.date_to) q.set('date_to', params.date_to)
    const response = await apiFetch(`/ocr-journals/export/data?${q}`)
    if (!response.ok) {
      throw new Error(await readError(response, `OCR journal export failed (${response.status})`))
    }
    return response.json()
  },

  async exportCsv(params: {
    task_id?: string
    date_from?: string
    date_to?: string
  }): Promise<string> {
    const q = new URLSearchParams({ format: 'csv' })
    if (params.task_id) q.set('task_id', params.task_id)
    if (params.date_from) q.set('date_from', params.date_from)
    if (params.date_to) q.set('date_to', params.date_to)
    const response = await apiFetch(`/ocr-journals/export/data?${q}`)
    if (!response.ok) {
      throw new Error(await readError(response, `OCR journal export failed (${response.status})`))
    }
    return response.text()
  },
}
