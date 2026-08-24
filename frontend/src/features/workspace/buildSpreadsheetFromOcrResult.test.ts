import { describe, expect, it } from 'vitest'
import { buildSpreadsheetRowsFromOcrResult } from './buildSpreadsheetFromOcrResult'

describe('buildSpreadsheetRowsFromOcrResult source page', () => {
  it('uses each tsv row _page when pages[] is flattened into one blob', () => {
    const { spreadsheetData } = buildSpreadsheetRowsFromOcrResult({
      fileId: 'file-a',
      fileName: '2024支出 (5).pdf',
      processingMode: 'AP',
      rowIndexStart: 1,
      result: {
        ai_enhanced: {
          tsv_rows: [
            { amount: '100', payee: 'Taxi A', _page: 1 },
            { amount: '200', payee: 'Taxi B', _page: 2 },
            { amount: '300', payee: 'Taxi C', _page: 3 },
          ],
        },
      },
    })
    expect(spreadsheetData.map(r => r.file_position)).toEqual([
      '2024支出 (5).pdf P1',
      '2024支出 (5).pdf P2',
      '2024支出 (5).pdf P3',
    ])
  })

  it('does not stamp every flattened row as P1', () => {
    const { spreadsheetData } = buildSpreadsheetRowsFromOcrResult({
      fileId: 'file-a',
      fileName: 'receipts.pdf',
      processingMode: 'AP',
      rowIndexStart: 1,
      result: {
        ai_enhanced: {
          tsv_rows: [
            { amount: '10', _page: 2 },
            { amount: '20', _page: 3 },
          ],
        },
      },
    })
    expect(spreadsheetData.every(r => r.file_position === 'receipts.pdf P1')).toBe(false)
    expect(spreadsheetData[0]?.file_position).toBe('receipts.pdf P2')
    expect(spreadsheetData[1]?.file_position).toBe('receipts.pdf P3')
  })

  it('keeps per-page labels from multi_page_pdf pages[]', () => {
    const { spreadsheetData } = buildSpreadsheetRowsFromOcrResult({
      fileId: 'file-a',
      fileName: 'pack.pdf',
      processingMode: 'AP',
      rowIndexStart: 1,
      result: {
        document_type: 'multi_page_pdf',
        pages: [
          { page: 1, ai_enhanced: { tsv_rows: [{ amount: '1' }] } },
          { page: 3, ai_enhanced: { tsv_rows: [{ amount: '3' }] } },
        ],
      },
    })
    expect(spreadsheetData.map(r => r.file_position)).toEqual(['pack.pdf P1', 'pack.pdf P3'])
  })
})
