import { describe, expect, it } from 'vitest'
import { buildReceiptCropRequest, resolveCropTaskFileId } from './imageQualityCrop'

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
