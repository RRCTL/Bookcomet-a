/** Map page-normalized crop boxes onto an object-fit:contain image stage. */

import type { CSSProperties } from 'react'
import type { RegionNorm } from './types'

export type FitRect = {
  left: number
  top: number
  width: number
  height: number
}

export type Size = { width: number; height: number }

/**
 * CSS object-fit: contain content rectangle inside a stage (centered).
 * When the stage aspect differs from the image, letterbox bars appear on the
 * unused axis — overlays must use this rect, not the full stage.
 */
export function containFitRect(
  stage: Size,
  image: Size,
): FitRect {
  const sw = Math.max(0, stage.width)
  const sh = Math.max(0, stage.height)
  const iw = Math.max(0, image.width)
  const ih = Math.max(0, image.height)
  if (sw <= 0 || sh <= 0) {
    return { left: 0, top: 0, width: 0, height: 0 }
  }
  if (iw <= 0 || ih <= 0) {
    return { left: 0, top: 0, width: sw, height: sh }
  }
  const scale = Math.min(sw / iw, sh / ih)
  const width = iw * scale
  const height = ih * scale
  return {
    left: (sw - width) / 2,
    top: (sh - height) / 2,
    width,
    height,
  }
}

/** Position a region_norm box as percentages of the stage (for absolute children). */
export function regionNormToStageStyle(
  region: RegionNorm,
  fit: FitRect,
  stage: Size,
): CSSProperties {
  if (stage.width <= 0 || stage.height <= 0 || fit.width <= 0 || fit.height <= 0) {
    return {
      left: `${region.x * 100}%`,
      top: `${region.y * 100}%`,
      width: `${region.w * 100}%`,
      height: `${region.h * 100}%`,
    }
  }
  return {
    left: `${((fit.left + region.x * fit.width) / stage.width) * 100}%`,
    top: `${((fit.top + region.y * fit.height) / stage.height) * 100}%`,
    width: `${(region.w * fit.width / stage.width) * 100}%`,
    height: `${(region.h * fit.height / stage.height) * 100}%`,
  }
}

/** Convert a pointer position on the stage into region_norm (image content space). */
export function clientPointToRegionNorm(
  clientX: number,
  clientY: number,
  stageRect: { left: number; top: number },
  fit: FitRect,
): { x: number; y: number } {
  const x = (clientX - stageRect.left - fit.left) / Math.max(1e-6, fit.width)
  const y = (clientY - stageRect.top - fit.top) / Math.max(1e-6, fit.height)
  return {
    x: Math.max(0, Math.min(1, x)),
    y: Math.max(0, Math.min(1, y)),
  }
}
