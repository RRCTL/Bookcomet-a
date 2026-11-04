import { describe, expect, it } from 'vitest'
import {
  clientPointToRegionNorm,
  containFitRect,
  regionNormToStageStyle,
} from './overlayCoords'

describe('containFitRect (object-fit: contain letterboxing)', () => {
  it('fills the stage when aspect ratios match', () => {
    const fit = containFitRect({ width: 400, height: 300 }, { width: 800, height: 600 })
    expect(fit.left).toBeCloseTo(0)
    expect(fit.top).toBeCloseTo(0)
    expect(fit.width).toBeCloseTo(400)
    expect(fit.height).toBeCloseTo(300)
  })

  it('letterboxes left/right for a taller image in a wide stage (side-by-side receipts case)', () => {
    // Tall scan (e.g. two receipts) shown in a wide stage → pillars on L/R.
    const fit = containFitRect({ width: 800, height: 400 }, { width: 600, height: 900 })
    expect(fit.top).toBeCloseTo(0)
    expect(fit.height).toBeCloseTo(400)
    expect(fit.width).toBeCloseTo((600 / 900) * 400)
    expect(fit.left).toBeCloseTo((800 - fit.width) / 2)
    expect(fit.left).toBeGreaterThan(100)
  })

  it('letterboxes top/bottom for a wide image in a tall stage', () => {
    const fit = containFitRect({ width: 300, height: 600 }, { width: 900, height: 600 })
    expect(fit.left).toBeCloseTo(0)
    expect(fit.width).toBeCloseTo(300)
    expect(fit.height).toBeCloseTo((600 / 900) * 300)
    expect(fit.top).toBeCloseTo((600 - fit.height) / 2)
  })
})

describe('regionNormToStageStyle', () => {
  it('maps norm boxes into the contained image rect, not the full stage', () => {
    const stage = { width: 800, height: 400 }
    const fit = containFitRect(stage, { width: 600, height: 900 })
    // Two side-by-side receipts roughly in the middle band of the page.
    const box1 = regionNormToStageStyle({ x: 0.12, y: 0.05, w: 0.36, h: 0.9 }, fit, stage)
    const box2 = regionNormToStageStyle({ x: 0.52, y: 0.05, w: 0.36, h: 0.9 }, fit, stage)

    const left1 = parseFloat(String(box1.left))
    const width1 = parseFloat(String(box1.width))
    const left2 = parseFloat(String(box2.left))

    // Naive stage-% would put box1 at 12% of 800 = 96px from stage left.
    // Contained mapping must start further right (after the left letterbox).
    const naiveLeft1Pct = 12
    expect(left1).toBeGreaterThan(naiveLeft1Pct)
    // Box width must be narrower than naive 36% of stage.
    expect(width1).toBeLessThan(36)
    // Boxes stay inside the content rect.
    expect(left1).toBeGreaterThan((fit.left / stage.width) * 100 - 0.01)
    expect(left2 + parseFloat(String(box2.width))).toBeLessThan(
      ((fit.left + fit.width) / stage.width) * 100 + 0.01,
    )
  })

  it('matches naive stage percentages when there is no letterboxing', () => {
    const stage = { width: 400, height: 300 }
    const fit = containFitRect(stage, { width: 800, height: 600 })
    const style = regionNormToStageStyle({ x: 0.25, y: 0.1, w: 0.5, h: 0.8 }, fit, stage)
    expect(parseFloat(String(style.left))).toBeCloseTo(25)
    expect(parseFloat(String(style.top))).toBeCloseTo(10)
    expect(parseFloat(String(style.width))).toBeCloseTo(50)
    expect(parseFloat(String(style.height))).toBeCloseTo(80)
  })
})

describe('clientPointToRegionNorm', () => {
  it('converts pointer coords relative to the contained image, not letterbox bars', () => {
    const stage = { width: 800, height: 400 }
    const fit = containFitRect(stage, { width: 600, height: 900 })
    const stageRect = { left: 100, top: 50 }
    // Click the center of the contained image content.
    const cx = stageRect.left + fit.left + fit.width / 2
    const cy = stageRect.top + fit.top + fit.height / 2
    const p = clientPointToRegionNorm(cx, cy, stageRect, fit)
    expect(p.x).toBeCloseTo(0.5)
    expect(p.y).toBeCloseTo(0.5)
  })

  it('maps the left letterbox edge to x≈0 (clamped)', () => {
    const stage = { width: 800, height: 400 }
    const fit = containFitRect(stage, { width: 600, height: 900 })
    const stageRect = { left: 0, top: 0 }
    const p = clientPointToRegionNorm(0, fit.height / 2, stageRect, fit)
    expect(p.x).toBe(0)
  })
})
