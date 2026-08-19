import type { WorkflowRunFile } from './workflowApi'
import type { ApVlmReceiptSignal, ApVlmTablePreset } from '../workspace/apComposerOptions'

export type ReVlmWorkflowSettings = {
  templateId: string
  provider: string
  receiptSignal?: ApVlmReceiptSignal
  tablePreset?: ApVlmTablePreset
}

export type ReVlmReasonId =
  | 'missed_receipts'
  | 'too_many_splits'
  | 'wrong_layout'
  | 'wrong_amount'
  | 'wrong_currency'
  | 'wrong_vendor'
  | 'wrong_date'
  | 'wrong_invoice_no'
  | 'gate_false_positive'
  | 'incomplete_rows'

export type ReVlmReasonChip = {
  id: ReVlmReasonId
  label: string
}

export const RE_VLM_REASON_CHIPS: ReVlmReasonChip[] = [
  { id: 'missed_receipts', label: 'Missed receipt(s) on page' },
  { id: 'too_many_splits', label: 'Too many splits' },
  { id: 'wrong_layout', label: 'Wrong document type' },
  { id: 'wrong_amount', label: 'Wrong amount' },
  { id: 'wrong_currency', label: 'Wrong currency' },
  { id: 'wrong_vendor', label: 'Wrong vendor / payee' },
  { id: 'wrong_date', label: 'Wrong date' },
  { id: 'wrong_invoice_no', label: 'Wrong invoice / voucher no.' },
  { id: 'gate_false_positive', label: 'Gate rejected valid doc' },
  { id: 'incomplete_rows', label: 'Missing rows / columns' },
]

export const RE_VLM_NOTE_MAX_LEN = 200
export const RE_VLM_EXPECTED_COUNT_MAX = 36

export function suggestRescanReasons(file: WorkflowRunFile): ReVlmReasonId[] {
  const out: ReVlmReasonId[] = []
  const gate = (file.gate_result ?? '').toUpperCase()
  const err = (file.error_text ?? '').toLowerCase()

  if (file.file_status === 'warning' && gate && gate !== 'TRANSACTIONAL') {
    out.push('gate_false_positive')
  }
  if (err.includes('needs_confirmation') || err.includes('multi')) {
    out.push('missed_receipts')
  }
  if (file.file_status === 'failed') {
    out.push('incomplete_rows')
  }
  return out
}

export function suggestRescanReasonsForFiles(
  files: WorkflowRunFile[],
  selectedIds: string[],
): ReVlmReasonId[] {
  const selected = files.filter(f => selectedIds.includes(f.task_file_id))
  const merged = new Set<ReVlmReasonId>()
  for (const file of selected) {
    for (const id of suggestRescanReasons(file)) {
      merged.add(id)
    }
  }
  return [...merged]
}

export type ReVlmConfirmPayload = {
  taskFileIds: string[]
  rescanReasons: ReVlmReasonId[]
  rescanNote: string
  /** Optional hard expected physical receipt count (any N). */
  expectedReceiptCount?: number | null
  workflow?: ReVlmWorkflowSettings
}
