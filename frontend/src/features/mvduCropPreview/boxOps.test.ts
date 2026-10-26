import { describe, expect, it } from 'vitest'
import {
  addBox,
  deleteBox,
  mergeWithNeighbor,
  normalizeIncomingBoxes,
  parseCropPreviewFromSummary,
  rotateBox90,
  updateBoxNorm,
} from './boxOps'

describe('mvdu crop preview boxOps', () => {
  it('normalizes and renumbers Detect boxes', () => {
    const boxes = normalizeIncomingBoxes([
      { id: 'a', index: 9, region_norm: { x: 0.1, y: 0.2, w: 0.3, h: 0.4 } },
      { id: 'b', region_norm: { x: 0.5, y: 0.1, w: 0.2, h: 0.2 } },
    ])
    expect(boxes).toHaveLength(2)
    expect(boxes[0]!.index).toBe(1)
    expect(boxes[1]!.index).toBe(2)
  })

  it('supports add / delete / merge / rotate / nudge', () => {
    let boxes = normalizeIncomingBoxes([
      { id: 'a', region_norm: { x: 0.1, y: 0.1, w: 0.2, h: 0.2 } },
      { id: 'b', region_norm: { x: 0.35, y: 0.12, w: 0.2, h: 0.2 } },
    ])
    boxes = addBox(boxes)
    expect(boxes).toHaveLength(3)
    boxes = deleteBox(boxes, boxes[2]!.id)
    expect(boxes).toHaveLength(2)
    boxes = mergeWithNeighbor(boxes, 'a')
    expect(boxes).toHaveLength(1)
    expect(boxes[0]!.region_norm.w).toBeGreaterThan(0.2)
    boxes = rotateBox90(boxes, boxes[0]!.id)
    expect(boxes[0]!.rotation_deg).toBe(90)
    boxes = updateBoxNorm(boxes, boxes[0]!.id, { x: 0.05, y: 0.05, w: 0.4, h: 0.3 })
    expect(boxes[0]!.region_norm.x).toBeCloseTo(0.05)
  })

  it('parses gate payload from result summary', () => {
    const { needsGate, preview } = parseCropPreviewFromSummary({
      needs_crop_preview: true,
      crop_preview: {
        phase: 'preview',
        page: 1,
        image_width: 800,
        image_height: 600,
        needs_split_review: false,
        boxes: [{ id: 'x', index: 1, region_norm: { x: 0, y: 0, w: 0.5, h: 0.5 } }],
      },
    })
    expect(needsGate).toBe(true)
    expect(preview?.boxes).toHaveLength(1)
  })

  it('empty Detect summary still needs gate', () => {
    const { needsGate, preview } = parseCropPreviewFromSummary({
      needs_crop_preview: true,
      needs_split_review: true,
      crop_preview: {
        phase: 'preview',
        page: 1,
        image_width: 100,
        image_height: 100,
        needs_split_review: true,
        boxes: [],
        message: 'Draw boxes or confirm forced split',
      },
    })
    expect(needsGate).toBe(true)
    expect(preview?.boxes).toHaveLength(0)
    expect(preview?.message).toMatch(/Draw boxes/)
  })
})
