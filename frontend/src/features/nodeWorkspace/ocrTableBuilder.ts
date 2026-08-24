import type { ARAPTransaction } from '../../components/ARAPReview'
import type { BankTransaction } from '../../components/BankStatementReview'
import type { SpreadsheetRow } from '../../components/EditableSpreadsheet'
import type { ApVlmTablePreset } from '../workspace/apComposerOptions'
import {
  buildSpreadsheetRowsFromOcrResult,
  spreadsheetRowsToArapTransactions,
} from '../workspace/buildSpreadsheetFromOcrResult'
import type { WorkflowRun } from './workflowApi'
import { coalesceBankAccountTypeRows } from '../../utils/bankAccountTypeCoalesce'
import { formatBankSourceFile } from '../../utils/bankSourceFile'

function getTxnValue(txn: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = txn[key]
    if (value !== undefined && value !== null && String(value).trim() !== '') return String(value)
  }
  return ''
}

function toNumberOrNull(value: unknown): number | null {
  if (value === null || value === undefined || String(value).trim() === '') return null
  const n = parseFloat(String(value).replace(/,/g, ''))
  return Number.isNaN(n) ? null : n
}

function mergedRowsToBankTransactions(
  merged: Record<string, unknown>[],
  fileName: string,
  rowIndexStart: number,
): BankTransaction[] {
  return merged.map((txn, idx) => {
    const deposit = toNumberOrNull(getTxnValue(txn, ['存入', 'received', 'deposit']))
    const withdrawal = toNumberOrNull(getTxnValue(txn, ['提取', 'spent', 'withdrawal']))
    const balance = toNumberOrNull(getTxnValue(txn, ['原幣結餘', 'balance', '結餘', '结余']))
    const refVal = getTxnValue(txn, ['憑證號', 'reference']) || ''
    const bankDbId = getTxnValue(txn, ['db_id', 'bank_txn_id', 'id']) || String(idx + 1)
    const sourceFile = formatBankSourceFile(
      fileName,
      txn._page,
      getTxnValue(txn, ['source_file', 'file_position']),
    )
    return {
      ...txn,
      id_number: refVal || bankDbId,
      date: getTxnValue(txn, ['日期', 'date', 'transaction_date', 'bank_date']),
      source_file: sourceFile,
      account_type: getTxnValue(txn, ['賬戶類型', '帳戶類型', '账户类型', 'account_type']),
      account_number: getTxnValue(txn, ['account_number']),
      deposit,
      withdrawal,
      balance: balance ?? undefined,
      particulars: getTxnValue(txn, ['備註', 'description', 'memo', 'description_raw']),
      currency: getTxnValue(txn, ['幣別', 'currency']) || 'HKD',
      categorise: getTxnValue(txn, ['categorise', '分類', 'category']),
      reference: refVal,
      _row: rowIndexStart + idx,
    } as BankTransaction
  })
}

function dictRows(raw: unknown): Record<string, unknown>[] {
  if (!Array.isArray(raw)) return []
  return raw.filter((r): r is Record<string, unknown> => Boolean(r && typeof r === 'object'))
}

function rowsFromEnhanced(raw: unknown): Record<string, unknown>[] {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
  const enhanced = raw as Record<string, unknown>
  const rows: Record<string, unknown>[] = []
  rows.push(...dictRows(enhanced.tsv_rows))
  rows.push(...dictRows(enhanced.transactions))
  rows.push(...dictRows(enhanced.rows))
  return rows
}

function stampPageOnRows(rows: Record<string, unknown>[], pageNum: unknown): Record<string, unknown>[] {
  const page = Number(pageNum)
  if (!Number.isFinite(page) || page < 1) return rows
  for (const row of rows) {
    if (row._page == null) row._page = page
  }
  return rows
}

/** If page payload has image_quality but row lacks extraction_provenance.image_quality, attach it. */
function stampImageQualityOnRows(
  rows: Record<string, unknown>[],
  pageObj: Record<string, unknown>,
): Record<string, unknown>[] {
  const pageIq = pageObj.image_quality
  if (!pageIq || typeof pageIq !== 'object' || Array.isArray(pageIq)) return rows
  for (const row of rows) {
    const prov =
      row.extraction_provenance && typeof row.extraction_provenance === 'object'
        ? (row.extraction_provenance as Record<string, unknown>)
        : null
    if (prov?.image_quality && typeof prov.image_quality === 'object') continue
    row.extraction_provenance = { ...(prov ?? {}), image_quality: pageIq }
  }
  return rows
}

