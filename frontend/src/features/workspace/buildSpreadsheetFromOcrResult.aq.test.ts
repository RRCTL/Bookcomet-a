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
})
