import type { ARAPTransaction } from '../../components/ARAPReview'
import type { SpreadsheetRow } from '../../components/EditableSpreadsheet'
import type { ApVlmTablePreset } from '../workspace/apComposerOptions'
import { batchOcrSnapshotMessageId, mergeSpreadsheetRowsForFile } from '../workspace/batchOcrSnapshot'
import { spreadsheetRowsToArapTransactions } from '../workspace/buildSpreadsheetFromOcrResult'
import { taskApi } from '../../services/api'
import { buildIncomingForFiles } from './ocrTableBuilder'
import { hasBankSourcePageSuffix } from '../../utils/bankSourceFile'
import { committedTimelineBatches } from './runFileBatches'
import {
  hasOcrDataOnRun,
  mergeTablePayload,
  snapshotPayloadShape,
  tablePayloadHasRows,
} from './tablePayloadMerge'
import type { WorkflowRun } from './workflowApi'

export { batchOcrSnapshotMessageId as batchSnapshotMessageId }

const POST_APPROVE_RUN_STATUSES = new Set(['coa_running', 'completed', 'done', 'saved'])

/** Approved table rows stored on the run after Processing Approve. */
export function approvedPayloadFromRun(run: WorkflowRun): Record<string, unknown> | null {
  const states = run.node_states_json
  if (!states || typeof states !== 'object') return null
  const raw = (states as Record<string, unknown>).approved_payload
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const payload = raw as Record<string, unknown>
  if (!tablePayloadHasRows(payload, run.processing_mode)) return null
  return payload
}

export function runHasLockedApprovedTable(run: WorkflowRun): boolean {
  const status = (run.run_status || '').toLowerCase()
  return POST_APPROVE_RUN_STATUSES.has(status) && approvedPayloadFromRun(run) != null
}

/** Per-batch payloads from approved_payload; null when run is still pre-approve. */
export function batchPayloadsFromApprovedRun(
  run: WorkflowRun,
): Record<string, Record<string, unknown>> | null {
  const approved = approvedPayloadFromRun(run)
  if (!approved || !runHasLockedApprovedTable(run)) return null
  return mapCombinedPayloadToBatches(run, approved)
}

export function frozenPresetForBatch(run: WorkflowRun, uploadBatchId: string): ApVlmTablePreset {
  const batchFiles = run.files.filter(f => (f.upload_batch_id ?? f.task_file_id) === uploadBatchId)
  const preset = batchFiles.find(f => f.batch_table_preset)?.batch_table_preset
  if (preset === 'ap_table' || preset === 'default') return preset
  return 'default'
}

export function tablePresetLabel(
  preset: ApVlmTablePreset | string | null | undefined,
  processingMode?: string | null,
): string {
  if (preset === 'ap_table') return 'AP table'
  const m = (processingMode || '').toUpperCase()
  if (m === 'AR') return 'Standard AR columns'
  return 'Default'
}

export function fileIdsInBatch(run: WorkflowRun, uploadBatchId: string): string[] {
  return run.files
    .filter(f => f.batch_committed_at && (f.upload_batch_id ?? f.task_file_id) === uploadBatchId)
    .map(f => f.task_file_id)
}

export async function loadBatchTablePayload(
  taskId: string,
  uploadBatchId: string,
  companyId?: string | null,
): Promise<Record<string, unknown> | null> {
  const messages = await taskApi.getMessages(taskId, companyId)
  const msgId = batchOcrSnapshotMessageId(uploadBatchId)
  const snap = messages.find(m => m.id === msgId)
  if (snap?.payload_json && typeof snap.payload_json === 'object') {
    return snap.payload_json as Record<string, unknown>
  }
  return null
}

/** Overlay Books-module saves (moduleSavedAt) onto approved/frozen batch slices. */
export function preferModuleAuthoritativeBatches(
  base: Record<string, Record<string, unknown>>,
  snapshots: Record<string, Record<string, unknown>>,
): Record<string, Record<string, unknown>> {
  const out = { ...base }
  for (const [batchId, snap] of Object.entries(snapshots)) {
    if (isModuleAuthoritativeSnapshot(snap)) out[batchId] = snap
  }
  return out
}

