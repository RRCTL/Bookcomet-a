import { useEffect, useMemo, useState } from 'react'
import { taskApi } from '../../services/api'
import { buildReceiptCropRequest, type CropPreviewFile } from '../../utils/imageQualityCrop'
import type { CropPreviewBox, RegionNorm } from './types'
import { normalizeIncomingBoxes } from './boxOps'

type RowLike = Record<string, unknown> & { _id: string | number }

type Props = {
  taskId: string
  companyId?: string | null
  files: CropPreviewFile[]
  rows: RowLike[]
  selectedRowIds: Set<string | number>
  onSelectRowIds: (ids: Set<string | number>) => void
  /** Optional crop_preview.boxes from latest OCR result for full-page overlay. */
  previewBoxes?: unknown
  page?: number
}

function regionFromRow(row: RowLike): RegionNorm | null {
  const prov = row.extraction_provenance
  if (!prov || typeof prov !== 'object') return null
  const p = prov as Record<string, unknown>
  const n = p.receipt_region_norm
  if (n && typeof n === 'object') {
    const r = n as Record<string, unknown>
    const x = Number(r.x)
    const y = Number(r.y)
    const w = Number(r.w)
    const h = Number(r.h)
    if ([x, y, w, h].every(Number.isFinite) && w > 0 && h > 0) return { x, y, w, h }
  }
  return null
}

function receiptIndexFromRow(row: RowLike): number | null {
  const prov = row.extraction_provenance
  if (prov && typeof prov === 'object') {
    const n = Number((prov as Record<string, unknown>).receipt_index)
    if (Number.isFinite(n) && n >= 1) return n
  }
  const src = String(row.source_file ?? row.file_position ?? '')
  const m = src.match(/\bP\d+-R(\d+)\b/i)
  if (m) return Number(m[1])
  return null
}

export function MvduReviewPagePane({
  taskId,
  companyId,
  files,
  rows,
  selectedRowIds,
  onSelectRowIds,
  previewBoxes,
  page = 1,
}: Props) {
  const [mode, setMode] = useState<'full' | 'receipt'>('full')
  const [pageUrl, setPageUrl] = useState<string | null>(null)
  const [cropUrl, setCropUrl] = useState<string | null>(null)

  const boxes: CropPreviewBox[] = useMemo(() => {
    const fromPreview = normalizeIncomingBoxes(previewBoxes)
    if (fromPreview.length) return fromPreview
    // Reconstruct from row provenance when crop_preview was not retained.
    const byIndex = new Map<number, CropPreviewBox>()
    for (const row of rows) {
      const idx = receiptIndexFromRow(row)
      const norm = regionFromRow(row)
      if (idx == null || !norm || byIndex.has(idx)) continue
      byIndex.set(idx, {
        id: `rowbox_${idx}`,
        index: idx,
        page,
        region_norm: norm,
        rotation_deg: 0,
        extract_status: 'done',
      })
    }
    return [...byIndex.values()].sort((a, b) => a.index - b.index)
  }, [previewBoxes, rows, page])

  const selectedRow = useMemo(() => {
    if (selectedRowIds.size !== 1) return null
    const id = Array.from(selectedRowIds)[0]
    return rows.find(r => r._id === id) ?? null
  }, [rows, selectedRowIds])

  const activeIndex = selectedRow ? receiptIndexFromRow(selectedRow) : null

  const primaryFileId = useMemo(() => {
    if (selectedRow) {
      const req = buildReceiptCropRequest(selectedRow, files)
      if (req) return req.taskFileId
    }
    return files[0]?.taskFileId ?? null
  }, [selectedRow, files])

  useEffect(() => {
    if (!primaryFileId) return
    let cancelled = false
    let url: string | null = null
    void taskApi
      .downloadReceiptCrop(taskId, primaryFileId, { page, companyId })
      .then(blob => {
        if (cancelled) return
        url = URL.createObjectURL(blob)
        setPageUrl(url)
      })
      .catch(() => {
        if (!cancelled) setPageUrl(null)
      })
    return () => {
      cancelled = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [taskId, primaryFileId, page, companyId])

  useEffect(() => {
    if (!selectedRow || !primaryFileId) {
      setCropUrl(null)
      return
    }
    const req = buildReceiptCropRequest(selectedRow, files)
    if (!req) {
      setCropUrl(null)
      return
    }
    let cancelled = false
    let url: string | null = null
    void taskApi
      .downloadReceiptCrop(taskId, req.taskFileId, {
        page: req.page,
        regionNorm: req.regionNorm,
        regionBbox: req.regionBbox,
        companyId,
      })
      .then(blob => {
        if (cancelled) return
        url = URL.createObjectURL(blob)
        setCropUrl(url)
      })
      .catch(() => {
        if (!cancelled) setCropUrl(null)
      })
    return () => {
      cancelled = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [selectedRow, primaryFileId, files, taskId, companyId])

  if (!files.length || boxes.length === 0) return null

  const selectBox = (box: CropPreviewBox) => {
    const matched = rows.filter(r => receiptIndexFromRow(r) === box.index)
    if (matched.length) {
      onSelectRowIds(new Set(matched.map(r => r._id)))
    }
  }

  return (
    <div className="mvdu-review-pane" data-testid="mvdu-review-pane">
      <div className="mvdu-review-pane__tabs">
        <button
          type="button"
          className={`erp-btn sm${mode === 'full' ? ' on' : ''}`}
          onClick={() => setMode('full')}
        >
          Full page
        </button>
        <button
          type="button"
          className={`erp-btn sm${mode === 'receipt' ? ' on' : ''}`}
          onClick={() => setMode('receipt')}
        >
          This receipt
        </button>
      </div>
      {mode === 'full' ? (
        <div className="mvdu-review-pane__stage" style={{ gridColumn: '1 / -1' }}>
          {pageUrl ? <img src={pageUrl} alt="Full page with Detect boxes" /> : null}
          {boxes.map(box => {
            const active = activeIndex === box.index
            const { x, y, w, h } = box.region_norm
            return (
              <div
                key={box.id}
                className={`mvdu-review-pane__box${active ? ' is-active' : ''}`}
                style={{
                  left: `${x * 100}%`,
                  top: `${y * 100}%`,
                  width: `${w * 100}%`,
                  height: `${h * 100}%`,
                }}
                title={`Receipt ${box.index}`}
                onClick={() => selectBox(box)}
              >
                <span className="mvdu-review-pane__badge">{box.index}</span>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="mvdu-review-pane__crop" style={{ gridColumn: '1 / -1' }}>
          <div style={{ fontSize: 12, color: '#64748b' }}>
            {selectedRow
              ? `Selected receipt ${activeIndex ?? '—'} · linked to ${selectedRowIds.size} row(s)`
              : 'Select a table row to show its crop'}
          </div>
          {cropUrl ? (
            <img src={cropUrl} alt="Selected receipt crop" />
          ) : (
            <div style={{ fontSize: 12, color: '#94a3b8' }}>No crop for this selection.</div>
          )}
        </div>
      )}
    </div>
  )
}
