/** Resolve on-demand AQ crop preview params from a Table Review row (no stored crop assets). */

export type CropPreviewFile = {
  taskFileId: string
  originalFilename?: string | null
}

export type ReceiptCropRequest = {
  taskFileId: string
  page: number
  regionNorm: { x: number; y: number; w: number; h: number } | null
  regionBbox: { x: number; y: number; w: number; h: number } | null
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null
}

function asNormRegion(v: unknown): { x: number; y: number; w: number; h: number } | null {
  const r = asRecord(v)
  if (!r) return null
  const x = Number(r.x)
  const y = Number(r.y)
  const w = Number(r.w)
  const h = Number(r.h)
  if (![x, y, w, h].every(n => Number.isFinite(n)) || w <= 0 || h <= 0) return null
  return { x, y, w, h }
}

function asBbox(v: unknown): { x: number; y: number; w: number; h: number } | null {
  const r = asRecord(v)
  if (!r) return null
  const x = Number(r.x)
  const y = Number(r.y)
  const w = Number(r.w)
  const h = Number(r.h)
  if (![x, y, w, h].every(n => Number.isFinite(n)) || w <= 0 || h <= 0) return null
  return { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) }
}

function sourceStem(sourceFile: string): string {
  const base = sourceFile.replace(/\s+P\d+(?:-R\d+)?\b/i, '').trim()
  const slash = Math.max(base.lastIndexOf('/'), base.lastIndexOf('\\'))
  return slash >= 0 ? base.slice(slash + 1) : base
}

function pageFromSourceFile(sourceFile: string): number | null {
  const m = sourceFile.match(/\bP(\d+)(?:-R\d+)?\b/i)
  if (!m) return null
  const n = Number(m[1])
  return Number.isFinite(n) && n >= 1 ? n : null
}

function receiptIndexFromSourceFile(sourceFile: string): number | null {
  const m = sourceFile.match(/\bP\d+-R(\d+)\b/i)
  if (!m) return null
  const n = Number(m[1])
  return Number.isFinite(n) && n >= 1 ? n : null
}

/** Match row source_file / id prefix to a task file id from the active run. */
export function resolveCropTaskFileId(
  row: Record<string, unknown>,
  files: CropPreviewFile[],
): string | null {
  if (!files.length) return null
  const explicit = String(row.task_file_id ?? row.source_file_id ?? '').trim()
  if (explicit && files.some(f => f.taskFileId === explicit)) return explicit

  const rowId = String(row.id ?? '').trim()
  if (rowId) {
    const hit = files.find(f => rowId === f.taskFileId || rowId.startsWith(`${f.taskFileId}-`))
    if (hit) return hit.taskFileId
  }

  const source = String(row.source_file ?? row.file_position ?? '').trim()
  if (!source) return files[0]!.taskFileId
  const stem = sourceStem(source).toLowerCase()
  const byName = files.find(f => {
    const name = (f.originalFilename ?? '').trim().toLowerCase()
    return name && (name === stem || stem.endsWith(name) || name.endsWith(stem))
  })
  if (byName) return byName.taskFileId
  return files[0]!.taskFileId
}

/** True when the row has M-VDU crop region provenance (target receipt box). */
export function rowHasReceiptCropRegion(row: Record<string, unknown>): boolean {
  const prov = asRecord(row.extraction_provenance)
  if (!prov) return false
  return Boolean(asNormRegion(prov.receipt_region_norm) || asBbox(prov.receipt_bbox_pixels))
}

/** M-VDU / multi-receipt row (region, receipt_index, or Source Page P#-R#). */
export function rowLooksLikeMvduReceipt(row: Record<string, unknown>): boolean {
  if (rowHasReceiptCropRegion(row)) return true
  const prov = asRecord(row.extraction_provenance)
  const fromProv = Number(prov?.receipt_index ?? row.receipt_index)
  if (Number.isFinite(fromProv) && fromProv >= 1) return true
  const src = String(row.source_file ?? row.file_position ?? '')
  return receiptIndexFromSourceFile(src) != null
}

/**
 * Process Live output: Target crop only when the row has a stored receipt box.
 * Full-page fallback is not a verified crop.
 */
export function rowCanShowReceiptCropPreview(
  row: Record<string, unknown>,
  files: CropPreviewFile[],
): boolean {
  return (
    rowHasReceiptCropRegion(row) &&
    files.length > 0 &&
    resolveCropTaskFileId(row, files) != null
  )
}

export function buildReceiptCropRequest(
  row: Record<string, unknown>,
  files: CropPreviewFile[],
): ReceiptCropRequest | null {
  const taskFileId = resolveCropTaskFileId(row, files)
  if (!taskFileId) return null

  const prov = asRecord(row.extraction_provenance)
  const regionNorm = asNormRegion(prov?.receipt_region_norm)
  const regionBbox = asBbox(prov?.receipt_bbox_pixels)
  const pageFromProv = Number(prov?.source_pdf_page)
  const pageFromSource = pageFromSourceFile(String(row.source_file ?? row.file_position ?? ''))
  const pageFromRow = Number(row._page)
  const page =
    (Number.isFinite(pageFromProv) && pageFromProv >= 1 && pageFromProv) ||
    pageFromSource ||
    (Number.isFinite(pageFromRow) && pageFromRow >= 1 && pageFromRow) ||
    1

  return {
    taskFileId,
    page: Number(page),
    regionNorm,
    regionBbox,
  }
}