function batchSnapshotsFromMessages(
  messages: { id: string; content_type?: string; payload_json?: unknown }[],
): Record<string, Record<string, unknown>> {
  const out: Record<string, Record<string, unknown>> = {}
  for (const m of messages) {
    if (m.content_type !== 'ocr_snapshot' || !m.id.startsWith('ocr-batch-')) continue
    const batchId = m.id.slice('ocr-batch-'.length)
    if (m.payload_json && typeof m.payload_json === 'object') {
      out[batchId] = m.payload_json as Record<string, unknown>
    }
  }
  return out
}

export async function loadAllBatchTablePayloads(
  run: WorkflowRun,
  companyId?: string | null,
): Promise<Record<string, Record<string, unknown>>> {
  const fromApproved = batchPayloadsFromApprovedRun(run)

  if (!run.task_id) return fromApproved ?? {}
  const messages = await taskApi.getMessages(run.task_id, companyId)
  const fromSnapshots = batchSnapshotsFromMessages(messages)

  // Post-approve Books Save writes ocr-batch-* with moduleSavedAt but does not
  // update approved_payload. Prefer those snapshots so Add Row / edits persist.
  if (fromApproved) {
    return preferModuleAuthoritativeBatches(fromApproved, fromSnapshots)
  }

  const status = (run.run_status || '').toLowerCase()
  if (POST_APPROVE_RUN_STATUSES.has(status) && run.snapshot_message_id) {
    const snap = messages.find(m => m.id === run.snapshot_message_id)
    if (snap?.payload_json && typeof snap.payload_json === 'object') {
      return preferModuleAuthoritativeBatches(
        mapCombinedPayloadToBatches(run, snap.payload_json as Record<string, unknown>),
        fromSnapshots,
      )
    }
  }

  if (Object.keys(fromSnapshots).length > 0) return fromSnapshots

  if (run.snapshot_message_id) {
    const snap = messages.find(m => m.id === run.snapshot_message_id)
    if (snap?.payload_json && typeof snap.payload_json === 'object') {
      return mapCombinedPayloadToBatches(run, snap.payload_json as Record<string, unknown>)
    }
  }
  return {}
}

export function batchesMissingTableRows(
  run: WorkflowRun,
  payloads: Record<string, Record<string, unknown>>,
): boolean {
  return committedTimelineBatches(run.files).some(
    b => !tablePayloadHasRows(payloads[b.uploadBatchId] ?? {}, run.processing_mode),
  )
}

export function mergeBatchTablePayloads(
  run: WorkflowRun,
  primary: Record<string, Record<string, unknown>>,
  fallback: Record<string, Record<string, unknown>>,
): Record<string, Record<string, unknown>> {
  const out: Record<string, Record<string, unknown>> = {}
  for (const batch of committedTimelineBatches(run.files)) {
    const id = batch.uploadBatchId
    const primaryPayload = primary[id]
    const fallbackPayload = fallback[id]
    if (tablePayloadHasRows(primaryPayload ?? {}, run.processing_mode)) {
      out[id] = primaryPayload!
    } else if (tablePayloadHasRows(fallbackPayload ?? {}, run.processing_mode)) {
      out[id] = fallbackPayload!
    }
  }
  return out
}

export function buildBatchTablePayloadsFromRun(
  run: WorkflowRun,
): Record<string, Record<string, unknown>> {
  const out: Record<string, Record<string, unknown>> = {}
  for (const batch of committedTimelineBatches(run.files)) {
    const fileIds = batch.files.map(f => f.task_file_id)
    const payload = resolveBatchTablePayloadAfterVlm(run, batch.uploadBatchId, {}, fileIds)
    if (tablePayloadHasRows(payload, run.processing_mode)) {
      out[batch.uploadBatchId] = payload
    }
  }
  return out
}

/** True when the destination module saved this batch (edits, deploy codes, add/delete). */
export function isModuleAuthoritativeSnapshot(payload: Record<string, unknown> | undefined): boolean {
  if (!payload) return false
  return typeof payload.moduleSavedAt === 'string' && payload.moduleSavedAt.length > 0
}

