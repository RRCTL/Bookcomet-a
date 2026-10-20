import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { taskApi } from '../../services/api'
import {
  addBox,
  boxesForApi,
  deleteBox,
  mergeWithNeighbor,
  normalizeIncomingBoxes,
  rotateBox90,
  updateBoxNorm,
} from './boxOps'
import type { CropPreviewBox, CropPreviewPayload } from './types'
import './mvduCropPreview.css'

type Props = {
  open: boolean
  taskId: string
  taskFileId: string
  companyId?: string | null
  filename?: string | null
  preview: CropPreviewPayload
  busy?: boolean
  onConfirm: (boxes: ReturnType<typeof boxesForApi>) => void
  onSkip: () => void
  onCancel: () => void
}

type DragState =
  | { kind: 'move'; id: string; startX: number; startY: number; orig: CropPreviewBox['region_norm'] }
  | {
      kind: 'resize'
      id: string
      handle: 'se' | 'ne' | 'sw' | 'nw'
      startX: number
      startY: number
      orig: CropPreviewBox['region_norm']
    }

const GREEN = '#22c55e'
const GREEN_ACTIVE = '#4ade80'

export function MvduCropPreviewGate({
  open,
  taskId,
  taskFileId,
  companyId,
  filename,
  preview,
  busy = false,
  onConfirm,
  onSkip,
  onCancel,
}: Props) {
  const [boxes, setBoxes] = useState<CropPreviewBox[]>(() =>
    normalizeIncomingBoxes(preview.boxes),
  )
  const [selectedId, setSelectedId] = useState<string | null>(
    preview.active_box_id ?? preview.boxes[0]?.id ?? null,
  )
  const [pageUrl, setPageUrl] = useState<string | null>(null)
  const [pageError, setPageError] = useState<string | null>(null)
  const [thumbUrls, setThumbUrls] = useState<Record<string, string>>({})
  const dragRef = useRef<DragState | null>(null)
  const stageRef = useRef<HTMLDivElement | null>(null)
  const extracting = preview.phase === 'extracting'
  const editable = preview.phase === 'preview' && !busy

  useEffect(() => {
    setBoxes(normalizeIncomingBoxes(preview.boxes))
    setSelectedId(preview.active_box_id ?? preview.boxes[0]?.id ?? null)
  }, [preview])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    let objectUrl: string | null = null
    setPageError(null)
    void taskApi
      .downloadReceiptCrop(taskId, taskFileId, {
        page: preview.page || 1,
        companyId,
      })
      .then(blob => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setPageUrl(objectUrl)
      })
      .catch(err => {
        if (cancelled) return
        setPageError(err instanceof Error ? err.message : 'Could not load page image')
        setPageUrl(null)
      })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [open, taskId, taskFileId, companyId, preview.page])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    const created: string[] = []
    const load = async () => {
      const next: Record<string, string> = {}
      for (const box of boxes) {
        try {
          const blob = await taskApi.downloadReceiptCrop(taskId, taskFileId, {
            page: box.page || preview.page || 1,
            regionNorm: box.region_norm,
            companyId,
          })
          if (cancelled) return
          const url = URL.createObjectURL(blob)
          created.push(url)
          next[box.id] = url
        } catch {
          /* thumb optional */
        }
      }
      if (!cancelled) setThumbUrls(next)
    }
    void load()
    return () => {
      cancelled = true
      created.forEach(u => URL.revokeObjectURL(u))
    }
  }, [open, boxes, taskId, taskFileId, companyId, preview.page])

  const selected = useMemo(
    () => boxes.find(b => b.id === selectedId) ?? null,
    [boxes, selectedId],
  )

  const clientToNorm = useCallback((clientX: number, clientY: number) => {
    const el = stageRef.current
    if (!el) return { x: 0, y: 0 }
    const rect = el.getBoundingClientRect()
    return {
      x: (clientX - rect.left) / Math.max(1, rect.width),
      y: (clientY - rect.top) / Math.max(1, rect.height),
    }
  }, [])

  useEffect(() => {
    if (!editable) return
    const onMove = (e: PointerEvent) => {
      const drag = dragRef.current
      if (!drag) return
      const cur = clientToNorm(e.clientX, e.clientY)
      const dx = cur.x - drag.startX
      const dy = cur.y - drag.startY
      const o = drag.orig
      if (drag.kind === 'move') {
        setBoxes(prev =>
          updateBoxNorm(prev, drag.id, {
            x: o.x + dx,
            y: o.y + dy,
            w: o.w,
            h: o.h,
          }),
        )
        return
      }
      let x = o.x
      let y = o.y
      let w = o.w
      let h = o.h
      if (drag.handle.includes('e')) w = o.w + dx
      if (drag.handle.includes('s')) h = o.h + dy
      if (drag.handle.includes('w')) {
        x = o.x + dx
        w = o.w - dx
      }
      if (drag.handle.includes('n')) {
        y = o.y + dy
        h = o.h - dy
      }
      setBoxes(prev => updateBoxNorm(prev, drag.id, { x, y, w, h }))
    }
    const onUp = () => {
      dragRef.current = null
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [editable, clientToNorm])

  if (!open) return null

  const emptyHint = boxes.length === 0

  return (
    <div className="mvdu-crop-gate" role="dialog" aria-modal="true" aria-label="Crop preview">
      <div className="mvdu-crop-gate__panel">
        <header className="mvdu-crop-gate__head">
          <div>
            <div className="mvdu-crop-gate__title">Crop preview</div>
            <div className="mvdu-crop-gate__sub">
              {filename ?? 'Upload'} · page {preview.page}
              {extracting ? ' · extracting…' : ''}
            </div>
          </div>
          <div className="mvdu-crop-gate__actions">
            {editable && (
              <button
                type="button"
                className="erp-btn"
                disabled={busy}
                onClick={() => {
                  const next = addBox(boxes, preview.page || 1)
                  setBoxes(next)
                  setSelectedId(next[next.length - 1]?.id ?? null)
                }}
              >
                + Add box
              </button>
            )}
            {editable && selected && (
              <>
                <button
                  type="button"
                  className="erp-btn"
                  disabled={busy}
                  title="Rotate crop 90° clockwise"
                  onClick={() => setBoxes(rotateBox90(boxes, selected.id))}
                >
                  Rotate 90°
                </button>
                <button
                  type="button"
                  className="erp-btn"
                  disabled={busy || boxes.length < 2}
                  onClick={() => {
                    const next = mergeWithNeighbor(boxes, selected.id)
                    setBoxes(next)
                    setSelectedId(next[0]?.id ?? null)
                  }}
                >
                  Merge neighbor
                </button>
                <button
                  type="button"
                  className="erp-btn danger"
                  disabled={busy}
                  onClick={() => {
                    const next = deleteBox(boxes, selected.id)
                    setBoxes(next)
                    setSelectedId(next[0]?.id ?? null)
                  }}
                >
                  Delete
                </button>
              </>
            )}
          </div>
        </header>

        <div className="mvdu-crop-gate__body">
          <div className="mvdu-crop-gate__stage-wrap">
            {pageError && <div className="mvdu-crop-gate__err">{pageError}</div>}
            <div
              ref={stageRef}
              className="mvdu-crop-gate__stage"
              style={{
                aspectRatio:
                  preview.image_width > 0 && preview.image_height > 0
                    ? `${preview.image_width} / ${preview.image_height}`
                    : '4 / 3',
              }}
            >
              {pageUrl ? (
                <img src={pageUrl} alt="Full page" className="mvdu-crop-gate__page" draggable={false} />
              ) : (
                <div className="mvdu-crop-gate__page mvdu-crop-gate__page--placeholder" />
              )}
              {emptyHint && (
                <div className="mvdu-crop-gate__empty">
                  {preview.message || 'Draw boxes or confirm forced split'}
                </div>
              )}
              {boxes.map(box => {
                const active =
                  box.id === selectedId ||
                  (extracting && box.id === preview.active_box_id) ||
                  box.extract_status === 'running'
                const { x, y, w, h } = box.region_norm
                return (
                  <div
                    key={box.id}
                    className={`mvdu-crop-gate__box${active ? ' is-active' : ''}`}
                    style={{
                      left: `${x * 100}%`,
                      top: `${y * 100}%`,
                      width: `${w * 100}%`,
                      height: `${h * 100}%`,
                      borderColor: active ? GREEN_ACTIVE : GREEN,
                    }}
                    onPointerDown={e => {
                      if (!editable) {
                        setSelectedId(box.id)
                        return
                      }
                      e.preventDefault()
                      e.stopPropagation()
                      setSelectedId(box.id)
                      const p = clientToNorm(e.clientX, e.clientY)
                      dragRef.current = {
                        kind: 'move',
                        id: box.id,
                        startX: p.x,
                        startY: p.y,
                        orig: { ...box.region_norm },
                      }
                    }}
                  >
                    <span className="mvdu-crop-gate__badge">{box.index}</span>
                    {box.rotation_deg ? (
                      <span className="mvdu-crop-gate__rot">{box.rotation_deg}°</span>
                    ) : null}
                    {editable &&
                      (['nw', 'ne', 'sw', 'se'] as const).map(handle => (
                        <i
                          key={handle}
                          className={`mvdu-crop-gate__handle mvdu-crop-gate__handle--${handle}`}
                          onPointerDown={e => {
                            e.preventDefault()
                            e.stopPropagation()
                            setSelectedId(box.id)
                            const p = clientToNorm(e.clientX, e.clientY)
                            dragRef.current = {
                              kind: 'resize',
                              id: box.id,
                              handle,
                              startX: p.x,
                              startY: p.y,
                              orig: { ...box.region_norm },
                            }
                          }}
                        />
                      ))}
                  </div>
                )
              })}
            </div>
          </div>

          <aside className="mvdu-crop-gate__strip" aria-label="Crop thumbnails">
            {boxes.length === 0 ? (
              <p className="mvdu-crop-gate__strip-empty">No boxes yet. Add a box for each slip.</p>
            ) : (
              boxes.map(box => (
                <button
                  key={box.id}
                  type="button"
                  className={`mvdu-crop-gate__thumb${box.id === selectedId ? ' is-selected' : ''}`}
                  onClick={() => setSelectedId(box.id)}
                >
                  <span className="mvdu-crop-gate__thumb-idx">{box.index}</span>
                  {thumbUrls[box.id] ? (
                    <img src={thumbUrls[box.id]} alt={`Crop ${box.index}`} />
                  ) : (
                    <span className="mvdu-crop-gate__thumb-ph" />
                  )}
                  <span className={`mvdu-crop-gate__status mvdu-crop-gate__status--${box.extract_status}`}>
                    {box.extract_status}
                  </span>
                </button>
              ))
            )}
          </aside>
        </div>

        <footer className="mvdu-crop-gate__foot">
          <button type="button" className="erp-btn" disabled={busy || extracting} onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="erp-btn"
            disabled={busy || extracting || emptyHint}
            title="Power users: Detect again and extract without editing"
            onClick={onSkip}
          >
            Skip preview
          </button>
          <button
            type="button"
            className="erp-btn primary"
            disabled={busy || extracting || emptyHint}
            onClick={() => onConfirm(boxesForApi(boxes))}
          >
            Confirm crops & extract
          </button>
        </footer>
      </div>
    </div>
  )
}