export function rowsFromOcrPayload(payload: Record<string, unknown>): Record<string, unknown>[] {
  const rows: Record<string, unknown>[] = []
  rows.push(...dictRows(payload.tsv_rows))
  rows.push(...dictRows(payload.transactions))
  rows.push(...rowsFromEnhanced(payload.ai_enhanced))
  const pages = payload.pages
  if (Array.isArray(pages)) {
    for (const page of pages) {
      if (!page || typeof page !== 'object' || Array.isArray(page)) continue
      const pageObj = page as Record<string, unknown>
      const pageRows: Record<string, unknown>[] = []
      pageRows.push(...dictRows(pageObj.rows))
      pageRows.push(...rowsFromEnhanced(pageObj.ai_enhanced))
      stampImageQualityOnRows(pageRows, pageObj)
      rows.push(...stampPageOnRows(pageRows, pageObj.page))
    }
  }
  return rows
}

function ocrByFileFromRunFiles(run: WorkflowRun): Record<string, Record<string, unknown>[]> {
  const out: Record<string, Record<string, unknown>[]> = {}
  for (const file of run.files) {
    if (file.file_status !== 'ok') continue
    const payload = file.result_summary_json
    if (!payload || typeof payload !== 'object') continue
    const rows = rowsFromOcrPayload(payload)
    if (rows.length > 0) out[file.task_file_id] = rows
  }
  return out
}

function ocrByFileFromNodeState(
  raw: unknown,
  run: WorkflowRun,
  options?: { includeWorkflow?: boolean },
): Record<string, Record<string, unknown>[]> {
  const includeWorkflow = options?.includeWorkflow ?? false
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
  const fileIds = new Set(run.files.map(f => f.task_file_id))
  const out: Record<string, Record<string, unknown>[]> = {}
  for (const [fileId, rows] of Object.entries(raw as Record<string, unknown>)) {
    if (!Array.isArray(rows)) continue
    if (fileId === 'workflow') {
      if (!includeWorkflow) continue
    } else if (!fileIds.has(fileId)) {
      continue
    }
    const clean = rows.filter((r): r is Record<string, unknown> => Boolean(r && typeof r === 'object'))
    if (clean.length > 0) out[fileId] = clean
  }
  return out
}

function workflowRowsFromNodeState(states: Record<string, unknown>): Record<string, unknown>[] {
  const raw = states.ocr_by_file
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    const workflow = (raw as Record<string, unknown>).workflow
    if (Array.isArray(workflow)) {
      const clean = workflow.filter((r): r is Record<string, unknown> => Boolean(r && typeof r === 'object'))
      if (clean.length > 0) return clean
    }
  }
  const merged = states.merged_ocr
  if (Array.isArray(merged)) {
    const clean = merged.filter((r): r is Record<string, unknown> => Boolean(r && typeof r === 'object'))
    if (clean.length > 0) return clean
  }
  return []
}

function rowsHavePage(rows: Record<string, unknown>[]): boolean {
  return rows.some(r => r._page != null)
}

/** Prefer per-file OCR rows stamped with `_page` when node state rows lack page info. */
function preferRowsWithPageFromFiles(
  fromState: Record<string, Record<string, unknown>[]>,
  fromFiles: Record<string, Record<string, unknown>[]>,
): Record<string, Record<string, unknown>[]> {
  const out: Record<string, Record<string, unknown>[]> = { ...fromState }
  for (const [fileId, stateRows] of Object.entries(fromState)) {
    const fileRows = fromFiles[fileId]
    if (!fileRows?.length || rowsHavePage(stateRows)) continue
    if (rowsHavePage(fileRows)) out[fileId] = fileRows
  }
  return out
}

export function getOcrByFileFromRun(run: WorkflowRun): Record<string, Record<string, unknown>[]> {
  const states = run.node_states_json ?? {}
  const isMergeSource = states.table_source === 'merge'
  const fromFiles = ocrByFileFromRunFiles(run)

  if (isMergeSource) {
    const fromState = ocrByFileFromNodeState(states.ocr_by_file, run, { includeWorkflow: true })
    if (Object.keys(fromState).length > 0) {
      return preferRowsWithPageFromFiles(fromState, fromFiles)
    }
    const workflowRows = workflowRowsFromNodeState(states)
    if (workflowRows.length > 0) return { workflow: workflowRows }
    if (Object.keys(fromFiles).length > 0) return fromFiles
    return {}
  }

  const fromState = ocrByFileFromNodeState(states.ocr_by_file, run)
  if (Object.keys(fromState).length > 0) {
    return preferRowsWithPageFromFiles(fromState, fromFiles)
  }

  if (Object.keys(fromFiles).length > 0) return fromFiles

  const workflowRows = workflowRowsFromNodeState(states)
  if (workflowRows.length > 0) return { workflow: workflowRows }
  return {}
}

function fileNameForRun(run: WorkflowRun, taskFileId: string): string {
  const f = run.files.find(x => x.task_file_id === taskFileId)
  return f?.original_filename?.trim() || taskFileId
}

