/** Synthetic TF-01/TF-02 demo data — abstract geometry only, no real receipt imagery. */

export type RowReviewState =
  | 'ready'
  | 'provisional'
  | 'needs_region_review'
  | 'needs_data_review'
  | 'superseded'
  | 'rejected'
  | 'processing'

export type RegionNorm = { x: number; y: number; w: number; h: number }

export type DemoRegion = {
  region_id: string
  label: string
  page: number
  geometry: RegionNorm
  source: 'opencv_contour' | 'vlm_layout' | 'force_split' | 'manual'
  confidence: number
  /** Held from OCR until user resolves (count/overlap conflict). */
  hold_ocr: boolean
  status: 'candidate' | 'ocr_done' | 'conflict' | 'superseded'
}

export type DemoRow = {
  row_id: string
  region_id: string
  review_state: RowReviewState
  page: number
  region_label: string
  source: string
  date: string
  payee: string
  currency: string
  amount: number | null
  category: string
  note?: string
}

export const DEMO_PAGE = {
  width: 900,
  height: 640,
  title: 'Synthetic multi-receipt page (preview)',
}

export function initialRegions(): DemoRegion[] {
  return [
    {
      region_id: 'rrg_r1',
      label: 'R1',
      page: 1,
      geometry: { x: 0.06, y: 0.08, w: 0.4, h: 0.38 },
      source: 'opencv_contour',
      confidence: 0.91,
      hold_ocr: false,
      status: 'ocr_done',
    },
    {
      region_id: 'rrg_r2',
      label: 'R2',
      page: 1,
      geometry: { x: 0.52, y: 0.1, w: 0.4, h: 0.34 },
      source: 'vlm_layout',
      confidence: 0.72,
      hold_ocr: false,
      status: 'ocr_done',
    },
    {
      region_id: 'rrg_r3',
      label: 'R3',
      page: 1,
      geometry: { x: 0.08, y: 0.52, w: 0.84, h: 0.4 },
      source: 'opencv_contour',
      confidence: 0.48,
      hold_ocr: true,
      status: 'conflict',
    },
  ]
}

export function initialRows(): DemoRow[] {
  return [
    {
      row_id: 'row_1',
      region_id: 'rrg_r1',
      review_state: 'ready',
      page: 1,
      region_label: 'R1',
      source: 'OpenCV',
      date: '2026-08-12',
      payee: 'North Desk Supplies',
      currency: 'HKD',
      amount: 328.5,
      category: 'Office',
    },
    {
      row_id: 'row_2',
      region_id: 'rrg_r2',
      review_state: 'provisional',
      page: 1,
      region_label: 'R2',
      source: 'VLM layout',
      date: '2026-08-13',
      payee: 'Harbor Cafe',
      currency: 'HKD',
      amount: 86.0,
      category: 'Meals',
      note: 'Single detector — provisional export allowed',
    },
    {
      row_id: 'row_3',
      region_id: 'rrg_r3',
      review_state: 'needs_region_review',
      page: 1,
      region_label: 'R3',
      source: 'OpenCV · conflict',
      date: '',
      payee: '',
      currency: '',
      amount: null,
      category: '',
      note: 'OCR held — overlap/count conflict; placeholder keeps count visible',
    },
  ]
}

/** Mock targeted re-OCR after split: only the conflict region becomes two children. */
export function applySplitConflict(
  regions: DemoRegion[],
  rows: DemoRow[],
  regionId: string,
): { regions: DemoRegion[]; rows: DemoRow[] } {
  const parent = regions.find(r => r.region_id === regionId)
  if (!parent || parent.status === 'superseded') {
    return { regions, rows }
  }

  const g = parent.geometry
  const left: DemoRegion = {
    region_id: `${regionId}_a`,
    label: `${parent.label}a`,
    page: parent.page,
    geometry: { x: g.x, y: g.y, w: g.w * 0.48, h: g.h },
    source: 'manual',
    confidence: 0.88,
    hold_ocr: false,
    status: 'ocr_done',
  }
  const right: DemoRegion = {
    region_id: `${regionId}_b`,
    label: `${parent.label}b`,
    page: parent.page,
    geometry: { x: g.x + g.w * 0.52, y: g.y, w: g.w * 0.48, h: g.h },
    source: 'manual',
    confidence: 0.87,
    hold_ocr: false,
    status: 'ocr_done',
  }

  const nextRegions = regions.map(r =>
    r.region_id === regionId ? { ...r, status: 'superseded' as const } : r,
  )
  nextRegions.push(left, right)

  const nextRows = rows.map(row =>
    row.region_id === regionId
      ? { ...row, review_state: 'superseded' as const, note: 'Superseded by split + targeted re-OCR' }
      : row,
  )
  nextRows.push(
    {
      row_id: `row_${left.region_id}`,
      region_id: left.region_id,
      review_state: 'ready',
      page: 1,
      region_label: left.label,
      source: 'Manual split · re-OCR',
      date: '2026-08-14',
      payee: 'East Transit',
      currency: 'HKD',
      amount: 45.0,
      category: 'Travel',
      note: 'Targeted re-OCR: this crop only',
    },
    {
      row_id: `row_${right.region_id}`,
      region_id: right.region_id,
      review_state: 'ready',
      page: 1,
      region_label: right.label,
      source: 'Manual split · re-OCR',
      date: '2026-08-14',
      payee: 'West Print Shop',
      currency: 'HKD',
      amount: 210.0,
      category: 'Office',
      note: 'Targeted re-OCR: this crop only',
    },
  )

  return { regions: nextRegions, rows: nextRows }
}