/**
 * Recover rows dropped by a lossy approval/consolidation. The run's extraction
 * state (node_states_json) is the source of truth for how many rows were
 * extracted per batch. If a stored snapshot has fewer rows than the
 * reconstruction, prefer the reconstruction so no extracted row stays hidden.
 * At awaiting_review the counts match, so this is a no-op there; user deletions
 * flow through the approved payload, not these per-batch snapshots.
 * Module-saved batches (moduleSavedAt) are never replaced.
 */
function bankSourcePageCount(payload: Record<string, unknown> | undefined): number {
  const bank = (payload?.bankTransactions as { source_file?: string }[] | undefined) ?? []
  return bank.filter(t => hasBankSourcePageSuffix(t.source_file)).length
}

/** True when row already carries AQ image_quality for Table Review chips. */
function rowHasImageQuality(row: ARAPTransaction | Record<string, unknown>): boolean {
  const raw = row as Record<string, unknown>
  const prov =
    raw.extraction_provenance && typeof raw.extraction_provenance === 'object'
      ? (raw.extraction_provenance as Record<string, unknown>)
      : null
  if (prov?.image_quality && typeof prov.image_quality === 'object') return true
  return Boolean(raw.image_quality && typeof raw.image_quality === 'object')
}

function arapIdentityKeys(row: ARAPTransaction): string[] {
  const keys = new Set<string>()
  const idn = String(row.id_number ?? '').trim()
  if (idn) keys.add(idn)
  const voucher = String((row as Record<string, unknown>).voucher_no ?? '').trim()
  if (voucher) keys.add(voucher)
  if (idn.startsWith('AR-') || idn.startsWith('AP-')) keys.add(idn.slice(3))
  const memo = String(row.memo ?? '').trim()
  const amount = String(row.amount ?? row.debit ?? row.credit ?? '').trim()
  const date = String(row.date ?? '').trim()
  if (memo || amount || date) keys.add(`${date}|${amount}|${memo}`)
  return [...keys]
}

/**
 * Copy extraction_provenance (AQ) from OCR rebuild onto persisted snapshot rows
 * that were saved before provenance was passed through the spreadsheet mapper.
 */
export function enrichArapImageQualityFromRebuild(
  current: Record<string, unknown> | undefined,
  rebuilt: Record<string, unknown> | undefined,
): Record<string, unknown> | undefined {
  if (!current || !rebuilt) return current
  const curArap = (current.arapTransactions as ARAPTransaction[] | undefined) ?? []
  const rebArap = (rebuilt.arapTransactions as ARAPTransaction[] | undefined) ?? []
  if (!curArap.length || !rebArap.length) return current
  if (!curArap.some(r => !rowHasImageQuality(r))) return current
  if (!rebArap.some(r => rowHasImageQuality(r))) return current

  const rebByKey = new Map<string, ARAPTransaction>()
  for (const row of rebArap) {
    for (const key of arapIdentityKeys(row)) {
      if (key && !rebByKey.has(key)) rebByKey.set(key, row)
    }
  }

  let changed = false
  const next = curArap.map(row => {
    if (rowHasImageQuality(row)) return row
    let match: ARAPTransaction | undefined
    for (const key of arapIdentityKeys(row)) {
      match = match ?? (key ? rebByKey.get(key) : undefined)
    }
    if (!match || !rowHasImageQuality(match)) return row
    changed = true
    const out: ARAPTransaction = { ...row }
    if (match.extraction_provenance) out.extraction_provenance = match.extraction_provenance
    const m = match as Record<string, unknown>
    if (typeof m.needs_review === 'boolean') (out as Record<string, unknown>).needs_review = m.needs_review
    if (Array.isArray(m.validation_flags)) {
      ;(out as Record<string, unknown>).validation_flags = m.validation_flags
    }
    return out
  })
  if (!changed) return current
  return { ...current, arapTransactions: next }
}

