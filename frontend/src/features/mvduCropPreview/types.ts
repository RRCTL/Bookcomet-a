/** M-VDU crop-preview gate types (Comet MultiCut pattern). */

export type RegionNorm = { x: number; y: number; w: number; h: number }
export type RegionBbox = { x: number; y: number; w: number; h: number }

export type CropExtractStatus = 'pending' | 'running' | 'done' | 'error'

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
}

export type CropPreviewGateFile = {
  taskFileId: string
  originalFilename?: string | null
  resultSummary: Record<string, unknown>
}
