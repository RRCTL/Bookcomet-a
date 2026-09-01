import { describe, expect, it } from 'vitest'
import {
  buildReceiptCropRequest,
  resolveCropTaskFileId,
  rowCanShowReceiptCropPreview,
  rowHasReceiptCropRegion,
} from './imageQualityCrop'

describe('resolveCropTaskFileId', () => {
  const files = [
    { taskFileId: 'file-a', originalFilename: 'synthetic.pdf' },
    { taskFileId: 'file-b', originalFilename: 'other.pdf' },
  ]

  it('matches source_file stem', () => {
    expect(resolveCropTaskFileId({ source_file: 'synthetic.pdf P2' }, files)).toBe('file-a')
  })

  it('matches spreadsheet row id prefix', () => {
    expect(resolveCropTaskFileId({ id: 'file-b-tsv1' }, files)).toBe('file-b')
  })
})

describe('buildReceiptCropRequest', () => {
  it('reads normalized region and page from provenance', () => {
    const req = buildReceiptCropRequest(
      {
        source_file: 'synthetic.pdf P3',
        extraction_provenance: {
          source_pdf_page: 3,
          receipt_region_norm: { x: 0.1, y: 0.2, w: 0.3, h: 0.4 },
        },
      },
      [{ taskFileId: 'file-a', originalFilename: 'synthetic.pdf' }],
    )
    expect(req).toEqual({
      taskFileId: 'file-a',
      page: 3,
      regionNorm: { x: 0.1, y: 0.2, w: 0.3, h: 0.4 },
      regionBbox: null,
    })
  })
})

describe('rowCanShowReceiptCropPreview', () => {
  const files = [{ taskFileId: 'file-a', originalFilename: 'synthetic.pdf' }]

  it('is true for M-VDU rows with region provenance (even without AQ)', () => {
    const row = {
      source_file: 'synthetic.pdf P1',
      extraction_provenance: {
        source_pdf_page: 1,
        receipt_region_norm: { x: 0.05, y: 0.1, w: 0.4, h: 0.35 },
      },
    }
    expect(rowHasReceiptCropRegion(row)).toBe(true)
    expect(rowCanShowReceiptCropPreview(row, files)).toBe(true)
  })

  it('is false for normal VLM rows without region (file alone is not enough)', () => {
    const row = {
      source_file: 'synthetic.pdf',
      extraction_provenance: { source_pdf_page: 1 },
    }
    expect(rowHasReceiptCropRegion(row)).toBe(false)
    expect(rowCanShowReceiptCropPreview(row, files)).toBe(false)
  })
})