export function reconcileBatchPayloadsWithRun(
  run: WorkflowRun,
  loaded: Record<string, Record<string, unknown>>,
): Record<string, Record<string, unknown>> {
  if (runHasLockedApprovedTable(run)) return loaded
  if (!hasOcrDataOnRun(run)) return loaded
  const rebuilt = buildBatchTablePayloadsFromRun(run)
  if (Object.keys(rebuilt).length === 0) return loaded
  const isBank = (run.processing_mode || '').toUpperCase() === 'BANK'
  const out: Record<string, Record<string, unknown>> = { ...loaded }
  for (const [batchId, rebuiltPayload] of Object.entries(rebuilt)) {
    if (isModuleAuthoritativeSnapshot(loaded[batchId])) {
      // Still allow AQ provenance fill-in; do not replace edited cells.
      const enriched = enrichArapImageQualityFromRebuild(out[batchId], rebuiltPayload)
      if (enriched) out[batchId] = enriched
      continue
    }
    const currentCount = batchPayloadRowCount(loaded[batchId], run.processing_mode)
    const rebuiltCount = batchPayloadRowCount(rebuiltPayload, run.processing_mode)
    if (isBank) {
      const currentPages = bankSourcePageCount(loaded[batchId])
      const rebuiltPages = bankSourcePageCount(rebuiltPayload)
      if (rebuiltPages > currentPages) {
        out[batchId] = rebuiltPayload
        continue
      }
    }
    if (rebuiltCount > currentCount) {
      out[batchId] = rebuiltPayload
      continue
    }
    const enriched = enrichArapImageQualityFromRebuild(out[batchId], rebuiltPayload)
    if (enriched) out[batchId] = enriched
  }
  return out
}

export function resolveBatchTablePayloadAfterVlm(
  run: WorkflowRun,
  uploadBatchId: string,
  base: Record<string, unknown>,
  processedFileIds: string[],
): Record<string, unknown> {
  const allBatchIds = fileIdsInBatch(run, uploadBatchId)
  if (!allBatchIds.length || !processedFileIds.length) return base

  const preset = frozenPresetForBatch(run, uploadBatchId)
  const fileIds = processedFileIds.includes('workflow')
    ? ['workflow']
    : allBatchIds.filter(id => processedFileIds.includes(id))
  if (!fileIds.length) return base

  let incoming = buildIncomingForFiles(run, fileIds, preset)
  let mergeIds = fileIds
  if (!tablePayloadHasRows(incoming, run.processing_mode) && !fileIds.includes('workflow')) {
    const workflowIncoming = buildIncomingForFiles(run, ['workflow'], preset)
    if (tablePayloadHasRows(workflowIncoming, run.processing_mode)) {
      incoming = workflowIncoming
      mergeIds = ['workflow']
    }
  }
  if (!tablePayloadHasRows(incoming, run.processing_mode)) return base
  if (!tablePayloadHasRows(base, run.processing_mode)) return incoming
  return mergeTablePayload(base, incoming, mergeIds, run, preset)
}

export async function persistBatchTableSnapshot(
  run: WorkflowRun,
  uploadBatchId: string,
  payload: Record<string, unknown>,
  tablePreset: ApVlmTablePreset,
  companyId?: string | null,
  options?: { fromModule?: boolean },
): Promise<void> {
  if (!run.task_id) return
  const preset = tablePreset ?? frozenPresetForBatch(run, uploadBatchId)
  const withMeta =
    options?.fromModule === true
      ? { ...payload, moduleSavedAt: new Date().toISOString() }
      : payload
  const shaped = snapshotPayloadShape(
    { ...withMeta, uploadBatchId },
    run,
    preset,
  )
  await taskApi.upsertBatchOcrSnapshot(
    run.task_id,
    uploadBatchId,
    { role: 'assistant', content_text: 'OCR snapshot', payload_json: shaped },
    companyId,
  )
}

export function combineBatchTablePayloads(
  payloads: Record<string, Record<string, unknown>>,
  run: WorkflowRun,
): Record<string, unknown> {
  const batches = committedTimelineBatches(run.files)
  let combined: Record<string, unknown> = {}
  for (const batch of batches) {
    const payload = payloads[batch.uploadBatchId]
    if (!payload || !tablePayloadHasRows(payload, run.processing_mode)) continue
    const fileIds = batch.files.map(f => f.task_file_id)
    combined = mergeTablePayload(combined, payload, fileIds, run)
  }
  return combined
}

