/** Helpers to surface AQ-01/AQ-02 provenance on Table Review rows (no image assets). */

export type ImageQualityInfo = {
  present: boolean
  status: string
  uiLabel: string
  uiState: string
  selection: string
  reason: string
  issues: string[]
  recipeOps: string[]
  scoreBefore: number | null
  scoreAfter: number | null
  qualityBefore: Record<string, number> | null
  qualityAfter: Record<string, number> | null
}

const EMPTY: ImageQualityInfo = {
  present: false,
  status: '',
  uiLabel: '',
  uiState: '',
  selection: '',
  reason: '',
  issues: [],
  recipeOps: [],
  scoreBefore: null,
  scoreAfter: null,
  qualityBefore: null,
  qualityAfter: null,
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null
}

function asNumber(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v
  if (typeof v === 'string' && v.trim() && !Number.isNaN(Number(v))) return Number(v)
  return null
}

function asStringList(v: unknown): string[] {
  if (!Array.isArray(v)) return []
  return v.map(x => String(x)).filter(Boolean)
}

function asNumberRecord(v: unknown): Record<string, number> | null {
  const r = asRecord(v)
  if (!r) return null
  const out: Record<string, number> = {}
  for (const [k, val] of Object.entries(r)) {
    const n = asNumber(val)
    if (n != null) out[k] = n
  }
  return Object.keys(out).length ? out : null
}

/** Read compact image_quality block from extraction_provenance (backend AQ audit). */
export function readImageQuality(row: {
  extraction_provenance?: Record<string, unknown> | null
}): ImageQualityInfo {
  const prov = asRecord(row.extraction_provenance)
  const iq = asRecord(prov?.image_quality)
  if (!iq || iq.enabled === false) return EMPTY

  const recipe = Array.isArray(iq.recipe) ? iq.recipe : []
  const recipeOps = recipe
    .map(step => {
      const s = asRecord(step)
      return s?.op != null ? String(s.op) : ''
    })
    .filter(Boolean)

  const status = String(iq.status ?? '')
  const selection = String(iq.selection ?? '')
  const uiLabel =
    String(iq.ui_label ?? '') ||
    (selection === 'enhanced_selected'
      ? 'Auto-enhanced'
      : selection === 'recapture_requested'
        ? 'Cannot verify'
        : status === 'clear'
          ? 'Original · clear'
          : status || 'Quality')

  return {
    present: true,
    status,
    uiLabel,
    uiState: String(iq.ui_state ?? ''),
    selection,
    reason: String(iq.reason ?? ''),
    issues: asStringList(iq.issues),
    recipeOps,
    scoreBefore: asNumber(iq.score_before),
    scoreAfter: asNumber(iq.score_after),
    qualityBefore: asNumberRecord(iq.quality_before),
    qualityAfter: asNumberRecord(iq.quality_after),
  }
}

export function imageQualityChipStyle(status: string): { bg: string; fg: string; border: string } {
  const s = status.toLowerCase()
  if (s === 'clear') return { bg: '#ecfdf5', fg: '#065f46', border: '#a7f3d0' }
  if (s === 'recoverable') return { bg: '#fffbeb', fg: '#92400e', border: '#fcd34d' }
  if (s === 'unrecoverable') return { bg: '#fef2f2', fg: '#991b1b', border: '#fecaca' }
  return { bg: '#f8fafc', fg: '#475569', border: '#e2e8f0' }
}

export const IMAGE_QUALITY_METRIC_KEYS = [
  'blur_variance',
  'local_contrast',
  'glare_hotspot_fraction',
  'glare_fraction',
  'ink_fraction',
  'edge_density',
] as const
