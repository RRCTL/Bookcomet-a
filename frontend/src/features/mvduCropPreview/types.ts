/** M-VDU crop-preview gate types (Comet MultiCut pattern). */

export type RegionNorm = { x: number; y: number; w: number; h: number }
export type RegionBbox = { x: number; y: number; w: number; h: number }

export type CropExtractStatus = 'pending' | 'running' | 'done' | 'error'

/** Confirm-state stored in crop_preview.pages[].status (RUNNING is viewer-only). */
export type CropPageConfirmStatus = 'pending' | 'confirmed' | 'empty'

/** Display status for the far-right page thumb rail. */
export type CropPageThumbStatus = 'RUNNING' | 'PENDING' | 'CONFIRMED' | 'EMPTY'

export type CropPreviewBox = {
  id: string
  index: number
  page: number
  region_norm: RegionNorm
  region_bbox?: RegionBbox
  rotation_deg: number
  extract_status: CropExtractStatus
  source?: string
}

export type CropPreviewPhase = 'preview' | 'extracting' | 'done'

export type CropPreviewPageEntry = {
  page: number
  status: CropPageConfirmStatus
  image_width: number
  image_height: number
  boxes: CropPreviewBox[]
  needs_split_review?: boolean
  message?: string
  layout_failure_reason?: string
}

export type CropPreviewPayload = {
  phase: CropPreviewPhase
  page: number
  image_width: number
  image_height: number
  boxes: CropPreviewBox[]
  needs_split_review: boolean
  active_box_id?: string | null
  message?: string
  layout_failure_reason?: string
  /** Page control entries. Length ≤1 → hide page thumb rail. */
  pages?: CropPreviewPageEntry[]
}

export type CropPreviewGateFile = {
  taskFileId: string
  originalFilename?: string | null
  resultSummary: Record<string, unknown>
}

/** File entry for in-gate file strip (2+ awaiting crop preview). */
export type CropPreviewGateNavFile = {
  taskFileId: string
  filename?: string | null
  preview: CropPreviewPayload
}

/** In-memory gate edits kept when switching files mid-review. */
export type CropPreviewFileGateMemory = {
  pageEntries: CropPreviewPageEntry[]
  activePage: number
  boxes: CropPreviewBox[]
  selectedId: string | null
}
