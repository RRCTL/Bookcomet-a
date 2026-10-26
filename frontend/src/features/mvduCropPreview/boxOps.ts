import type { CropPreviewBox, RegionNorm } from './types'

function clamp01(n: number): number {
  return Math.max(0, Math.min(1, n))
}

function newId(): string {
  return `box_${Math.random().toString(36).slice(2, 10)}`
}

export function normalizeIncomingBoxes(raw: unknown): CropPreviewBox[] {
  if (!Array.isArray(raw)) return []
  const out: CropPreviewBox[] = []
  raw.forEach((item, i) => {
    if (!item || typeof item !== 'object') return
    const r = item as Record<string, unknown>
    const normRaw = (r.region_norm ?? r.regionNorm) as Record<string, unknown> | undefined
    let norm: RegionNorm | null = null
    if (normRaw) {
      const x = Number(normRaw.x)
      const y = Number(normRaw.y)
      const w = Number(normRaw.w)
      const h = Number(normRaw.h)
      if ([x, y, w, h].every(Number.isFinite) && w > 0 && h > 0) {
        norm = { x: clamp01(x), y: clamp01(y), w: clamp01(w), h: clamp01(h) }
      }
    }
    if (!norm) return
    const status = String(r.extract_status ?? 'pending')
    out.push({
      id: String(r.id || newId()),
      index: Number(r.index) > 0 ? Number(r.index) : i + 1,
      page: Number(r.page) > 0 ? Number(r.page) : 1,
      region_norm: norm,
      region_bbox: r.region_bbox as CropPreviewBox['region_bbox'],
      rotation_deg: Number(r.rotation_deg ?? r.rotation ?? 0) % 360,
      extract_status:
        status === 'running' || status === 'done' || status === 'error' ? status : 'pending',
      source: r.source != null ? String(r.source) : undefined,
    })
  })
  return renumber(out)
}

export function renumber(boxes: CropPreviewBox[]): CropPreviewBox[] {
  return boxes.map((b, i) => ({ ...b, index: i + 1 }))
}

export function deleteBox(boxes: CropPreviewBox[], id: string): CropPreviewBox[] {
  return renumber(boxes.filter(b => b.id !== id))
}

export function addBox(boxes: CropPreviewBox[], page = 1): CropPreviewBox[] {
  return renumber([
    ...boxes,
    {
      id: newId(),
      index: boxes.length + 1,
      page,
      region_norm: { x: 0.1, y: 0.1, w: 0.3, h: 0.25 },
      rotation_deg: 0,
      extract_status: 'pending',
      source: 'manual',
    },
  ])
}

/** Merge box `id` with its nearest neighbor (by center distance). */
export function mergeWithNeighbor(boxes: CropPreviewBox[], id: string): CropPreviewBox[] {
  const idx = boxes.findIndex(b => b.id === id)
  if (idx < 0 || boxes.length < 2) return boxes
  const a = boxes[idx]!
  const acx = a.region_norm.x + a.region_norm.w / 2
  const acy = a.region_norm.y + a.region_norm.h / 2
  let best = -1
  let bestDist = Infinity
  boxes.forEach((b, i) => {
    if (i === idx) return
    const cx = b.region_norm.x + b.region_norm.w / 2
    const cy = b.region_norm.y + b.region_norm.h / 2
    const d = (cx - acx) ** 2 + (cy - acy) ** 2
    if (d < bestDist) {
      bestDist = d
      best = i
    }
  })
  if (best < 0) return boxes
  const b = boxes[best]!
  const x1 = Math.min(a.region_norm.x, b.region_norm.x)
  const y1 = Math.min(a.region_norm.y, b.region_norm.y)
  const x2 = Math.max(a.region_norm.x + a.region_norm.w, b.region_norm.x + b.region_norm.w)
  const y2 = Math.max(a.region_norm.y + a.region_norm.h, b.region_norm.y + b.region_norm.h)
  const merged: CropPreviewBox = {
    ...a,
    region_norm: {
      x: clamp01(x1),
      y: clamp01(y1),
      w: clamp01(x2 - x1),
      h: clamp01(y2 - y1),
    },
    source: 'manual_merge',
  }
  const next = boxes.filter((_, i) => i !== idx && i !== best)
  next.splice(Math.min(idx, best), 0, merged)
  return renumber(next)
}

export function rotateBox90(boxes: CropPreviewBox[], id: string, clockwise = true): CropPreviewBox[] {
  return boxes.map(b =>
    b.id === id
      ? { ...b, rotation_deg: (b.rotation_deg + (clockwise ? 90 : -90) + 360) % 360 }
      : b,
  )
}

export function updateBoxNorm(
  boxes: CropPreviewBox[],
  id: string,
  region_norm: RegionNorm,
): CropPreviewBox[] {
  const next = {
    x: clamp01(region_norm.x),
    y: clamp01(region_norm.y),
    w: clamp01(region_norm.w),
    h: clamp01(region_norm.h),
  }
  if (next.w < 0.01) next.w = 0.01
  if (next.h < 0.01) next.h = 0.01
  if (next.x + next.w > 1) next.w = 1 - next.x
  if (next.y + next.h > 1) next.h = 1 - next.y
  return boxes.map(b => (b.id === id ? { ...b, region_norm: next } : b))
}

export function boxesForApi(boxes: CropPreviewBox[]) {
  return boxes.map(b => ({
    id: b.id,
    index: b.index,
    page: b.page,
    region_norm: b.region_norm,
    rotation_deg: b.rotation_deg,
  }))
}

export function parseCropPreviewFromSummary(
  summary: Record<string, unknown> | null | undefined,
): {
  needsGate: boolean
  preview: import('./types').CropPreviewPayload | null
} {
  if (!summary) return { needsGate: false, preview: null }
  const needsGate = Boolean(summary.needs_crop_preview) || summary.crop_status === 'needs_crop_preview'
  const raw = summary.crop_preview
  if (!raw || typeof raw !== 'object') {
    return { needsGate, preview: null }
  }
  const cp = raw as Record<string, unknown>
  const boxes = normalizeIncomingBoxes(cp.boxes)
  const phase = String(cp.phase || 'preview')
  return {
    needsGate: needsGate || phase === 'preview',
    preview: {
      phase: phase === 'extracting' || phase === 'done' ? phase : 'preview',
      page: Number(cp.page) > 0 ? Number(cp.page) : 1,
      image_width: Number(cp.image_width) || 1,
      image_height: Number(cp.image_height) || 1,
      boxes,
      needs_split_review: Boolean(cp.needs_split_review),
      active_box_id: cp.active_box_id != null ? String(cp.active_box_id) : null,
      message: cp.message != null ? String(cp.message) : undefined,
      layout_failure_reason:
        cp.layout_failure_reason != null ? String(cp.layout_failure_reason) : undefined,
    },
  }
}
