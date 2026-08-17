import type { ARAPTransaction } from '../../components/ARAPReview'
import type { BankTransaction } from '../../components/BankStatementReview'
import type { SpreadsheetRow } from '../../components/EditableSpreadsheet'
import type { ApVlmTablePreset } from '../workspace/apComposerOptions'
import { mergeSpreadsheetRowsForFile } from '../workspace/batchOcrSnapshot'
import { spreadsheetRowsToArapTransactions } from '../workspace/buildSpreadsheetFromOcrResult'
import { taskApi } from '../../services/api'
import { buildIncomingForFiles, buildTablePayloadFromRun, getOcrByFileFromRun } from './ocrTableBuilder'
import type { WorkflowRun } from './workflowApi'

function rowsForFile(sheet: SpreadsheetRow[], fileId: string): SpreadsheetRow[] {
  const prefix = `${fileId}-`
  return sheet.filter(r => {
    const id = String(r.id ?? '')
    return id === fileId || id.startsWith(prefix)
  })
}

function bankRowsForFile(txns: BankTransaction[], fileName: string, fileId: string): BankTransaction[] {
  return txns.filter(t => {
    const sf = String(t.source_file ?? '')
    return sf === fileName || sf.startsWith(`${fileName} `) || sf.includes(fileId)
  })
}

function mergeFileRefs(
  base: { id: string; name: string }[] | undefined,
  incoming: { id: string; name: string }[] | undefined,
): { id: string; name: string }[] {
  const map = new Map<string, { id: string; name: string }>()
  for (const r of base ?? []) {
    if (r?.id) map.set(r.id, r)
  }
  for (const r of incoming ?? []) {
    if (r?.id) map.set(r.id, r)
  }
  return [...map.values()]
}

export function tablePayloadHasRows(payload: Record<string, unknown>, mode: string): boolean {
  const m = mode.toUpperCase()
  if (m === 'BANK') {
    return ((payload.bankTransactions as BankTransaction[] | undefined)?.length ?? 0) > 0
  }
  return (
    ((payload.arapTransactions as ARAPTransaction[] | undefined)?.length ?? 0) > 0 ||
    ((payload.spreadsheetData as SpreadsheetRow[] | undefined)?.length ?? 0) > 0
  )
}

function arapRowKey(row: ARAPTransaction): string {
  return String(row.id_number ?? (row as Record<string, unknown>).id ?? '')
}

function arapMatchKeys(row: ARAPTransaction): string[] {
  const keys = new Set<string>()
  const idn = arapRowKey(row)
  if (idn) keys.add(idn)
  const voucher = String((row as Record<string, unknown>).voucher_no ?? '')
  if (voucher) keys.add(voucher)
  if (idn.startsWith('AR-') || idn.startsWith('AP-')) keys.add(idn.slice(3))
  return [...keys]
}

/** Keep module edits (account_code, category) and user-added rows when merging VLM data. */
function mergeArapPreservingEdits(baseArap: ARAPTransaction[], rebuilt: ARAPTransaction[]): ARAPTransaction[] {
  const baseByKey = new Map<string, ARAPTransaction>()
  for (const row of baseArap) {
    for (const key of arapMatchKeys(row)) {
      if (key) baseByKey.set(key, row)
    }
  }
  const seen = new Set<string>()
  const merged = rebuilt.map(row => {
    let prior: ARAPTransaction | undefined
    for (const key of arapMatchKeys(row)) {
      if (key) seen.add(key)
      prior = prior ?? (key ? baseByKey.get(key) : undefined)
    }
    if (!prior) return row
    return {
      ...row,
      account_code: prior.account_code ?? row.account_code,
      category: prior.category ?? row.category,
    }
  })
  for (const row of baseArap) {
    const keys = arapMatchKeys(row)
    if (keys.some(k => seen.has(k))) continue
    merged.push(row)
  }
  return merged
}

export function snapshotPayloadShape(
  payload: Record<string, unknown>,
  run: WorkflowRun,
  tablePreset?: ApVlmTablePreset,
): Record<string, unknown> {
  const mode = (run.processing_mode || 'AR').toUpperCase()
  const shaped: Record<string, unknown> = {
    spreadsheetData: payload.spreadsheetData ?? [],
    fileRefs: payload.fileRefs ?? [],
  }
  if (typeof payload.moduleSavedAt === 'string' && payload.moduleSavedAt) {
    shaped.moduleSavedAt = payload.moduleSavedAt
  }
  if (mode === 'BANK') {
    shaped.bankTransactions = payload.bankTransactions ?? []
  } else {
    shaped.arapTransactions = payload.arapTransactions ?? []
    shaped.arapFilename = payload.arapFilename ?? 'workflow'
    const preset = tablePreset ?? payload.apVlmTablePreset
    if (preset) shaped.apVlmTablePreset = preset
  }
  return shaped
}

