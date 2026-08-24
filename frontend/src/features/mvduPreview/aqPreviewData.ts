/** VM-captured AQ metrics for DEV preview — numbers only, no image assets in repo. */

export type AqPreviewCase = {
  id: string
  label: string
  selection: string
  ui_label: string
  status: string
  ui_state: string
  issues: string[]
  reason?: string
  score_before?: number
  score_after?: number | null
  recipe: Array<Record<string, unknown>>
  quality_before?: Record<string, number>
  quality_after?: Record<string, number> | null
  /** SVG sketch variant — abstract bars only. */
  sketch: 'clean' | 'faded' | 'glare' | 'blurry'
  has_enhanced_sketch?: boolean
}

export const AQ_PREVIEW_CASES: AqPreviewCase[] = [
  {
    id: 'clean',
    label: 'Clean',
    selection: 'original_selected',
    ui_label: 'Original · clear',
    status: 'clear',
    ui_state: 'original_clear',
    issues: [],
    reason: 'Crop passes quality gates; prefer original.',
    score_before: 0.668114,
    score_after: null,
    recipe: [],
    quality_before: {
      blur_variance: 2648.0824,
      local_contrast: 0.210915,
      glare_fraction: 0.0,
      glare_hotspot_fraction: 0.0,
      ink_fraction: 1.0,
      edge_density: 0.040476,
      luminance_std: 0.210915,
      mean_luminance: 0.843288,
      noise_estimate: 0.014138,
      width: 320.0,
      height: 420.0,
    },
    quality_after: null,
    sketch: 'clean',
  },
  {
    id: 'faded',
    label: 'Faded',
    selection: 'enhanced_selected',
    ui_label: 'Auto-enhanced · view original',
    status: 'recoverable',
    ui_state: 'faded_receipt',
    issues: ['blur', 'low_contrast'],
    reason: 'Recoverable quality issues detected; try minimal enhancement recipe.',
    score_before: 0.184375,
    score_after: 0.206558,
    recipe: [
      { op: 'deskew', max_degrees: 12.0 },
      { op: 'lab_clahe', clip_limit: 2.0, tile_grid: 8 },
      { op: 'unsharp_mild', amount: 0.35, radius: 1.0 },
    ],
    quality_before: {
      blur_variance: 25.3871,
      local_contrast: 0.021468,
      glare_fraction: 0.0,
      glare_hotspot_fraction: 0.0,
      ink_fraction: 1.0,
      edge_density: 0.0,
      luminance_std: 0.021468,
      mean_luminance: 0.909003,
      noise_estimate: 0.001426,
      width: 320.0,
      height: 420.0,
    },
    quality_after: {
      blur_variance: 51.5798,
      local_contrast: 0.025022,
      glare_fraction: 0.0,
      glare_hotspot_fraction: 0.0,
      ink_fraction: 1.0,
      edge_density: 0.0,
      luminance_std: 0.025022,
      mean_luminance: 0.907012,
      noise_estimate: 0.0021,
      width: 320.0,
      height: 420.0,
    },
    sketch: 'faded',
    has_enhanced_sketch: true,
  },
  {
    id: 'glare',
    label: 'Glare',
    selection: 'recapture_requested',
    ui_label: 'Image cannot be verified',
    status: 'unrecoverable',
    ui_state: 'glare_cannot_verify',
    issues: ['glare'],
    reason: 'Glare / overexposure covers too much of the crop; retake with indirect light.',
    score_before: 0.588,
    score_after: null,
    recipe: [],
    quality_before: {
      blur_variance: 800.0,
      local_contrast: 0.13,
      glare_fraction: 0.21,
      glare_hotspot_fraction: 0.14,
      ink_fraction: 0.79,
      edge_density: 0.028,
      luminance_std: 0.13,
      mean_luminance: 0.9,
      noise_estimate: 0.006,
      width: 320.0,
      height: 420.0,
    },
    quality_after: null,
    sketch: 'glare',
  },
  {
    id: 'blurry',
    label: 'Blurry',
    selection: 'recapture_requested',
    ui_label: 'Image cannot be verified',
    status: 'unrecoverable',
    ui_state: 'blur_recapture',
    issues: ['blur', 'low_contrast'],
    reason: 'Image is too blurry to verify totals or dates; recapture recommended.',
    score_before: 0.26,
    score_after: null,
    recipe: [],
    quality_before: {
      blur_variance: 12.0,
      local_contrast: 0.09,
      glare_fraction: 0.0,
      glare_hotspot_fraction: 0.0,
      ink_fraction: 0.95,
      edge_density: 0.008,
      luminance_std: 0.09,
      mean_luminance: 0.88,
      noise_estimate: 0.002,
      width: 320.0,
      height: 420.0,
    },
    quality_after: null,
    sketch: 'blurry',
  },
]
