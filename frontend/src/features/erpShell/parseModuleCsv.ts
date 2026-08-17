/**
 * Parse AP / AR / Bank CSV for Books module Import.
 * Accepts sample-file English headers and Export CSV display headers.
 */
import { parseCsvText } from '../workspace/parseArapCsv'
import { defaultDrCr, hydrateDebitCredit } from './arapDebitCredit'

export type ModuleCsvMode = 'AP' | 'AR' | 'BANK'

function normHeader(h: string): string {
  return String(h || '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '_')
    .replace(/\./g, '')
}

/** Map normalized header → canonical field. */
const ALIASES: Record<string, string> = {
  id_no: 'id_number',
  id_number: 'id_number',
  voucher_no: 'id_number',
  reference: 'id_number',
  invoice_no: 'invoice_number',
  invoice_number: 'invoice_number',
  date: 'date',
  due_date: 'due_date',
  supplier: 'payee',
  payee: 'payee',
  payer: 'payer',
  debit: 'debit',
  credit: 'credit',
  tax: 'tax_amount',
  tax_amount: 'tax_amount',
  cur: 'currency',
  currency: 'currency',
  account: 'account_code',
  account_code: 'account_code',
  gl_code: 'account_code',
  category: 'category',
  payment: 'payment_status',
  payment_status: 'payment_status',
  memo: 'memo',
  bank: 'bank',
  amount: 'amount',
  dr_cr: 'dr_cr',
  bank_account: 'account_type',
  account_type: 'account_type',
  description: 'particulars',
  particulars: 'particulars',
  withdrawal: 'withdrawal',
  deposit: 'deposit',
  balance: 'balance',
}

function parseNum(s: string): number | null {
  if (!s || String(s).trim() === '') return null
  const n = parseFloat(String(s).replace(/,/g, ''))
  return Number.isNaN(n) ? null : n
}

function cell(row: Record<string, string>, field: string): string {
  return (row[field] ?? '').trim()
}

function rowToMap(headers: string[], cells: string[]): Record<string, string> {
  const out: Record<string, string> = {}
  headers.forEach((h, i) => {
    const canon = ALIASES[normHeader(h)]
    if (!canon) return
    const v = String(cells[i] ?? '').trim()
    if (v !== '' || out[canon] === undefined) out[canon] = v
  })
  return out
}

function buildArapTx(mode: 'AP' | 'AR', row: Record<string, string>): Record<string, unknown> | null {
  const date = cell(row, 'date')
  const debit = parseNum(cell(row, 'debit'))
  const credit = parseNum(cell(row, 'credit'))
  let amount = parseNum(cell(row, 'amount'))
  if (amount == null) {
    if (debit != null) amount = debit
    else if (credit != null) amount = credit
  }
  if (!date && amount == null && !cell(row, 'payee') && !cell(row, 'payer')) return null

  const sides = hydrateDebitCredit(
    {
      amount,
      debit,
      credit,
      dr_cr: cell(row, 'dr_cr') || defaultDrCr(mode),
      transaction_type: mode,
    },
    mode,
  )

  return {
    id_number: cell(row, 'id_number'),
    invoice_number: cell(row, 'invoice_number'),
    date: date || new Date().toISOString().slice(0, 10),
    due_date: cell(row, 'due_date'),
    transaction_type: mode,
    amount: sides.amount,
    debit: sides.debit,
    credit: sides.credit,
    dr_cr: sides.dr_cr,
    currency: cell(row, 'currency') || 'HKD',
    payer: cell(row, 'payer'),
    payee: cell(row, 'payee'),
    bank: cell(row, 'bank'),
    account_code: cell(row, 'account_code'),
    category: cell(row, 'category'),
    memo: cell(row, 'memo'),
    tax_amount: parseNum(cell(row, 'tax_amount')),
    payment_status: cell(row, 'payment_status'),
    source_file: '',
    manual_entry: true,
  }
}

function buildBankTx(row: Record<string, string>): Record<string, unknown> | null {
  const date = cell(row, 'date')
  const particulars = cell(row, 'particulars')
  let deposit = parseNum(cell(row, 'deposit'))
  let withdrawal = parseNum(cell(row, 'withdrawal'))
  const amount = parseNum(cell(row, 'amount'))
  if (deposit == null && withdrawal == null && amount != null) {
    if (amount >= 0) deposit = amount
    else withdrawal = Math.abs(amount)
  }
  if (!date && !particulars && deposit == null && withdrawal == null) return null

  const accountType = cell(row, 'account_type')
  return {
    id_number: cell(row, 'id_number'),
    date: date || new Date().toISOString().slice(0, 10),
    account_type: accountType,
    賬戶類型: accountType,
    帳戶類型: accountType,
    账户类型: accountType,
    particulars,
    description: particulars,
    deposit,
    withdrawal,
    balance: parseNum(cell(row, 'balance')) ?? undefined,
    currency: cell(row, 'currency') || 'HKD',
    account_code: cell(row, 'account_code'),
    category: cell(row, 'category'),
    source_file: '',
    manual_entry: true,
  }
}

export function parseModuleCsvTransactions(
  text: string,
  mode: ModuleCsvMode,
): Record<string, unknown>[] {
  const table = parseCsvText(text)
  if (table.length < 2) {
    throw new Error('CSV has no data rows. Download the sample template and try again.')
  }
  const headers = table[0]
  const canonHeaders = headers.map(h => ALIASES[normHeader(h)]).filter(Boolean)
  if (mode === 'BANK') {
    const ok =
      canonHeaders.includes('date') &&
      (canonHeaders.includes('particulars') ||
        canonHeaders.includes('amount') ||
        canonHeaders.includes('deposit') ||
        canonHeaders.includes('withdrawal'))
    if (!ok) {
      throw new Error(
        'CSV headers do not match the Bank template. Need Date plus Description/Amount (or Deposit/Withdrawal).',
      )
    }
  } else if (mode === 'AP') {
    const hasAmt =
      canonHeaders.includes('amount') ||
      canonHeaders.includes('debit') ||
      canonHeaders.includes('credit')
    if (!canonHeaders.includes('date') || !canonHeaders.includes('payee') || !hasAmt) {
      throw new Error(
        'CSV headers do not match the AP template. Required: date, payee, amount (or Debit/Credit).',
      )
    }
  } else {
    const hasAmt =
      canonHeaders.includes('amount') ||
      canonHeaders.includes('debit') ||
      canonHeaders.includes('credit')
    if (!canonHeaders.includes('date') || !canonHeaders.includes('payer') || !hasAmt) {
      throw new Error(
        'CSV headers do not match the AR template. Required: date, payer, amount (or Debit/Credit).',
      )
    }
  }

  const txs: Record<string, unknown>[] = []
  for (let i = 1; i < table.length; i++) {
    const map = rowToMap(headers, table[i])
    const tx =
      mode === 'BANK' ? buildBankTx(map) : buildArapTx(mode, map)
    if (tx) txs.push(tx)
  }
  if (txs.length === 0) {
    throw new Error('CSV has no usable transaction rows.')
  }
  return txs
}