export async function loadTablePayloadForRun(
  run: WorkflowRun,
  companyId?: string | null,
): Promise<Record<string, unknown> | null> {
  if (!run.task_id) return null
  try {
    const messages = await taskApi.getMessages(run.task_id, companyId)
    let snap = run.snapshot_message_id
      ? messages.find(m => m.id === run.snapshot_message_id)
      : undefined
    if (!snap) {
      const snaps = messages.filter(m => m.content_type === 'ocr_snapshot')
      snap = snaps[snaps.length - 1]
    }
    if (snap?.payload_json && typeof snap.payload_json === 'object') {
      return snap.payload_json as Record<string, unknown>
    }
  } catch {
    /* fall through */
  }
  return null
}

export function mergeTablePayload(
  base: Record<string, unknown>,
  incoming: Record<string, unknown>,
  processedFileIds: string[],
  run: WorkflowRun,
  tablePreset?: ApVlmTablePreset,
): Record<string, unknown> {
  const mode = (run.processing_mode || 'AR').toUpperCase()
  let sheet = [...((base.spreadsheetData as SpreadsheetRow[] | undefined) ?? [])]
  const incomingSheet = (incoming.spreadsheetData as SpreadsheetRow[] | undefined) ?? []

  for (const fileId of processedFileIds) {
    const fileIncoming = rowsForFile(incomingSheet, fileId)
    if (fileIncoming.length === 0 && fileId === 'workflow') {
      sheet = [...incomingSheet]
      continue
    }
    sheet = mergeSpreadsheetRowsForFile(sheet, fileIncoming, fileId)
  }

  const fileRefs = mergeFileRefs(
    base.fileRefs as { id: string; name: string }[] | undefined,
    incoming.fileRefs as { id: string; name: string }[] | undefined,
  )

  if (mode === 'BANK') {
    let bank = [...((base.bankTransactions as BankTransaction[] | undefined) ?? [])]
    const incomingBank = (incoming.bankTransactions as BankTransaction[] | undefined) ?? []
    for (const fileId of processedFileIds) {
      const fileName = run.files.find(f => f.task_file_id === fileId)?.original_filename ?? fileId
      const fileIncoming = bankRowsForFile(incomingBank, fileName, fileId)
      bank = bank.filter(t => {
        const sf = String(t.source_file ?? '')
        return !(sf === fileName || sf.startsWith(`${fileName} `) || sf.includes(fileId))
      })
      bank = [...bank, ...fileIncoming]
    }
    const baseBank = (base.bankTransactions as BankTransaction[] | undefined) ?? []
    const byId = new Map(baseBank.map(t => [String(t.id_number ?? ''), t]))
    bank = bank.map(t => {
      const prior = byId.get(String(t.id_number ?? ''))
      if (!prior?.account_code) return t
      return { ...t, account_code: prior.account_code }
    })
    return { spreadsheetData: sheet, bankTransactions: bank, fileRefs }
  }

  const baseArap = (base.arapTransactions as ARAPTransaction[] | undefined) ?? []
  const arap = mergeArapPreservingEdits(baseArap, spreadsheetRowsToArapTransactions(sheet, mode))
  return {
    spreadsheetData: sheet,
    arapTransactions: arap,
    arapFilename:
      fileRefs.length === 1 ? fileRefs[0]!.name : ((base.arapFilename as string | undefined) ?? 'workflow'),
    fileRefs,
    ...(tablePreset ? { apVlmTablePreset: tablePreset } : {}),
  }
}

export async function persistTableSnapshot(
  run: WorkflowRun,
  payload: Record<string, unknown>,
  companyId?: string | null,
  tablePreset?: ApVlmTablePreset,
): Promise<WorkflowRun> {
  const shaped = snapshotPayloadShape(payload, run, tablePreset)
  if (run.snapshot_message_id && run.task_id) {
    await taskApi.patchMessage(
      run.task_id,
      run.snapshot_message_id,
      { content_text: 'OCR snapshot', payload_json: shaped },
      companyId,
    )
    return run
  }
  if (!run.task_id) return run
  const msg = await taskApi.upsertOcrSnapshot(
    run.task_id,
    { role: 'assistant', content_text: 'OCR snapshot', payload_json: shaped },
    companyId,
  )
  return { ...run, snapshot_message_id: msg.id }
}

export function resolveTablePayloadAfterVlm(
  run: WorkflowRun,
  base: Record<string, unknown>,
  processedFileIds: string[],
  tablePreset?: ApVlmTablePreset,
): Record<string, unknown> {
  if (processedFileIds.length === 0) {
    const built = buildTablePayloadFromRun(run)
    if (tablePayloadHasRows(built, run.processing_mode)) return built
    return base
  }
  const incoming = buildIncomingForFiles(run, processedFileIds, tablePreset)
  if (!tablePayloadHasRows(base, run.processing_mode)) {
    if (tablePayloadHasRows(incoming, run.processing_mode)) return incoming
    return buildTablePayloadFromRun(run)
  }
  if (!tablePayloadHasRows(incoming, run.processing_mode)) return base
  return mergeTablePayload(base, incoming, processedFileIds, run, tablePreset)
}

export function hasOcrDataOnRun(run: WorkflowRun): boolean {
  const ocrByFile = getOcrByFileFromRun(run)
  return Object.keys(ocrByFile).length > 0
}
