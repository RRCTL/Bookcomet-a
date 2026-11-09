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
  mergeServerBoxStatus,
  mergeWithNeighbor,
  nextPendingPage,
  normalizeIncomingBoxes,
  normalizePageEntries,
  pageThumbDisplayStatus,
  rotateBox90,
  updateBoxNorm,
} from './boxOps'
import { clientPointToRegionNorm, regionNormToStageStyle } from './overlayCoords'
import type {
  CropPreviewBox,
  CropPreviewFileGateMemory,
  CropPreviewGateNavFile,
  CropPreviewPageEntry,
  CropPreviewPayload,
} from './types'
import { useContainFitStage } from './useContainFitStage'
import './mvduCropPreview.css'

type Props = {
  open: boolean
  taskId: string
  taskFileId: string
  companyId?: string | null
  filename?: string | null
  preview: CropPreviewPayload
  /**
   * All files in this run awaiting crop preview (including the active one).
   * Length ≤ 1 → file control hidden. Parent keeps Confirm/Skip/Cancel scoped
   * to the active taskFileId.
   */
  gatedFiles?: CropPreviewGateNavFile[]
  busy?: boolean
  /**
   * File ids whose Confirm/Skip is queued behind another file's extract.
   * Active queued file locks footer actions but never blocks WAITING nav.
   */
  queuedFileIds?: string[]
  /** Optional parent-surfaced action error (confirm/skip/cancel). */
  actionError?: string | null
  onConfirm: (boxes: ReturnType<typeof boxesForApi>) => void | Promise<void>
  onSkip: () => void | Promise<void>
  onCancel: () => void | Promise<void>
  /** Fired when the user picks another gated file (parent updates taskFileId/preview). */
  onActiveFileChange?: (taskFileId: string) => void
}

