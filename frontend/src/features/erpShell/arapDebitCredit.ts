/** Bank-style Debit/Credit sync for AP/AR module rows (amount + dr_cr). */

export type DrCrSide = 'Dr' | 'Cr'

function toNum(v: unknown): number | null {
  if (v === null || v === undefined || String(v).trim() === '') return null
  const n = parseFloat(String(v).replace(/,/g, ''))
  return Number.isNaN(n) ? null : n
}

export function defaultDrCr(txType: string): DrCrSide {
  return String(txType).toUpperCase() === 'AP' ? 'Dr' : 'Cr'
}

export function normalizeDrCr(value: unknown, txType: string): DrCrSide {
  const raw = String(value ?? '').trim()
  if (raw === 'Dr' || raw === 'Cr') return raw
  const titled = raw.charAt(0).toUpperCase() + raw.slice(1).toLowerCase()
  if (titled === 'Dr' || titled === 'Cr') return titled
  return defaultDrCr(txType)
}

export type DebitCreditFields = {
  debit: number | null
  credit: number | null
  amount: number | null
  dr_cr: DrCrSide
}

/** Derive debit/credit display fields from a books/OCR row. */
export function hydrateDebitCredit(
  tx: Record<string, unknown>,
  modeHint?: string,
): DebitCreditFields {
  const txType = String(tx.transaction_type ?? modeHint ?? '').toUpperCase()
  const existingDebit = toNum(tx.debit)
  const existingCredit = toNum(tx.credit)

  if (existingDebit != null && existingDebit !== 0) {
    const mag = Math.abs(existingDebit)
    return { debit: mag, credit: null, amount: mag, dr_cr: 'Dr' }
  }
  if (existingCredit != null && existingCredit !== 0) {
    const mag = Math.abs(existingCredit)
    return { debit: null, credit: mag, amount: mag, dr_cr: 'Cr' }
  }

  const amt = toNum(tx.amount)
  const side = normalizeDrCr(tx.dr_cr, txType)
  if (amt == null) {
    return { debit: null, credit: null, amount: null, dr_cr: side }
  }
  const mag = Math.abs(amt)
  return side === 'Dr'
    ? { debit: mag, credit: null, amount: mag, dr_cr: 'Dr' }
    : { debit: null, credit: mag, amount: mag, dr_cr: 'Cr' }
}

/** Apply an edit to one side; clears the other and syncs amount + dr_cr. */
export function applyDebitCreditSide(
  side: 'debit' | 'credit',
  value: number | null,
): DebitCreditFields {
  if (value == null) {
    return {
      debit: null,
      credit: null,
      amount: null,
      dr_cr: side === 'debit' ? 'Dr' : 'Cr',
    }
  }
  const mag = Math.abs(value)
  if (side === 'debit') {
    return { debit: mag, credit: null, amount: mag, dr_cr: 'Dr' }
  }
  return { debit: null, credit: mag, amount: mag, dr_cr: 'Cr' }
}
