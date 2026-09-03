import { describe, expect, it } from 'vitest'
import { buildSpreadsheetRowsFromOcrResult } from './buildSpreadsheetFromOcrResult'

describe('buildSpreadsheetRowsFromOcrResult source page', () => {
  it('uses each tsv row _page when pages[] is flattened into one blob', () => {
    const { spreadsheetData } = buildSpreadsheetRowsFromOcrResult({
      fileId: 'file-a',
      fileName: 'SAMPLE-2403.pdf',
      processingMode: 'AP',
      rowIndexStart: 1,
      result: {
        ai_enhanced: {
          tsv_rows: [
            { amount: '100', payee: 'Acme Supplies Ltd', _page: 1 },
            { amount: '200', payee: 'Sample Office Ltd', _page: 2 },
            { amount: '300', payee: 'Example Customer Ltd', _page: 3 },
          ],
        },
      },
    })
    expect(spreadsheetData.map(r => r.file_position)).toEqual([
      'SAMPLE-2403.pdf P1',
      'SAMPLE-2403.pdf P2',
      'SAMPLE-2403.pdf P3',
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

  it('maps VLM_CROP_TIMEOUT without tsv_rows to OCR timeout memo', () => {
    const { spreadsheetData } = buildSpreadsheetRowsFromOcrResult({
      fileId: 'file-a',
      fileName: 'synthetic.pdf',
      processingMode: 'AP',
      rowIndexStart: 1,
      result: {
        document_type: 'multi_page_pdf',
        pages: [
          {
            page: 6,
            receipt_index: 2,
            status: 'error',
            error_code: 'VLM_CROP_TIMEOUT',
            error_detail: 'Crop OCR exceeded the configured per-crop deadline',
          },
        ],
      },
    })
    expect(spreadsheetData).toHaveLength(1)
    expect(String(spreadsheetData[0]?.memo)).toMatch(/^\[OCR timeout\]/)
  })

  it('uses timeout stub tsv_rows instead of the error placeholder', () => {
    const { spreadsheetData } = buildSpreadsheetRowsFromOcrResult({
      fileId: 'file-a',
      fileName: 'synthetic.pdf',
      processingMode: 'AP',
      rowIndexStart: 1,
      result: {
        document_type: 'multi_page_pdf',
        pages: [
          {
            page: 6,
            receipt_index: 2,
            status: 'error',
            error_code: 'VLM_CROP_TIMEOUT',
            ai_enhanced: {
              tsv_rows: [{ voucher_no: 'P6-R2', amount: '', memo: '[OCR timeout]' }],
            },
          },
        ],
      },
    })
    expect(spreadsheetData).toHaveLength(1)
    expect(spreadsheetData[0]?.voucher_no).toBe('P6-R2')
    expect(spreadsheetData[0]?.memo).toBe('[OCR timeout]')
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