function shortFilename(name: string | null | undefined, max = 18): string {
  const raw = (name ?? 'Upload').trim() || 'Upload'
  if (raw.length <= max) return raw
  const dot = raw.lastIndexOf('.')
  if (dot > 0 && raw.length - dot <= 5) {
    const ext = raw.slice(dot)
    const stem = raw.slice(0, Math.max(1, max - ext.length - 1))
    return `${stem}…${ext}`
  }
  return `${raw.slice(0, max - 1)}…`
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
  gatedFiles,
  busy = false,
  queuedFileIds,
  actionError = null,
  onConfirm,
  onSkip,
  onCancel,
  onActiveFileChange,
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
  const activeTaskFileIdRef = useRef(taskFileId)
  const fileMemoryRef = useRef<Map<string, CropPreviewFileGateMemory>>(new Map())
  const pageEntriesRef = useRef(pageEntries)
  const boxesRef = useRef(boxes)
  const activePageRef = useRef(activePage)
  const selectedIdRef = useRef(selectedId)
  pageEntriesRef.current = pageEntries
  boxesRef.current = boxes
  activePageRef.current = activePage
  selectedIdRef.current = selectedId

  const extracting = preview.phase === 'extracting'
  const queuedIds = useMemo(
    () => new Set(queuedFileIds ?? []),
    [queuedFileIds],
  )
  const queued = queuedIds.has(taskFileId)
  // Footer / box edits lock for THIS file only. Never gate WAITING file nav on sibling extract.
  const actionLocked = busy || actionPending || extracting || queued
  const editable = preview.phase === 'preview' && !busy && !actionPending && !queued
  const shownError = localActionError || actionError
  const multipage = pageEntries.length > 1
  const fileNavList = useMemo(() => {
    if (gatedFiles && gatedFiles.length > 0) return gatedFiles
    return [
      {
        taskFileId,
        filename: filename ?? null,
        preview,
      } satisfies CropPreviewGateNavFile,
    ]
  }, [gatedFiles, taskFileId, filename, preview])
  const showFileControl = fileNavList.length > 1
  const activeFileIndex = Math.max(
    0,
    fileNavList.findIndex(f => f.taskFileId === taskFileId),
  )
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

  const { stageSize, fit, onImageLoad } = useContainFitStage({
    stageRef,
    fallbackImage: {
      width: activeEntry?.image_width || preview.image_width || 0,
      height: activeEntry?.image_height || preview.image_height || 0,
    },
    layoutKey: `${open}:${taskFileId}:${activePage}`,
    imageKey: pageUrl,
  })

  const gateBootstrapKey = useMemo(() => {
    const pages = preview.pages ?? []
    const pageIds = pages.map(p => p.page).join(',') || String(preview.page || 1)
    return `${taskFileId}:${preview.phase}:${pageIds}`
  }, [taskFileId, preview.phase, preview.pages, preview.page])

  // Drop memory for files that left the gate (extracted / cancelled).
  useEffect(() => {
    const live = new Set(fileNavList.map(f => f.taskFileId))
    for (const id of [...fileMemoryRef.current.keys()]) {
      if (!live.has(id)) fileMemoryRef.current.delete(id)
    }
  }, [fileNavList])

  const applyMemory = useCallback((mem: CropPreviewFileGateMemory) => {
    setPageEntries(mem.pageEntries)
    setActivePage(mem.activePage)
    setBoxes(mem.boxes)
    setSelectedId(mem.selectedId)
  }, [])

  const bootstrapFromPreview = useCallback(
    (nextPreview: CropPreviewPayload, preferFirstPage: boolean) => {
      const nextPages = pagesFromPreview(nextPreview)
      const start = preferFirstPage
        ? (nextPages[0]?.page ?? nextPreview.page ?? 1)
        : (nextPages.find(p => p.page === nextPreview.page)?.page ??
          nextPages.find(p => p.status !== 'confirmed')?.page ??
          nextPages[0]?.page ??
          1)
      setPageEntries(nextPages)
      setActivePage(start)
      const entry = nextPages.find(p => p.page === start)
      const nextBoxes = normalizeIncomingBoxes(entry?.boxes ?? nextPreview.boxes, start)
      setBoxes(nextBoxes)
      setSelectedId(nextPreview.active_box_id ?? nextBoxes[0]?.id ?? null)
    },
    [],
  )

  // Bootstrap from server on new gate / file switch / extract phase; restore in-memory edits on return.
  useEffect(() => {
    const fileChanged = activeTaskFileIdRef.current !== taskFileId
    if (fileChanged) {
      activeTaskFileIdRef.current = taskFileId
      const mem = fileMemoryRef.current.get(taskFileId)
      if (mem && preview.phase === 'preview') {
        applyMemory(mem)
        gateBootstrapKeyRef.current = gateBootstrapKey
        return
      }
      // Fresh visit: page 1 (first gated page) RUNNING, remaining PENDING from Detect.
      bootstrapFromPreview(preview, true)
      gateBootstrapKeyRef.current = gateBootstrapKey
      return
    }

    const nextPages = pagesFromPreview(preview)
    if (preview.phase === 'extracting' || preview.phase === 'done') {
      // Extracting: drop local memory so we don't resurrect stale CONFIRMED after send.
      fileMemoryRef.current.delete(taskFileId)
      // Preserve the page the user selected in the thumb rail across extract polls.
      // Previously every poll forced page 1, leaving page-1 overlays after clicking thumb 2.
      const keyChanged = gateBootstrapKeyRef.current !== gateBootstrapKey
      gateBootstrapKeyRef.current = gateBootstrapKey
      setPageEntries(nextPages)
      const prevActive = activePageRef.current
      const start =
        !keyChanged && nextPages.some(p => p.page === prevActive)
          ? prevActive
          : Number(preview.page) || nextPages[0]?.page || 1
      if (start !== prevActive) setActivePage(start)
      const entry = nextPages.find(p => p.page === start)
      const serverBoxes = normalizeIncomingBoxes(entry?.boxes ?? preview.boxes, start)
      setBoxes(prev => {
        const samePage =
          prev.length > 0 && prev.every(b => (b.page || start) === start)
        if (!keyChanged && samePage) return mergeServerBoxStatus(prev, serverBoxes)
        return serverBoxes
      })
      setSelectedId(prev => {
        if (!keyChanged && prev && serverBoxes.some(b => b.id === prev)) return prev
        return preview.active_box_id ?? serverBoxes[0]?.id ?? null
      })
      return
    }
    if (gateBootstrapKeyRef.current === gateBootstrapKey) {
      return
    }
    gateBootstrapKeyRef.current = gateBootstrapKey
    bootstrapFromPreview(preview, false)
  }, [preview, gateBootstrapKey, taskFileId, applyMemory, bootstrapFromPreview])

  const stashActiveFile = useCallback(() => {
    const id = activeTaskFileIdRef.current
    const savedPages = pageEntriesRef.current.map(p =>
      p.page === activePageRef.current
        ? {
            ...p,
            boxes: boxesRef.current.map(b => ({ ...b, page: activePageRef.current })),
          }
        : p,
    )
    fileMemoryRef.current.set(id, {
      pageEntries: savedPages,
      activePage: activePageRef.current,
      boxes: boxesRef.current.map(b => ({ ...b, page: activePageRef.current })),
      selectedId: selectedIdRef.current,
    })
  }, [])

  const switchToFile = useCallback(
    (nextFileId: string) => {
      // Always allow WAITING / sibling file nav while another file extracts.
      if (!nextFileId || nextFileId === taskFileId) return
      if (!fileNavList.some(f => f.taskFileId === nextFileId)) return
      stashActiveFile()
      setLocalActionError(null)
      onActiveFileChange?.(nextFileId)
    },
    [taskFileId, fileNavList, stashActiveFile, onActiveFileChange],
  )

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
    if (actionLocked || emptyHint || pageRasterMissing) return
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
    if (actionLocked) return
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
                : queued
                  ? ' · queued…'
                  : busy || actionPending
                    ? ' · starting extract…'
                    : ' · awaiting review'}
            </div>
            <div className="mvdu-crop-gate__sub">
              Crop preview
              {showFileControl
                ? ` · file ${activeFileIndex + 1} of ${fileNavList.length}`
                : ''}
              {' · '}
              page {activePage}
              {multipage ? ` of ${pageEntries.length}` : ''}
            </div>
          </div>
          <div className="mvdu-crop-gate__actions">
            {editable && (
              <button
                type="button"
                className="erp-btn"
                disabled={actionLocked}
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
                  disabled={actionLocked}
                  title="Rotate crop 90° clockwise"
                  onClick={() => persistActiveBoxes(rotateBox90(boxes, selected.id))}
                >
                  Rotate 90°
                </button>
                <button
                  type="button"
                  className="erp-btn"
                  disabled={actionLocked || boxes.length < 2}
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
                  disabled={actionLocked}
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

        <div
          className={`mvdu-crop-gate__body${multipage ? ' mvdu-crop-gate__body--pages' : ''}${
            showFileControl ? ' mvdu-crop-gate__body--files' : ''
          }`}
        >
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
            {(busy || actionPending || extracting || queued) && (
              <div className="mvdu-crop-gate__progress" data-testid="mvdu-crop-gate-progress">
                {extracting
                  ? 'Extracting receipts from confirmed crops…'
                  : queued
                    ? 'Queued — waiting for the previous file to finish extract…'
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
                    key={`${taskFileId}:${box.id}`}
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

          <aside
            className={`mvdu-crop-gate__strip${multipage ? ' mvdu-crop-gate__strip--pages' : ''}`}
            aria-label={multipage ? 'Page control' : 'Crop thumbnails'}
          >
            {showFileControl && (
              <div className="mvdu-crop-gate__file-rail" aria-label="File control">
                <div className="mvdu-crop-gate__file-nav">
                  <button
                    type="button"
                    className="mvdu-crop-gate__file-step"
                    disabled={activeFileIndex <= 0}
                    aria-label="Previous file"
                    data-testid="mvdu-crop-gate-file-prev"
                    onClick={() => {
                      const prev = fileNavList[activeFileIndex - 1]
                      if (prev) switchToFile(prev.taskFileId)
                    }}
                  >
                    ‹
                  </button>
                  <span className="mvdu-crop-gate__file-pos" data-testid="mvdu-crop-gate-file-pos">
                    {activeFileIndex + 1}/{fileNavList.length}
                  </span>
                  <button
                    type="button"
                    className="mvdu-crop-gate__file-step"
                    disabled={activeFileIndex >= fileNavList.length - 1}
                    aria-label="Next file"
                    data-testid="mvdu-crop-gate-file-next"
                    onClick={() => {
                      const next = fileNavList[activeFileIndex + 1]
                      if (next) switchToFile(next.taskFileId)
                    }}
                  >
                    ›
                  </button>
                </div>
                {fileNavList.map((file, idx) => {
                  const selected = file.taskFileId === taskFileId
                  const phase = file.preview.phase
                  const isQueued = queuedIds.has(file.taskFileId)
                  const statusKind = selected
                    ? 'running'
                    : phase === 'extracting'
                      ? 'running'
                      : isQueued
                        ? 'queued'
                        : 'pending'
                  const statusLabel = selected
                    ? 'ACTIVE'
                    : phase === 'extracting'
                      ? 'EXTRACT'
                      : isQueued
                        ? 'QUEUED'
                        : 'WAITING'
                  return (
                    <button
                      key={file.taskFileId}
                      type="button"
                      className={`mvdu-crop-gate__file-thumb${selected ? ' is-selected' : ''}${
                        phase === 'extracting' ? ' is-extracting' : ''
                      }${isQueued && !selected ? ' is-queued' : ''}`}
                      aria-current={selected ? 'true' : undefined}
                      title={file.filename ?? `File ${idx + 1}`}
                      data-testid={`mvdu-crop-gate-file-thumb-${file.taskFileId}`}
                      onClick={() => {
                        if (selected) return
                        switchToFile(file.taskFileId)
                      }}
                    >
                      <span className="mvdu-crop-gate__thumb-idx">{idx + 1}</span>
                      <span className="mvdu-crop-gate__file-name">
                        {shortFilename(file.filename)}
                      </span>
                      <span className={`mvdu-crop-gate__status mvdu-crop-gate__status--${statusKind}`}>
                        {statusLabel}
                      </span>
                    </button>
                  )
                })}
              </div>
            )}

            {multipage ? (
              pageEntries.map(entry => {
                const display = pageThumbDisplayStatus(entry, activePage)
                const clickable = display !== 'RUNNING'
                return (
                  <button
                    key={`${taskFileId}:page:${entry.page}`}
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
              })
            ) : boxes.length === 0 ? (
              <p className="mvdu-crop-gate__strip-empty">No boxes yet. Add a box for each slip.</p>
            ) : (
              boxes.map(box => (
                <button
                  key={`${taskFileId}:${box.id}`}
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
        </div>

        <footer className="mvdu-crop-gate__foot">
          <button
            type="button"
            className="erp-btn"
            disabled={extracting || busy || actionPending}
            title={
              extracting
                ? 'Extract is already running for this file'
                : undefined
            }
            onClick={() => void runAction(() => onCancel(), 'Could not cancel crop preview.')}
          >
            Cancel
          </button>
          <button
            type="button"
            className="erp-btn"
            disabled={actionLocked || (emptyHint && pendingCount === 0)}
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
            disabled={actionLocked || emptyHint || pageRasterMissing}
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