export type MoveFileRowsResult = {
  payloads: Record<string, Record<string, unknown>>
  moved: number
}

function fileNameForRun(run: WorkflowRun, taskFileId: string): string {
  const f = run.files.find(x => x.task_file_id === taskFileId)
  return f?.original_filename?.trim() || taskFileId
}

function rowMatchesFile(sourceFile: string, fileId: string, fileName: string): boolean {
  const sf = sourceFile.trim()
  if (!sf) return false
  return sf === fileName || sf.startsWith(`${fileName} `) || sf.includes(fileName) || sf.includes(fileId)
}

function arapRowMatchesFile(t: ARAPTransaction, fileId: string, fileName: string): boolean {
  const raw = t as Record<string, unknown>
  const rowId = String(raw['id'] ?? '')
  if (rowId === fileId || rowId.startsWith(`${fileId}-`)) return true

  const sf = String(t.source_file ?? raw['file_position'] ?? '').trim()
  if (sf && rowMatchesFile(sf, fileId, fileName)) return true

  const stem = fileName.replace(/\.[^.]+$/, '')
  if (stem && sf && (sf === stem || sf.startsWith(`${stem} `) || sf.startsWith(`${stem} P`))) {
    return true
  }
  return false
}

function arapRowsForSpreadsheetRows(
  arap: ARAPTransaction[],
  sheet: SpreadsheetRow[],
  rowIds: string[],
): ARAPTransaction[] {
  const moveSet = new Set(rowIds.map(String))
  const byRowId = arap.filter(t => moveSet.has(String((t as Record<string, unknown>)['id'] ?? '')))
  if (byRowId.length > 0) return byRowId

  const indices: number[] = []
  sheet.forEach((r, i) => {
    if (moveSet.has(String(r.id ?? ''))) indices.push(i)
  })
  return indices.map(i => arap[i]).filter((t): t is ARAPTransaction => Boolean(t))
}

function batchPayloadFromCombined(
  run: WorkflowRun,
  uploadBatchId: string,
  payload: Record<string, unknown>,
  fileIds: string[],
): Record<string, unknown> {
  const mode = (run.processing_mode || 'AR').toUpperCase()
  const preset = frozenPresetForBatch(run, uploadBatchId)
  const sheet = (payload.spreadsheetData as SpreadsheetRow[] | undefined) ?? []
  let batchSheet: SpreadsheetRow[] = []
  for (const fileId of fileIds) {
    batchSheet = mergeSpreadsheetRowsForFile(batchSheet, rowsForBatchFile(sheet, fileId), fileId)
  }

  if (mode === 'BANK') {
    const bank =
      (payload.bankTransactions as { source_file?: string; manual_entry?: boolean; upload_batch_id?: string }[] | undefined) ??
      []
    const batchBank = bank.filter(t => {
      if (t.manual_entry === true && String(t.upload_batch_id ?? '') === uploadBatchId) return true
      const sf = String(t.source_file ?? '')
      return fileIds.some(fid => {
        const name = fileNameForRun(run, fid)
        return sf === name || sf.startsWith(`${name} `) || sf.includes(fid)
      })
    })
    return { spreadsheetData: batchSheet, bankTransactions: batchBank, fileRefs: payload.fileRefs }
  }

  let arap = spreadsheetRowsToArapTransactions(batchSheet, mode)
  if (Array.isArray(payload.arapTransactions)) {
    const fromArap = (payload.arapTransactions as ARAPTransaction[]).filter(t => {
      const raw = t as Record<string, unknown>
      if (raw.manual_entry === true && String(raw.upload_batch_id ?? '') === uploadBatchId) return true
      return fileIds.some(fid => arapRowMatchesFile(t, fid, fileNameForRun(run, fid)))
    })
    if (arap.length === 0) {
      arap = fromArap
    } else {
      // Spreadsheet rebuild omits Books Add Row; keep batch-stamped manuals.
      const manuals = fromArap.filter(t => (t as Record<string, unknown>).manual_entry === true)
      if (manuals.length > 0) arap = [...arap, ...manuals]
    }
  }
  return {
    spreadsheetData: batchSheet,
    arapTransactions: arap,
    arapFilename: payload.arapFilename,
    fileRefs: payload.fileRefs,
    apVlmTablePreset: preset,
  }
}

