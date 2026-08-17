import { describe, expect, it } from 'vitest'
import {
  parseArapCsvToOcrResult,
  parseCsvText,
  csvSampleForMode,
} from './parseArapCsv'

describe('parseCsvText', () => {
  it('parses quoted commas', () => {
    const rows = parseCsvText('a,b\n"x,y",z\n')
    expect(rows).toEqual([
      ['a', 'b'],
      ['x,y', 'z'],
    ])
  })
})

describe('parseArapCsvToOcrResult', () => {
  it('parses AP sample-shaped CSV into tsv_rows', () => {
    const csv =
      'date,due_date,invoice_number,payee,amount,dr_cr,currency,tax_amount,account_code,category,memo,voucher_no\n' +
      '2026-01-10,2026-02-10,INV-1,Acme,1500.00,Dr,HKD,0,,Office,Memo,AP-001\n'
    const { result, rowCount } = parseArapCsvToOcrResult(csv, 'AP', 'ap.csv')
    expect(rowCount).toBe(1)
    const rows = (result as any).extracted_fields.tsv_rows
    expect(rows[0].payee).toBe('Acme')
    expect(rows[0].amount).toBe('1500.00')
    expect(rows[0].transaction_type).toBe('AP')
  })

  it('rejects missing AR headers', () => {
    expect(() => parseArapCsvToOcrResult('date,amount\n2026-01-01,1\n', 'AR', 'ar.csv')).toThrow(
      /Missing: payer/,
    )
  })
})

describe('csvSampleForMode', () => {
  it('maps modules to public samples', () => {
    expect(csvSampleForMode('AP')?.download).toBe('ap-transactions-sample.csv')
    expect(csvSampleForMode('AR')?.download).toBe('ar-transactions-sample.csv')
    expect(csvSampleForMode('BANK')?.download).toBe('bank-statement-sample.csv')
  })
})
