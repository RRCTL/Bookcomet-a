/** Company vs receipt FX helpers. Empty receipt currency stays empty (do not invent HKD). */

export const FX_CURRENCY_OPTIONS = ['HKD', 'USD', 'CNY', 'EUR', 'GBP', 'JPY'] as const

const HKD_ALIASES = new Set([
  'HKD',
  'HK$',
  'HK',
  'HONG KONG DOLLAR',
  'HONGKONG DOLLAR',
  'HONG KONG DOLLARS',
  '港元',
  '港幣',
  '港币',
])

const CODE_ALIASES: Record<string, string> = {
  JYP: 'JPY',
  YEN: 'JPY',
  RMB: 'CNY',
  CNH: 'CNY',
  人民幣: 'CNY',
  人民币: 'CNY',
  日元: 'JPY',
  US$: 'USD',
  USD$: 'USD',
}

export type FxRowLike = {
  currency?: string | null
  amount?: number | string | null
  tax_amount?: number | string | null
  company_currency?: string | null
  company_amount?: number | string | null
  company_tax_amount?: number | string | null
  exchange_rate?: number | string | null
  printed_company_amount?: number | string | null
}

export function normalizeCurrencyCode(raw: string | null | undefined): string {
  const c = String(raw ?? '').trim()
  if (!c) return ''
  if (HKD_ALIASES.has(c) || HKD_ALIASES.has(c.toUpperCase())) return 'HKD'
  const upper = c.toUpperCase()
  return CODE_ALIASES[upper] || CODE_ALIASES[c] || upper
}

export function parseFxNumber(raw: unknown): number | null {
  if (raw == null || raw === '') return null
  const n = typeof raw === 'number' ? raw : parseFloat(String(raw).replace(/,/g, ''))
  return Number.isFinite(n) ? n : null
}

export function quantizeRate(rate: number): number {
  return Math.round(rate * 10000) / 10000
}

export function quantizeMoney(amount: number): number {
  return Math.round(amount * 100) / 100
}

export function convertReceiptToCompany(receiptAmount: number, rate: number): number {
  return quantizeMoney(receiptAmount * quantizeRate(rate))
}

export function impliedRateFromPrinted(receiptAmount: number, companyAmount: number): number | null {
  if (!receiptAmount) return null
  return quantizeRate(companyAmount / receiptAmount)
}

export function fxPairKey(from: string, to: string): string {
  return `${normalizeCurrencyCode(from)}:${normalizeCurrencyCode(to)}`
}

export function parseFxPairKey(key: string): { from: string; to: string } | null {
  const [from, to] = String(key || '').split(':')
  if (!from || !to) return null
  return { from: normalizeCurrencyCode(from), to: normalizeCurrencyCode(to) }
}

