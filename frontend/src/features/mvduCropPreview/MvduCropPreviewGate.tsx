import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { taskApi } from '../../services/api'
import {
  addBox,
  allBoxesFromPages,
  boxesForApi,
  boxesGeometrySignature,
  confirmPageInSet,
  countPendingPages,
  deleteBox,
  mergeWithNeighbor,
  nextPendingPage,
  normalizeIncomingBoxes,
  normalizePageEntries,
  pageThumbDisplayStatus,
  rotateBox90,
  updateBoxNorm,
} from './boxOps'
import { clientPointToRegionNorm, regionNormToStageStyle } from './overlayCoords'
import type { CropPreviewBox, CropPreviewPageEntry, CropPreviewPayload } from './types'
import { useContainFitStage } from './useContainFitStage'
import './mvduCropPreview.css'

type Props = {
  open: boolean
  taskId: string
  taskFileId: string
  companyId?: string | null
  filename?: string | null
  preview: CropPreviewPayload
  busy?: boolean
  /** Optional parent-surfaced action error (confirm/skip/cancel). */
  actionError?: string | null
  onConfirm: (boxes: ReturnType<typeof boxesForApi>) => void | Promise<void>
  onSkip: () => void | Promise<void>
  onCancel: () => void | Promise<void>
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

function pagesFromPreview(preview: CropPreviewPayload): CropPreviewPageEntry[] {
  return normalizePageEntries(preview.pages, {
    page: preview.page || 1,
    boxes: normalizeIncomingBoxes(preview.boxes, preview.page || 1),
    image_width: preview.image_width || 1,
    image_height: preview.image_height || 1,
    needs_split_review: preview.needs_split_review,
    message: preview.message,
    layout_failure_reason: preview.layout_failure_reason,
  })
}

export function MvduCropPreviewGate({
  open,
  taskId,
  taskFileId,
  companyId,
  filename,
  preview,
  busy = false,
  actionError = null,
  onConfirm,
  onSkip,
  onCancel,
}: Props) {
  const [pageEntries, setPageEntries] = useState<CropPreviewPageEntry[]>(() =>
    pagesFromPreview(preview),
  )
  const [activePage, setActivePage] = useState<number>(
    () => pagesFromPreview(preview)[0]?.page ?? preview.page ?? 1,
  )
  const activeEntry = useMemo(
    () => pageEntries.find(p => p.page === activePage) ?? pageEntries[0] ?? null,
    [pageEntries, activePage],
  )
  const [boxes, setBoxes] = useState<CropPreviewBox[]>(() =>
    normalizeIncomingBoxes(activeEntry?.boxes ?? preview.boxes, activePage),
  )
  const [selectedId, setSelectedId] = useState<string | null>(
    preview.active_box_id ?? boxes[0]?.id ?? null,
  )
  const [pageUrl, setPageUrl] = useState<string | null>(null)
  const [pageError, setPageError] = useState<string | null>(null)
  const [thumbUrls, setThumbUrls] = useState<Record<number, string>>({})
  const [boxThumbUrls, setBoxThumbUrls] = useState<Record<string, string>>({})
  const [localActionError, setLocalActionError] = useState<string | null>(null)
  const [actionPending, setActionPending] = useState(false)
  const dragRef = useRef<DragState | null>(null)
  const stageRef = useRef<HTMLDivElement | null>(null)
  const gateBootstrapKeyRef = useRef<string>('')
  const extracting = preview.phase === 'extracting'
  const editable = preview.phase === 'preview' && !busy && !actionPending
  const shownError = localActionError || actionError
  const multipage = pageEntries.length > 1
  const pendingCount = countPendingPages(pageEntries)
  const otherPending = pageEntries.filter(
    p => p.page !== activePage && p.status !== 'confirmed',
  ).length
  // Primary becomes extract only when this is the last unconfirmed page (or single-page).
  const isLastUnconfirmed =
    !multipage || (activeEntry?.status !== 'confirmed' && otherPending === 0)
  const confirmLabel = isLastUnconfirmed
    ? 'Confirm crops & extract →'
    : 'Confirm page →'
  const blocked = busy || actionPending || extracting

  const { stageSize, fit, onImageLoad } = useContainFitStage({
    stageRef,
    fallbackImage: {
      width: activeEntry?.image_width || preview.image_width || 0,
      height: activeEntry?.image_height || preview.image_height || 0,
    },
    layoutKey: `${open}:${activePage}`,
    imageKey: pageUrl,
  })

  const gateBootstrapKey = useMemo(() => {
    const pages = preview.pages ?? []
    const pageIds = pages.map(p => p.page).join(',') || String(preview.page || 1)
    return `${taskFileId}:${preview.phase}:${pageIds}`
  }, [taskFileId, preview.phase, preview.pages, preview.page])

  // Bootstrap from server on new gate / extract phase; keep local CONFIRMED across polls.
  useEffect(() => {
    const nextPages = pagesFromPreview(preview)
    if (preview.phase === 'extracting' || preview.phase === 'done') {
      setPageEntries(nextPages)
      const start = preview.page || nextPages[0]?.page || 1
      setActivePage(start)
      const entry = nextPages.find(p => p.page === start)
      const nextBoxes = normalizeIncomingBoxes(entry?.boxes ?? preview.boxes, start)
      setBoxes(nextBoxes)
      setSelectedId(preview.active_box_id ?? nextBoxes[0]?.id ?? null)
      gateBootstrapKeyRef.current = gateBootstrapKey
      return
    }
    if (gateBootstrapKeyRef.current === gateBootstrapKey) {
      return
    }
    gateBootstrapKeyRef.current = gateBootstrapKey
    setPageEntries(nextPages)
    const start =
      nextPages.find(p => p.page === preview.page)?.page ??
      nextPages.find(p => p.status !== 'confirmed')?.page ??
      nextPages[0]?.page ??
      1
    setActivePage(start)
    const entry = nextPages.find(p => p.page === start)
    const nextBoxes = normalizeIncomingBoxes(entry?.boxes ?? preview.boxes, start)
    setBoxes(nextBoxes)
    setSelectedId(preview.active_box_id ?? nextBoxes[0]?.id ?? null)
  }, [preview, gateBootstrapKey])

  const persistActiveBoxes = useCallback(
    (nextBoxes: CropPreviewBox[]) => {
      setBoxes(nextBoxes)
      setPageEntries(prev =>
        prev.map(p =>
          p.page === activePage
            ? { ...p, boxes: nextBoxes.map(b => ({ ...b, page: activePage })) }
            : p,
        ),
      )
    },
    [activePage],
  )

  const switchToPage = useCallback(
    (page: number) => {
      if (page === activePage) return
      // Save current page boxes before leaving.
      setPageEntries(prev => {
        const saved = prev.map(p =>
          p.page === activePage
            ? { ...p, boxes: boxes.map(b => ({ ...b, page: activePage })) }
            : p,
        )
        const target = saved.find(p => p.page === page)
        const nextBoxes = normalizeIncomingBoxes(target?.boxes ?? [], page)
        setBoxes(nextBoxes)
        setSelectedId(nextBoxes[0]?.id ?? null)
        setActivePage(page)
        return saved
      })
    },
    [activePage, boxes],
  )

  useEffect(() => {
    if (!open) return
    let cancelled = false
    let objectUrl: string | null = null
    setPageError(null)
    void taskApi
      .downloadReceiptCrop(taskId, taskFileId, {
        page: activePage || 1,
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
  }, [open, taskId, taskFileId, companyId, activePage])

  // Page thumbs (multipage rail) — revoke previous set only after the next set lands.
  useEffect(() => {
    if (!open || !multipage) return
    let cancelled = false
    const created: string[] = []
    const snapshot = pageEntries
    const load = async () => {
      const next: Record<number, string> = {}
      for (const entry of snapshot) {
        try {
          const blob = await taskApi.downloadReceiptCrop(taskId, taskFileId, {
            page: entry.page,
            companyId,
          })
          if (cancelled) break
          const url = URL.createObjectURL(blob)
          created.push(url)
          next[entry.page] = url
        } catch {
          /* thumb optional */
        }
      }
      if (cancelled) {
        created.forEach(u => URL.revokeObjectURL(u))
        return
      }
      setThumbUrls(prev => {
        Object.values(prev).forEach(u => URL.revokeObjectURL(u))
        return next
      })
    }
    void load()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload when page set identity changes
  }, [open, multipage, taskId, taskFileId, companyId, pageEntries.map(p => p.page).join(',')])

  // Box strip thumbs (single-page / edit aid). Debounce geometry to avoid revoke storms while dragging.
  const [boxThumbKey, setBoxThumbKey] = useState(
    () => boxesGeometrySignature(boxes) + `|p=${activePage}`,
  )
  useEffect(() => {
    if (multipage) return
    const next = boxesGeometrySignature(boxes) + `|p=${activePage}`
    const delay = dragRef.current ? 350 : 80
    const t = window.setTimeout(() => setBoxThumbKey(next), delay)
    return () => window.clearTimeout(t)
  }, [boxes, activePage, multipage])

  useEffect(() => {
    if (!open || multipage) return
    let cancelled = false
    const created: string[] = []
    const snapshot = boxes
    const load = async () => {
      const next: Record<string, string> = {}
      for (const box of snapshot) {
        try {
          const blob = await taskApi.downloadReceiptCrop(taskId, taskFileId, {
            page: box.page || activePage || 1,
            regionNorm: box.region_norm,
            companyId,
          })
          if (cancelled) break
          const url = URL.createObjectURL(blob)
          created.push(url)
          next[box.id] = url
        } catch {
          /* thumb optional — do not abort confirm */
        }
      }
      if (cancelled) {
        created.forEach(u => URL.revokeObjectURL(u))
        return
      }
      setBoxThumbUrls(prev => {
        Object.values(prev).forEach(u => URL.revokeObjectURL(u))
        return next
      })
    }
    void load()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- deliberate: keyed by debounced geometry
  }, [open, multipage, boxThumbKey, taskId, taskFileId, companyId, activePage])

  useEffect(() => {
    return () => {
      setThumbUrls(prev => {
        Object.values(prev).forEach(u => URL.revokeObjectURL(u))
        return {}
      })
      setBoxThumbUrls(prev => {
        Object.values(prev).forEach(u => URL.revokeObjectURL(u))
        return {}
      })
    }
  }, [])

  const selected = useMemo(
    () => boxes.find(b => b.id === selectedId) ?? null,
    [boxes, selectedId],
  )

  const clientToNorm = useCallback(
    (clientX: number, clientY: number) => {
      const el = stageRef.current
      if (!el) return { x: 0, y: 0 }
      const rect = el.getBoundingClientRect()
      return clientPointToRegionNorm(clientX, clientY, rect, fit)
    },
    [fit],
  )

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
      if (dragRef.current) {
        // Flush edited boxes into the page map when the drag ends.
        setBoxes(current => {
          setPageEntries(prev =>
            prev.map(p =>
              p.page === activePage
                ? { ...p, boxes: current.map(b => ({ ...b, page: activePage })) }
                : p,
            ),
          )
          return current
        })
      }
      dragRef.current = null
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [editable, clientToNorm, activePage])

  // Keyboard: Left/Right or [ / ] among gate pages.
  useEffect(() => {
    if (!open || !multipage || extracting) return
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return
      const ordered = [...pageEntries].sort((a, b) => a.page - b.page)
      const idx = ordered.findIndex(p => p.page === activePage)
      if (idx < 0) return
      if (e.key === 'ArrowRight' || e.key === ']') {
        const next = ordered[Math.min(ordered.length - 1, idx + 1)]
        if (next && next.page !== activePage) {
          e.preventDefault()
          switchToPage(next.page)
        }
      } else if (e.key === 'ArrowLeft' || e.key === '[') {
        const prev = ordered[Math.max(0, idx - 1)]
        if (prev && prev.page !== activePage) {
          e.preventDefault()
          switchToPage(prev.page)
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, multipage, extracting, pageEntries, activePage, switchToPage])

  const runAction = async (fn: () => void | Promise<void>, fallback: string) => {
    setLocalActionError(null)
    setActionPending(true)
    try {
      await Promise.resolve(fn())
    } catch (err) {
      setLocalActionError(err instanceof Error ? err.message : fallback)
    } finally {
      setActionPending(false)
    }
  }

  if (!open) return null

  const emptyHint = boxes.length === 0
  const pageRasterMissing = Boolean(pageError)

  const handleConfirmClick = () => {
    if (blocked || emptyHint || pageRasterMissing) return
    // Persist current page edits, mark CONFIRMED.
    const withCurrent = pageEntries.map(p =>
      p.page === activePage
        ? { ...p, boxes: boxes.map(b => ({ ...b, page: activePage })) }
        : p,
    )
    const confirmed = confirmPageInSet(withCurrent, activePage, boxes)
    setPageEntries(confirmed)

    const stillPending = nextPendingPage(confirmed, activePage)
    if (stillPending != null && !isLastUnconfirmed) {
      // Auto-advance to next PENDING — do not extract yet.
      const target = confirmed.find(p => p.page === stillPending)
      setActivePage(stillPending)
      const nextBoxes = normalizeIncomingBoxes(target?.boxes ?? [], stillPending)
      setBoxes(nextBoxes)
      setSelectedId(nextBoxes[0]?.id ?? null)
      return
    }
    // Last page (or single-page): extract all CONFIRMED pages' boxes.
    void runAction(
      () => onConfirm(boxesForApi(allBoxesFromPages(confirmed))),
      'Could not confirm crops.',
    )
  }

  const handleSkipClick = () => {
    if (blocked) return
    const pending = countPendingPages(
      pageEntries.map(p =>
        p.page === activePage
          ? { ...p, boxes: boxes.map(b => ({ ...b, page: activePage })) }
          : p,
      ),
    )
    if (pending > 1) {
      const ok = window.confirm(`Skip crop review for ${pending} pages?`)
      if (!ok) return
    }
    void runAction(() => onSkip(), 'Could not skip crop preview.')
  }

  return (
    <div className="mvdu-crop-gate" role="region" aria-label="Crop preview">
      <div className="mvdu-crop-gate__panel">
        <header className="mvdu-crop-gate__head">
          <div>
            <div className="mvdu-crop-gate__title">
              {filename ?? 'Upload'}
              {extracting
                ? ' · extracting…'
                : busy || actionPending
                  ? ' · starting extract…'
                  : ' · awaiting review'}
            </div>
            <div className="mvdu-crop-gate__sub">
              Crop preview · page {activePage}
              {multipage ? ` of ${pageEntries.length}` : ''}
            </div>
          </div>
          <div className="mvdu-crop-gate__actions">
            {editable && (
              <button
                type="button"
                className="erp-btn"
                disabled={blocked}
                onClick={() => {
                  const next = addBox(boxes, activePage || 1)
                  persistActiveBoxes(next)
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
                  disabled={blocked}
                  title="Rotate crop 90° clockwise"
                  onClick={() => persistActiveBoxes(rotateBox90(boxes, selected.id))}
                >
                  Rotate 90°
                </button>
                <button
                  type="button"
                  className="erp-btn"
                  disabled={blocked || boxes.length < 2}
                  onClick={() => {
                    const next = mergeWithNeighbor(boxes, selected.id)
                    persistActiveBoxes(next)
                    setSelectedId(next[0]?.id ?? null)
                  }}
                >
                  Merge neighbor
                </button>
                <button
                  type="button"
                  className="erp-btn danger"
                  disabled={blocked}
                  onClick={() => {
                    const next = deleteBox(boxes, selected.id)
                    persistActiveBoxes(next)
                    setSelectedId(next[0]?.id ?? null)
                  }}
                >
                  Delete
                </button>
              </>
            )}
          </div>
        </header>

        <div className={`mvdu-crop-gate__body${multipage ? ' mvdu-crop-gate__body--pages' : ''}`}>
          <div className="mvdu-crop-gate__stage-wrap">
            {pageError && (
              <div className="mvdu-crop-gate__err">
                {pageError}
                <div style={{ marginTop: 6, fontSize: 11 }}>
                  Confirm is disabled for this page until the raster loads. Cancel or Retry the file.
                </div>
              </div>
            )}
            {shownError && (
              <div className="mvdu-crop-gate__err" role="alert" data-testid="mvdu-crop-gate-action-error">
                {shownError}
              </div>
            )}
            {(busy || actionPending || extracting) && (
              <div className="mvdu-crop-gate__progress" data-testid="mvdu-crop-gate-progress">
                {extracting
                  ? 'Extracting receipts from confirmed crops…'
                  : 'Confirm received — starting extract…'}
              </div>
            )}
            <div ref={stageRef} className="mvdu-crop-gate__stage">
              {pageUrl ? (
                <img
                  src={pageUrl}
                  alt={`Page ${activePage}`}
                  className="mvdu-crop-gate__page"
                  draggable={false}
                  onLoad={e => onImageLoad(e.currentTarget)}
                />
              ) : (
                <div className="mvdu-crop-gate__page mvdu-crop-gate__page--placeholder" />
              )}
              {emptyHint && (
                <div className="mvdu-crop-gate__empty">
                  {activeEntry?.message || preview.message || 'Draw boxes or confirm forced split'}
                </div>
              )}
              {boxes.map(box => {
                const active =
                  box.id === selectedId ||
                  (extracting && box.id === preview.active_box_id) ||
                  box.extract_status === 'running'
                const boxStyle = regionNormToStageStyle(box.region_norm, fit, stageSize)
                return (
                  <div
                    key={box.id}
                    className={`mvdu-crop-gate__box${active ? ' is-active' : ''}`}
                    style={{
                      ...boxStyle,
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

          {multipage ? (
            <aside className="mvdu-crop-gate__strip mvdu-crop-gate__strip--pages" aria-label="Page control">
              {pageEntries.map(entry => {
                const display = pageThumbDisplayStatus(entry, activePage)
                const clickable = display !== 'RUNNING'
                return (
                  <button
                    key={entry.page}
                    type="button"
                    className={`mvdu-crop-gate__thumb mvdu-crop-gate__thumb--page${
                      display === 'RUNNING' ? ' is-selected' : ''
                    }${display === 'CONFIRMED' ? ' is-confirmed' : ''}`}
                    aria-current={display === 'RUNNING' ? 'page' : undefined}
                    onClick={() => {
                      if (!clickable) return
                      switchToPage(entry.page)
                    }}
                  >
                    <span className="mvdu-crop-gate__thumb-idx">{entry.page}</span>
                    {thumbUrls[entry.page] ? (
                      <img src={thumbUrls[entry.page]} alt={`Page ${entry.page}`} />
                    ) : (
                      <span className="mvdu-crop-gate__thumb-ph" />
                    )}
                    <span
                      className={`mvdu-crop-gate__status mvdu-crop-gate__status--${display.toLowerCase()}`}
                    >
                      {display === 'CONFIRMED' ? '✓ CONFIRMED' : display}
                    </span>
                  </button>
                )
              })}
            </aside>
          ) : (
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
                    {boxThumbUrls[box.id] ? (
                      <img src={boxThumbUrls[box.id]} alt={`Crop ${box.index}`} />
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
          )}
        </div>

        <footer className="mvdu-crop-gate__foot">
          <button
            type="button"
            className="erp-btn"
            disabled={blocked}
            onClick={() => void runAction(() => onCancel(), 'Could not cancel crop preview.')}
          >
            Cancel
          </button>
          <button
            type="button"
            className="erp-btn"
            disabled={blocked || (emptyHint && pendingCount === 0)}
            title={
              pendingCount > 1
                ? `Skip crop review for ${pendingCount} pages`
                : 'Power users: Detect again and extract without editing'
            }
            onClick={handleSkipClick}
          >
            Skip preview
          </button>
          <button
            type="button"
            className="erp-btn primary mvdu-crop-gate__confirm"
            disabled={blocked || emptyHint || pageRasterMissing}
            data-testid="mvdu-crop-gate-confirm"
            onClick={handleConfirmClick}
          >
            {confirmLabel}
          </button>
        </footer>
      </div>
    </div>
  )
}