/** Map a post-approve combined payload back to per-batch keys for RunTimeline. */
export function mapCombinedPayloadToBatches(
  run: WorkflowRun,
  payload: Record<string, unknown>,
): Record<string, Record<string, unknown>> {
  const batches = committedTimelineBatches(run.files)
  if (batches.length === 0) {
    return { [`${run.id}-legacy`]: payload }
  }
  if (batches.length === 1) {
    const batchId = batches[0]!.uploadBatchId
    return {
      [batchId]: {
        ...payload,
        apVlmTablePreset: frozenPresetForBatch(run, batchId),
      },
    }
  }
  const out: Record<string, Record<string, unknown>> = {}
  for (const batch of batches) {
    const fileIds = batch.files.map(f => f.task_file_id)
    out[batch.uploadBatchId] = batchPayloadFromCombined(
      run,
      batch.uploadBatchId,
      payload,
      fileIds,
    )
  }
  return out
}

export function moveRowsBetweenBatches(
  sourceBatchId: string,
  targetBatchId: string,
  rowIds: string[],
  payloads: Record<string, Record<string, unknown>>,
  run: WorkflowRun,
): Record<string, Record<string, unknown>> {
  const source = payloads[sourceBatchId]
  if (!source || rowIds.length === 0) return payloads
  const targetPreset = frozenPresetForBatch(run, targetBatchId)
  const target = payloads[targetBatchId] ?? {
    spreadsheetData: [],
    arapTransactions: [],
    apVlmTablePreset: targetPreset,
  }

  const sourceSheet = [...((source.spreadsheetData as SpreadsheetRow[] | undefined) ?? [])]
  const targetSheet = [...((target.spreadsheetData as SpreadsheetRow[] | undefined) ?? [])]
  const moveSet = new Set(rowIds.map(String))
  const moving = sourceSheet.filter(r => moveSet.has(String(r.id ?? '')))
  if (moving.length === 0) return payloads

  const keptSource = sourceSheet.filter(r => !moveSet.has(String(r.id ?? '')))
  const mergedTarget = [...targetSheet, ...moving]
  const mode = (run.processing_mode || 'AR').toUpperCase()

  const sourceArap = (source.arapTransactions as ARAPTransaction[] | undefined) ?? []
  const targetArap = (target.arapTransactions as ARAPTransaction[] | undefined) ?? []
  const movingArap = arapRowsForSpreadsheetRows(sourceArap, sourceSheet, rowIds)
  const movingArapSet = new Set(movingArap)

  const nextSource: Record<string, unknown> = {
    ...source,
    spreadsheetData: keptSource,
    ...(mode === 'BANK'
      ? {}
      : {
          arapTransactions:
            movingArap.length > 0
              ? sourceArap.filter(t => !movingArapSet.has(t))
              : spreadsheetRowsToArapTransactions(keptSource, mode),
        }),
  }
  const nextTarget: Record<string, unknown> = {
    ...target,
    spreadsheetData: mergedTarget,
    apVlmTablePreset: targetPreset,
    ...(mode === 'BANK'
      ? {}
      : {
          arapTransactions:
            movingArap.length > 0
              ? [...targetArap, ...movingArap]
              : spreadsheetRowsToArapTransactions(mergedTarget, mode),
        }),
  }

  return {
    ...payloads,
    [sourceBatchId]: nextSource,
    [targetBatchId]: nextTarget,
  }
}

