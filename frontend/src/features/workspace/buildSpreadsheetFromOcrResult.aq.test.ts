import { describe, expect, it } from 'vitest'
import {
  buildSpreadsheetRowsFromOcrResult,
  provenanceSpreadsheetExtras,
  spreadsheetRowsToArapTransactions,
} from './buildSpreadsheetFromOcrResult'
import { readImageQuality } from '../../utils/imageQualityUi'

const SAMPLE_IQ = {
  enabled: true,
  selection: 'enhanced_selected',
  ui_label: 'Auto-enhanced · view original',
  status: 'recoverable',
  reason: 'Recoverable quality issues detected',
  issues: ['low_contrast'],
  score_before: 0.1,
  score_after: 0.2,
  recipe: [{ op: 'lab_clahe' }],
  quality_before: { blur_variance: 10 },
  quality_after: { blur_variance: 20 },
}

describe('provenanceSpreadsheetExtras', () => {
  it('keeps nested extraction_provenance.image_quality', () => {
    const extras = provenanceSpreadsheetExtras({
      extraction_provenance: { image_quality: SAMPLE_IQ, region: { x: 1 } },
    })
    expect(extras.extraction_provenance).toEqual({
      image_quality: SAMPLE_IQ,
      region: { x: 1 },
    })
  })

  it('wraps bare image_quality into extraction_provenance', () => {
    const extras = provenanceSpreadsheetExtras({ image_quality: SAMPLE_IQ })
    expect(extras.extraction_provenance).toEqual({ image_quality: SAMPLE_IQ })
  })
})

describe('AQ provenance survives spreadsheet → ARAP mapping', () => {
  it('Image quality chip can read provenance after build', () => {
    const { spreadsheetData } = buildSpreadsheetRowsFromOcrResult({
      fileId: 'f1',
      fileName: 'synthetic.pdf',
      processingMode: 'AP',
      rowIndexStart: 1,
      result: {
        ai_enhanced: {
          tsv_rows: [
            {
              date: '2024-01-01',
              amount: '12.00',
              currency: 'HKD',
              payee: 'Synthetic Shop',
              memo: 'fictional row',
              confidence: 0.95,
              extraction_provenance: { image_quality: SAMPLE_IQ },
            },
          ],
        },
      },
    })
    expect(spreadsheetData).toHaveLength(1)
    const arap = spreadsheetRowsToArapTransactions(spreadsheetData, 'AP')
    const iq = readImageQuality(arap[0]!)
    expect(iq.present).toBe(true)
    expect(iq.status).toBe('recoverable')
    expect(iq.uiLabel).toContain('Auto-enhanced')
  })

  it('stamps page receipt_bbox onto every M-VDU row without row-level region', () => {
    const { spreadsheetData } = buildSpreadsheetRowsFromOcrResult({
      fileId: 'f1',
      fileName: 'synthetic.pdf',
      processingMode: 'AP',
      rowIndexStart: 1,
      result: {
        document_type: 'multi_page_pdf',
        pages: [
          {
            page: 1,
            receipt_index: 2,
            receipt_bbox: { x: 10, y: 20, w: 100, h: 80 },
            ai_enhanced: {
              tsv_rows: [
                {
                  date: '2024-01-02',
                  amount: '8.00',
                  currency: 'HKD',
                  payee: 'Synthetic Cafe',
                  memo: 'fictional crop row',
                  confidence: 0.95,
                },
              ],
            },
          },
        ],
      },
    })
    expect(spreadsheetData).toHaveLength(1)
    const prov = spreadsheetData[0]!.extraction_provenance as {
      receipt_bbox_pixels?: { x: number; y: number; w: number; h: number }
      receipt_index?: number
    }
    expect(prov.receipt_index).toBe(2)
    expect(prov.receipt_bbox_pixels).toEqual({ x: 10, y: 20, w: 100, h: 80 })
  })
})
