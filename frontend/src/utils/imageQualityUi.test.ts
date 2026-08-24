import { describe, expect, it } from 'vitest'
import { readImageQuality } from './imageQualityUi'

describe('readImageQuality', () => {
  it('returns empty when provenance missing', () => {
    expect(readImageQuality({}).present).toBe(false)
  })

  it('parses AQ audit from extraction_provenance', () => {
    const info = readImageQuality({
      extraction_provenance: {
        image_quality: {
          enabled: true,
          selection: 'enhanced_selected',
          ui_label: 'Auto-enhanced · view original',
          status: 'recoverable',
          ui_state: 'faded_receipt',
          reason: 'Recoverable quality issues detected',
          issues: ['low_contrast', 'blur'],
          score_before: 0.18,
          score_after: 0.21,
          recipe: [{ op: 'lab_clahe' }, { op: 'deskew' }],
          quality_before: { blur_variance: 20, local_contrast: 0.02 },
          quality_after: { blur_variance: 40, local_contrast: 0.03 },
        },
      },
    })
    expect(info.present).toBe(true)
    expect(info.status).toBe('recoverable')
    expect(info.selection).toBe('enhanced_selected')
    expect(info.issues).toEqual(['low_contrast', 'blur'])
    expect(info.recipeOps).toEqual(['lab_clahe', 'deskew'])
    expect(info.scoreAfter).toBe(0.21)
    expect(info.qualityBefore?.blur_variance).toBe(20)
  })

  it('treats enabled:false as absent', () => {
    expect(
      readImageQuality({
        extraction_provenance: { image_quality: { enabled: false, status: 'clear' } },
      }).present,
    ).toBe(false)
  })
})