function buildPayloadForFileRows(
  run: WorkflowRun,
  taskFileId: string,
  rowRecords: Record<string, unknown>[],
  rowIndexStart: number,
  tablePreset?: ApVlmTablePreset,
): Record<string, unknown> {
  const mode = (run.processing_mode || 'AR').toUpperCase()
  const fileName = fileNameForRun(run, taskFileId)
  const normalizedRows =
    mode === 'BANK' ? coalesceBankAccountTypeRows([...rowRecords]) : rowRecords
  const ocrResult =
    mode === 'BANK'
      ? { ai_enhanced: { transactions: normalizedRows } }
      : { ai_enhanced: { tsv_rows: normalizedRows } }
  const { spreadsheetData: rows } = buildSpreadsheetRowsFromOcrResult({
    fileId: taskFileId,
    fileName,
    result: ocrResult,
    processingMode: mode,
    rowIndexStart,
  })
  const fileRef = { id: taskFileId, name: fileName }
  if (mode === 'BANK') {
    const bankTxns = mergedRowsToBankTransactions(normalizedRows, fileName, rowIndexStart)
    return {
      spreadsheetData: rows,
      bankTransactions: bankTxns,
      fileRefs: [fileRef],
    }
  }
  const arap = spreadsheetRowsToArapTransactions(rows, mode)
  return {
    spreadsheetData: rows,
    arapTransactions: arap,
    arapFilename: fileName,
    fileRefs: [fileRef],
    ...(tablePreset ? { apVlmTablePreset: tablePreset } : {}),
  }
}

export function buildTablePayloadFromOcrByFile(
  run: WorkflowRun,
  fileIds?: string[],
  tablePreset?: ApVlmTablePreset,
): Record<string, unknown> {
  const mode = (run.processing_mode || 'AR').toUpperCase()
  const ocrByFile = getOcrByFileFromRun(run)
  const ids =
    fileIds && fileIds.length > 0
      ? fileIds.filter(id => ocrByFile[id]?.length)
      : Object.keys(ocrByFile)
  if (ids.length === 0) {
    return {
      spreadsheetData: [] as SpreadsheetRow[],
      arapTransactions: [] as ARAPTransaction[],
      bankTransactions: [] as BankTransaction[],
      arapFilename: 'workflow',
      fileRefs: [] as { id: string; name: string }[],
    }
  }

  let allRows: SpreadsheetRow[] = []
  let allBank: BankTransaction[] = []
  let allArap: ARAPTransaction[] = []
  const fileRefs: { id: string; name: string }[] = []
  let rowStart = 1

  for (const fileId of ids) {
    const rowRecords = ocrByFile[fileId] ?? []
    if (!rowRecords.length) continue
    const part = buildPayloadForFileRows(run, fileId, rowRecords, rowStart, tablePreset)
    allRows = [...allRows, ...((part.spreadsheetData as SpreadsheetRow[]) ?? [])]
    if (mode === 'BANK') {
      allBank = [...allBank, ...((part.bankTransactions as BankTransaction[]) ?? [])]
    } else {
      allArap = [...allArap, ...((part.arapTransactions as ARAPTransaction[]) ?? [])]
    }
    const refs = part.fileRefs as { id: string; name: string }[] | undefined
    if (refs?.[0]) fileRefs.push(refs[0])
    rowStart = allRows.length + 1
  }

  if (mode === 'BANK') {
    if (allBank.length > 0) {
      allBank = coalesceBankAccountTypeRows(allBank.map(t => ({ ...t }))) as BankTransaction[]
    }
    return { spreadsheetData: allRows, bankTransactions: allBank, fileRefs }
  }
  return {
    spreadsheetData: allRows,
    arapTransactions: allArap,
    arapFilename: fileRefs.length === 1 ? fileRefs[0]!.name : 'workflow',
    fileRefs,
    ...(tablePreset ? { apVlmTablePreset: tablePreset } : {}),
  }
}

export function buildTablePayloadFromRun(run: WorkflowRun): Record<string, unknown> {
  const built = buildTablePayloadFromOcrByFile(run)
  const hasRows =
    ((built.arapTransactions as ARAPTransaction[] | undefined)?.length ?? 0) > 0 ||
    ((built.bankTransactions as BankTransaction[] | undefined)?.length ?? 0) > 0 ||
    ((built.spreadsheetData as SpreadsheetRow[] | undefined)?.length ?? 0) > 0
  if (hasRows) return built

  return {
    spreadsheetData: [],
    arapTransactions: [] as ARAPTransaction[],
    bankTransactions: [] as BankTransaction[],
    arapFilename: 'workflow',
    fileRefs: [],
  }
}

export function buildIncomingForFiles(
  run: WorkflowRun,
  fileIds: string[],
  tablePreset?: ApVlmTablePreset,
): Record<string, unknown> {
  return buildTablePayloadFromOcrByFile(run, fileIds, tablePreset)
}