export function formatMoneyAmount(amount: number): string {
  return amount.toLocaleString('en-HK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function formatCurrencyAmount(code: string, amount: number | null | undefined): string {
  const ccy = normalizeCurrencyCode(code)
  if (amount == null || !Number.isFinite(amount)) return ccy
  return ccy ? `${ccy} ${formatMoneyAmount(amount)}` : formatMoneyAmount(amount)
}

export function sameCurrency(receipt: string | null | undefined, company: string | null | undefined): boolean {
  const a = normalizeCurrencyCode(receipt)
  const b = normalizeCurrencyCode(company)
  return Boolean(a && b && a === b)
}

export function rowReadyForBooks(row: FxRowLike, companyCurrency?: string | null): boolean {
  const receipt = normalizeCurrencyCode(row.currency)
  const company = normalizeCurrencyCode(row.company_currency || companyCurrency)
  const receiptAmt = parseFxNumber(row.amount)
  if (receiptAmt == null) return true
  if (sameCurrency(receipt, company)) return true
  const companyAmt = parseFxNumber(row.company_amount)
  return Boolean(company && companyAmt != null)
}

export function applySavedRateToRow<T extends FxRowLike>(
  row: T,
  companyCurrency: string,
  rate: number | null,
  opts?: { keepPrinted?: boolean },
): T {
  const receipt = normalizeCurrencyCode(row.currency)
  const company = normalizeCurrencyCode(companyCurrency)
  const receiptAmt = parseFxNumber(row.amount)
  const taxAmt = parseFxNumber(row.tax_amount)
  if (!company) return row
  if (sameCurrency(receipt, company)) {
    return {
      ...row,
      company_currency: company,
      company_amount: receiptAmt,
      company_tax_amount: taxAmt,
      exchange_rate: 1,
    }
  }
  const printed = parseFxNumber(row.printed_company_amount)
  if (opts?.keepPrinted !== false && printed != null && receiptAmt != null) {
    return {
      ...row,
      company_currency: company,
      company_amount: quantizeMoney(printed),
      company_tax_amount: taxAmt != null && receiptAmt ? convertReceiptToCompany(taxAmt, impliedRateFromPrinted(receiptAmt, printed) || 0) : taxAmt,
      exchange_rate: impliedRateFromPrinted(receiptAmt, printed),
    }
  }
  if (rate == null || receiptAmt == null) {
    return { ...row, company_currency: company }
  }
  const qRate = quantizeRate(rate)
  return {
    ...row,
    company_currency: company,
    company_amount: convertReceiptToCompany(receiptAmt, qRate),
    company_tax_amount: taxAmt != null ? convertReceiptToCompany(taxAmt, qRate) : null,
    exchange_rate: qRate,
  }
}

export function lookupFxRate(fxRates: Record<string, unknown> | null | undefined, from: string, to: string): number | null {
  if (!fxRates) return null
  const key = fxPairKey(from, to)
  const raw = fxRates[key]
  const n = parseFxNumber(raw)
  return n != null && n > 0 ? quantizeRate(n) : null
}

export function reportingCurrencyFromSettings(custom: Record<string, unknown> | null | undefined): string {
  if (!custom || typeof custom !== 'object') return ''
  const raw = custom.reporting_currency ?? custom.currency
  return typeof raw === 'string' ? normalizeCurrencyCode(raw) : ''
}

export function fxRatesFromSettings(custom: Record<string, unknown> | null | undefined): Record<string, number> {
  const raw = custom?.fx_rates
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
  const out: Record<string, number> = {}
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    const n = parseFxNumber(value)
    if (n != null && n > 0) out[key] = quantizeRate(n)
  }
  return out
}

export function receiptAmountFromBankRow(row: Record<string, unknown>): number | null {
  const dep = parseFxNumber(row.deposit)
  const wit = parseFxNumber(row.withdrawal)
  if (dep != null && dep !== 0) return Math.abs(dep)
  if (wit != null && wit !== 0) return Math.abs(wit)
  return parseFxNumber(row.amount)
}

export function hydrateRowsForCompany<T extends FxRowLike>(
  rows: T[],
  companyCurrency: string,
  fxRates: Record<string, number>,
): T[] {
  const company = normalizeCurrencyCode(companyCurrency)
  if (!company) return rows
  return rows.map(row => {
    const receipt = normalizeCurrencyCode(row.currency)
    const amount = parseFxNumber(row.amount) ?? receiptAmountFromBankRow(row as Record<string, unknown>)
    const working = { ...row, amount: amount ?? row.amount }
    if (sameCurrency(receipt, company)) {
      return applySavedRateToRow(working, company, 1)
    }
    const printed = parseFxNumber(row.printed_company_amount)
    if (printed != null) {
      return applySavedRateToRow(working, company, null, { keepPrinted: true })
    }
    if (parseFxNumber(row.company_amount) != null && normalizeCurrencyCode(row.company_currency) === company) {
      return { ...row, company_currency: company }
    }
    const rate = lookupFxRate(fxRates, receipt, company)
    if (rate == null) return { ...row, company_currency: company }
    return applySavedRateToRow(working, company, rate, { keepPrinted: false })
  })
}

export function companyAmountForMatch(row: {
  company_amount?: number | string | null
  company_currency?: string | null
  amount?: number | string | null
  currency?: string | null
}): { currency: string; amount: number | null } {
  const companyAmt = parseFxNumber(row.company_amount)
  const companyCcy = normalizeCurrencyCode(row.company_currency)
  if (companyAmt != null && companyCcy) return { currency: companyCcy, amount: companyAmt }
  const receiptAmt = parseFxNumber(row.amount)
  const receiptCcy = normalizeCurrencyCode(row.currency)
  return { currency: receiptCcy, amount: receiptAmt }
}
