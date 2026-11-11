import { describe, expect, it } from 'vitest'
import {
  addBox,
  allBoxesFromPages,
  boxesGeometrySignature,
  confirmPageInSet,
  countPendingPages,
  deleteBox,
  mergeServerBoxStatus,
  mergeWithNeighbor,
  nextPendingPage,
  normalizeIncomingBoxes,
  normalizePageEntries,
  pageNeedsReview,
  pageThumbDisplayStatus,
  parseCropPreviewFromSummary,
  rotateBox90,
  skipPageInSet,
  skippedPagesFromSet,
  unskipPageInSet,
  updateBoxNorm,
} from './boxOps'
import { applyCropConfirmOptimisticRun } from './cropGateConfirm'

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

  it('parses multipage crop_preview.pages and supports confirm / advance', () => {
    const { needsGate, preview } = parseCropPreviewFromSummary({
      needs_crop_preview: true,
      crop_preview: {
        phase: 'preview',
        page: 1,
        image_width: 200,
        image_height: 200,
        boxes: [{ id: 'a', index: 1, page: 1, region_norm: { x: 0.1, y: 0.1, w: 0.2, h: 0.2 } }],
        pages: [
          {
            page: 1,
            status: 'pending',
            image_width: 200,
            image_height: 200,
            boxes: [{ id: 'a', index: 1, page: 1, region_norm: { x: 0.1, y: 0.1, w: 0.2, h: 0.2 } }],
          },
          {
            page: 2,
            status: 'pending',
            image_width: 200,
            image_height: 200,
            boxes: [
              { id: 'b', index: 1, page: 2, region_norm: { x: 0.2, y: 0.2, w: 0.3, h: 0.3 } },
              { id: 'c', index: 2, page: 2, region_norm: { x: 0.55, y: 0.2, w: 0.3, h: 0.3 } },
            ],
          },
        ],
      },
    })
    expect(needsGate).toBe(true)
    expect(preview?.pages).toHaveLength(2)
    const pages = preview!.pages!
    expect(pageThumbDisplayStatus(pages[0]!, 1)).toBe('RUNNING')
    expect(pageThumbDisplayStatus(pages[1]!, 1)).toBe('PENDING')
    expect(countPendingPages(pages)).toBe(2)

    const afterP1 = confirmPageInSet(pages, 1, pages[0]!.boxes)
    expect(afterP1[0]!.status).toBe('confirmed')
    expect(pageThumbDisplayStatus(afterP1[0]!, 2)).toBe('CONFIRMED')
    expect(nextPendingPage(afterP1, 1)).toBe(2)
    expect(countPendingPages(afterP1)).toBe(1)

    const afterP2 = confirmPageInSet(afterP1, 2, afterP1[1]!.boxes)
    expect(countPendingPages(afterP2)).toBe(0)
    expect(nextPendingPage(afterP2, 2)).toBeNull()
    const all = allBoxesFromPages(afterP2)
    expect(all).toHaveLength(3)
    expect(all.filter(b => b.page === 2)).toHaveLength(2)
  })

  it('skip page excludes boxes from extract and un-skip restores pending', () => {
    const pages = normalizePageEntries([
      {
        page: 1,
        status: 'pending',
        image_width: 100,
        image_height: 100,
        boxes: [{ id: 'a', index: 1, page: 1, region_norm: { x: 0.1, y: 0.1, w: 0.2, h: 0.2 } }],
      },
      {
        page: 2,
        status: 'pending',
        image_width: 100,
        image_height: 100,
        boxes: [{ id: 'b', index: 1, page: 2, region_norm: { x: 0.2, y: 0.2, w: 0.3, h: 0.3 } }],
      },
    ])
    const skipped = skipPageInSet(pages, 1)
    expect(skipped[0]!.status).toBe('skipped')
    expect(pageThumbDisplayStatus(skipped[0]!, 2)).toBe('SKIPPED')
    expect(pageNeedsReview(skipped[0]!)).toBe(false)
    expect(countPendingPages(skipped)).toBe(1)
    expect(nextPendingPage(skipped, 1)).toBe(2)
    expect(skippedPagesFromSet(skipped)).toEqual([1])

    const confirmedP2 = confirmPageInSet(skipped, 2, skipped[1]!.boxes)
    const extractBoxes = allBoxesFromPages(confirmedP2)
    expect(extractBoxes.map(b => b.page)).toEqual([2])
    expect(extractBoxes.map(b => b.id)).toEqual(['b'])

    const restored = unskipPageInSet(confirmedP2, 1)
    expect(restored[0]!.status).toBe('pending')
    expect(restored[0]!.boxes[0]!.id).toBe('a')
    expect(pageNeedsReview(restored[0]!)).toBe(true)
  })

  it('dismissed crop preview does not reopen the gate', () => {
    const { needsGate, preview } = parseCropPreviewFromSummary({
      crop_preview_dismissed: true,
      crop_preview: {
        phase: 'preview',
        page: 1,
        boxes: [{ id: 'a', region_norm: { x: 0, y: 0, w: 0.5, h: 0.5 } }],
      },
    })
    expect(needsGate).toBe(false)
    expect(preview).toBeNull()
  })

  it('normalizePageEntries falls back to single page from boxes', () => {
    const pages = normalizePageEntries(undefined, {
      page: 1,
      boxes: normalizeIncomingBoxes([{ region_norm: { x: 0, y: 0, w: 0.4, h: 0.4 } }]),
      image_width: 100,
      image_height: 100,
    })
    expect(pages).toHaveLength(1)
    expect(pages[0]!.status).toBe('pending')
  })

  it('geometry signature ignores extract_status (avoids thumb blob churn on poll)', () => {
    const a = normalizeIncomingBoxes([
      {
        id: 'a',
        index: 1,
        region_norm: { x: 0.1, y: 0.1, w: 0.2, h: 0.5 },
        extract_status: 'pending',
      },
    ])
    const b = normalizeIncomingBoxes([
      {
        id: 'a',
        index: 1,
        region_norm: { x: 0.1, y: 0.1, w: 0.2, h: 0.5 },
        extract_status: 'running',
      },
    ])
    expect(boxesGeometrySignature(a)).toBe(boxesGeometrySignature(b))
    const merged = mergeServerBoxStatus(a, b)
    expect(merged[0]!.extract_status).toBe('running')
    expect(merged[0]!.region_norm).toEqual(a[0]!.region_norm)
  })
})

describe('applyCropConfirmOptimisticRun', () => {
  it('marks run executing and file extracting so poll/WS activate (no silent hang)', () => {
    const run = {
      id: 'run1',
      run_status: 'warning',
      files: [
        {
          task_file_id: 'tf1',
          file_status: 'warning',
          error_text: 'needs_crop_preview',
          result_summary_json: {
            needs_crop_preview: true,
            crop_preview: {
              phase: 'preview',
              page: 1,
              boxes: [{ id: 'a', region_norm: { x: 0.1, y: 0.1, w: 0.3, h: 0.8 } }],
            },
          },
        },
      ],
    }
    const next = applyCropConfirmOptimisticRun(run, 'tf1', {
      boxes: [{ id: 'a', region_norm: { x: 0.1, y: 0.1, w: 0.3, h: 0.8 } }],
      phase: 'extracting',
    })
    expect(next.run_status).toBe('executing')
    expect(next.files[0]!.file_status).toBe('running')
    expect(next.files[0]!.error_text).toBeNull()
    const summary = next.files[0]!.result_summary_json!
    expect(summary.needs_crop_preview).toBe(false)
    expect((summary.crop_preview as { phase: string }).phase).toBe('extracting')
  })
})
