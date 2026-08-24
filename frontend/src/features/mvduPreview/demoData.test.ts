import { describe, expect, it } from 'vitest'
import { applySplitConflict, initialRegions, initialRows } from './demoData'

describe('table-first preview demoData', () => {
  it('keeps a placeholder row for OCR-held conflict candidates', () => {
    const rows = initialRows()
    const conflict = rows.find(r => r.region_id === 'rrg_r3')
    expect(conflict?.review_state).toBe('needs_region_review')
    expect(conflict?.amount).toBeNull()
    expect(conflict?.payee).toBe('')
  })

  it('targeted re-OCR after split only replaces the conflict region', () => {
    const beforeReady = initialRows().filter(r => r.review_state === 'ready').map(r => r.row_id)
    const next = applySplitConflict(initialRegions(), initialRows(), 'rrg_r3')
    const superseded = next.rows.find(r => r.region_id === 'rrg_r3')
    expect(superseded?.review_state).toBe('superseded')
    expect(next.rows.filter(r => r.review_state === 'ready').map(r => r.row_id)).toEqual(
      expect.arrayContaining([...beforeReady, 'row_rrg_r3_a', 'row_rrg_r3_b']),
    )
    expect(next.regions.filter(r => r.status === 'ocr_done').map(r => r.label)).toEqual(
      expect.arrayContaining(['R1', 'R2', 'R3a', 'R3b']),
    )
    // R1/R2 amounts unchanged (targeted, not whole-file)
    expect(next.rows.find(r => r.region_id === 'rrg_r1')?.amount).toBe(328.5)
    expect(next.rows.find(r => r.region_id === 'rrg_r2')?.amount).toBe(86.0)
  })

  it('does not re-split an already superseded conflict region', () => {
    const once = applySplitConflict(initialRegions(), initialRows(), 'rrg_r3')
    const twice = applySplitConflict(once.regions, once.rows, 'rrg_r3')
    expect(twice.rows.filter(r => r.region_id.startsWith('rrg_r3')).length).toBe(
      once.rows.filter(r => r.region_id.startsWith('rrg_r3')).length,
    )
  })
})
