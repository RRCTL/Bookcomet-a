/** Client-side AP/AR CSV import (skips VLM). Headers must match the public sample files. */

export type ArapCsvModule = 'AP' | 'AR'

export const AP_CSV_REQUIRED_HEADERS = ['date', 'payee', 'amount'] as const
export const AR_CSV_REQUIRED_HEADERS = ['date', 'payer', 'amount'] as const

const OPTIONAL_HEADERS = [
  'due_date',
  'invoice_number',
  'vendor_tax_id',
  'tax_amount',
  'payment_status',
  'dr_cr',
  'currency',
  'bank',
  'account_code',
  'category',
  'memo',
  'voucher_no',
  'transaction_type',
] as const

function stripBom(text: string): string {
  return text.charCodeAt(0) === 0xfeff ? text.slice(1) : text
}

/** Minimal RFC4180 CSV split (quotes, commas, CRLF). */
export function parseCsvText(text: string): string[][] {
  const input = stripBom(text)
  const rows: string[][] = []
  let row: string[] = []
  let cell = ''
  let i = 0
  let inQuotes = false
  while (i < input.length) {
    const ch = input[i]
    if (inQuotes) {
      if (ch === '"') {
        if (input[i + 1] === '"') {
          cell += '"'
          i += 2
          continue
        }
        inQuotes = false
        i += 1
        continue
      }
      cell += ch
      i += 1
      continue
    }
    if (ch === '"') {
      inQuotes = true
      i += 1
      continue
    }
    if (ch === ',') {
      row.push(cell)
      cell = ''
      i += 1
      continue
    }
    if (ch === '\r') {
      i += 1
      continue
    }
    if (ch === '\n') {
      row.push(cell)
      rows.push(row)
      row = []
      cell = ''
      i += 1
      continue
    }
    cell += ch
    i += 1
  }
  if (cell.length > 0 || row.length > 0) {
    row.push(cell)
    rows.push(row)
  }
  return rows.filter(r => r.some(c => String(c).trim() !== ''))
}

function normalizeHeader(h: string): string {
  return String(h || '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '_')
}

function cellAt(row: Record<string, string>, keys: string[]): string {
  for (const k of keys) {
    const v = row[k]
    if (v !== undefined && String(v).trim() !== '') return String(v).trim()
  }
  return ''
}

export function csvSampleForMode(mode: string): { href: string; download: string } | null {
  const m = String(mode || '').toUpperCase()
  if (m === 'BANK' || m === 'RECON') {
    return { href: '/bank-statement-sample.csv', download: 'bank-statement-sample.csv' }
  }
  if (m === 'AP') {
    return { href: '/ap-transactions-sample.csv', download: 'ap-transactions-sample.csv' }
  }
  if (m === 'AR') {
    return { href: '/ar-transactions-sample.csv', download: 'ar-transactions-sample.csv' }
  }
  return null
}

export function parseArapCsvToOcrResult(
  text: string,
  module: ArapCsvModule,
  fileName: string,
): { result: Record<string, unknown>; rowCount: number } {
  const table = parseCsvText(text)
  if (table.length < 2) {
    throw new Error('CSV has no data rows. Download the sample template and try again.')
  }
  const headers = table[0].map(normalizeHeader)
  const required = module === 'AP' ? AP_CSV_REQUIRED_HEADERS : AR_CSV_REQUIRED_HEADERS
  const missing = required.filter(h => !headers.includes(h))
  if (missing.length > 0) {
    throw new Error(
      `CSV headers do not match the ${module} template. Missing: ${missing.join(', ')}. ` +
        `Required: ${required.join(', ')}.`,
    )
  }

  const tsv_rows: Record<string, string>[] = []
  for (let r = 1; r < table.length; r++) {
    const cells = table[r]
    const row: Record<string, string> = {}
    headers.forEach((h, idx) => {
      if (!h) return
      row[h] = String(cells[idx] ?? '').trim()
    })
    const amount = cellAt(row, ['amount'])
    const date = cellAt(row, ['date'])
    if (!date && !amount) continue

    const voucher =
      cellAt(row, ['voucher_no', 'id_number', 'reference']) ||
      `${module}-${date || 'row'}-${String(r).padStart(3, '0')}`

    const category =
      cellAt(row, ['category', 'categorise', 'account_category']) ||
      cellAt(row, ['account_code'])

    const out: Record<string, string> = {
      voucher_no: voucher,
      transaction_type: module,
      date,
      amount,
      currency: cellAt(row, ['currency']) || 'HKD',
      payer: cellAt(row, ['payer']),
      payee: cellAt(row, ['payee']),
      bank: cellAt(row, ['bank']),
      category,
      memo: cellAt(row, ['memo', 'description']),
      confidence: '100',
      due_date: cellAt(row, ['due_date']),
      invoice_number: cellAt(row, ['invoice_number', 'invoice_no']),
      vendor_tax_id: cellAt(row, ['vendor_tax_id', 'tax_id']),
      tax_amount: cellAt(row, ['tax_amount']),
      payment_status: cellAt(row, ['payment_status']),
      dr_cr: cellAt(row, ['dr_cr', 'debit_credit']),
      account_code: cellAt(row, ['account_code']),
    }
    // Keep optional keys even when empty so AP table schema columns exist.
    for (const opt of OPTIONAL_HEADERS) {
      if (out[opt] === undefined) out[opt] = ''
    }
    tsv_rows.push(out)
  }

  if (tsv_rows.length === 0) {
    throw new Error('CSV has no usable transaction rows.')
  }

  const fields = {
    tsv_rows,
    confidence: 1,
    analysis_summary: `CSV import from ${fileName} (${tsv_rows.length} rows, no VLM)`,
  }
  return {
    rowCount: tsv_rows.length,
    result: {
      extracted_fields: fields,
      ai_enhanced: fields,
      total_pages: 1,
      csv_import: true,
      source_file: fileName,
    },
  }
}