export function moveFileRowsBetweenBatches(
  sourceBatchId: string,
  targetBatchId: string,
  fileId: string,
  payloads: Record<string, Record<string, unknown>>,
  run: WorkflowRun,
): MoveFileRowsResult {
  const source = payloads[sourceBatchId]
  if (!source) return { payloads, moved: 0 }

  const fileName = fileNameForRun(run, fileId)
  const mode = (run.processing_mode || 'AR').toUpperCase()
  const targetPreset = frozenPresetForBatch(run, targetBatchId)
  const target = payloads[targetBatchId] ?? {
    spreadsheetData: [],
    arapTransactions: [],
    apVlmTablePreset: targetPreset,
  }

  if (mode === 'BANK') {
    const bank = (source.bankTransactions as { source_file?: string }[] | undefined) ?? []
    const movingBank = bank.filter(t => rowMatchesFile(String(t.source_file ?? ''), fileId, fileName))
    if (movingBank.length > 0) {
      const keptBank = bank.filter(t => !rowMatchesFile(String(t.source_file ?? ''), fileId, fileName))
      const targetBank = [
        ...((target.bankTransactions as { source_file?: string }[] | undefined) ?? []),
        ...movingBank,
      ]
      return {
        payloads: {
          ...payloads,
          [sourceBatchId]: { ...source, bankTransactions: keptBank },
          [targetBatchId]: { ...target, bankTransactions: targetBank },
        },
        moved: movingBank.length,
      }
    }
    return { payloads, moved: 0 }
  }

  const sheet = (source.spreadsheetData as SpreadsheetRow[] | undefined) ?? []
  const movingSheet = rowsForBatchFile(sheet, fileId)
  const rowIdSet = new Set(movingSheet.map(r => String(r.id ?? '')))
  const arap = (source.arapTransactions as ARAPTransaction[] | undefined) ?? []
  let movingArap = arap.filter(t => arapRowMatchesFile(t, fileId, fileName))
  if (movingArap.length === 0 && movingSheet.length > 0) {
    movingArap = arapRowsForSpreadsheetRows(
      arap,
      sheet,
      movingSheet.map(r => String(r.id ?? '')),
    )
  }

  // ARAP-first: RunTimeline renders arapTransactions, not spreadsheetData.
  if (movingArap.length > 0) {
    const keptSheet = sheet.filter(r => !rowIdSet.has(String(r.id ?? '')))
    const targetSheet = [
      ...((target.spreadsheetData as SpreadsheetRow[] | undefined) ?? []),
      ...movingSheet,
    ]
    const movingArapSet = new Set(movingArap)
    const keptArap = arap.filter(t => !movingArapSet.has(t))
    const targetArap = [
      ...((target.arapTransactions as ARAPTransaction[]) ?? []),
      ...movingArap,
    ]
    return {
      payloads: {
        ...payloads,
        [sourceBatchId]: {
          ...source,
          spreadsheetData: keptSheet,
          arapTransactions: keptArap,
        },
        [targetBatchId]: {
          ...target,
          spreadsheetData: targetSheet,
          arapTransactions: targetArap,
          apVlmTablePreset: targetPreset,
        },
      },
      moved: movingArap.length,
    }
  }

  const rowIds = movingSheet.map(r => String(r.id ?? ''))
  if (rowIds.length > 0) {
    const nextPayloads = moveRowsBetweenBatches(sourceBatchId, targetBatchId, rowIds, payloads, run)
    if (nextPayloads !== payloads) {
      return { payloads: nextPayloads, moved: rowIds.length }
    }
  }

  return { payloads, moved: 0 }
}

export function batchPayloadRowCount(payload: Record<string, unknown> | undefined, mode: string): number {
  if (!payload) return 0
  if (!tablePayloadHasRows(payload, mode)) return 0
  if ((mode || '').toUpperCase() === 'BANK') {
    const bank = payload.bankTransactions as unknown[] | undefined
    if (bank?.length) return bank.length
  }
  const arap = payload.arapTransactions as ARAPTransaction[] | undefined
  if (arap?.length) return arap.length
  const sheet = payload.spreadsheetData as SpreadsheetRow[] | undefined
  return sheet?.length ?? 0
}

export function rowsForBatchFile(sheet: SpreadsheetRow[], fileId: string): SpreadsheetRow[] {
  const prefix = `${fileId}-`
  return sheet.filter(r => {
    const id = String(r.id ?? '')
    return id === fileId || id.startsWith(prefix)
  })
}

export { mergeSpreadsheetRowsForFile }
