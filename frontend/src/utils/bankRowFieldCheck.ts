/** Detect bank review rows where date/amount/currency look misplaced. */

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/
const COMPACT_DATE_RE = /^(\d{1,2})([A-Za-z]{3})(\d{2,4})$/
const SLASH_DATE_RE = /^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})$/
const CURRENCY_CODE_RE = /^[A-Za-z]{3}$/
const AMOUNT_RE = /^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?$/

const MONTH3: Record<string, number> = {
  JAN: 1,
  FEB: 2,
  MAR: 3,
  APR: 4,
  MAY: 5,
  JUN: 6,
  JUL: 7,
  AUG: 8,
  SEP: 9,
  OCT: 10,
  NOV: 11,
  DEC: 12,
}

const CURRENCY_ALIASES = new Set([
  'HKD',
  'HK$',
  'HK',
  '港元',
  '港幣',
  '港币',
  'USD',
  'US$',
  'CNY',
  'RMB',
  'CNH',
  'EUR',
  'GBP',
  'JPY',
])

const MONTH_FALSE_POSITIVES = new Set([
  'JAN',
  'FEB',
  'MAR',
  'APR',
  'MAY',
  'JUN',
  'JUL',
  'AUG',
  'SEP',
  'OCT',
  'NOV',
  'DEC',
])

export const BANK_ROW_CHECK_MESSAGE = 'Check this row: date or amount looks misplaced'

export type BankRowLike = {
  date?: string | null
  transaction_date?: string | null
  bank_date?: string | null
  日期?: string | null
  currency?: string | null
  幣別?: string | null
  币别?: string | null
  deposit?: number | string | null
  withdrawal?: number | string | null
  balance?: number | string | null
  存入?: number | string | null
  提取?: number | string | null
  原幣結餘?: number | string | null
  received?: number | string | null
  spent?: number | string | null
  description?: string | null
  particulars?: string | null
  備註?: string | null
  [key: string]: unknown
}

function pickStr(row: BankRowLike, keys: string[]): string {
  for (const k of keys) {
    const v = row[k]
    if (v !== undefined && v !== null && String(v).trim() !== '') return String(v).trim()
  }
  return ''
}

export function parseBankReviewDate(raw: string | null | undefined): string {
  const text = String(raw ?? '').trim()
  if (!text) return ''
  if (ISO_DATE_RE.test(text)) {
    const [y, m, d] = text.split('-').map(Number)
    const dt = new Date(Date.UTC(y, m - 1, d))
    if (dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d) {
      return text
    }
    return ''
  }
  const compact = text.replace(/\s+/g, '')
  const cm = COMPACT_DATE_RE.exec(compact)
  if (cm) {
    const day = Number(cm[1])
    const mon = MONTH3[cm[2].toUpperCase()]
    let yr = Number(cm[3])
    if (!mon) return ''
    if (yr < 100) yr += yr < 70 ? 2000 : 1900
    const dt = new Date(Date.UTC(yr, mon - 1, day))
    if (dt.getUTCFullYear() === yr && dt.getUTCMonth() === mon - 1 && dt.getUTCDate() === day) {
      return `${yr}-${String(mon).padStart(2, '0')}-${String(day).padStart(2, '0')}`
    }
    return ''
  }
  const sm = SLASH_DATE_RE.exec(compact)
  if (sm) {
    const day = Number(sm[1])
    const mon = Number(sm[2])
    let yr = Number(sm[3])
    if (yr < 100) yr += yr < 70 ? 2000 : 1900
    const dt = new Date(Date.UTC(yr, mon - 1, day))
    if (dt.getUTCFullYear() === yr && dt.getUTCMonth() === mon - 1 && dt.getUTCDate() === day) {
      return `${yr}-${String(mon).padStart(2, '0')}-${String(day).padStart(2, '0')}`
    }
  }
  return ''
}

export function isValidBankCurrencyCode(raw: string | null | undefined): boolean {
  const text = String(raw ?? '').trim()
  if (!text) return false
  if (CURRENCY_ALIASES.has(text) || CURRENCY_ALIASES.has(text.toUpperCase())) return true
  if (!CURRENCY_CODE_RE.test(text)) return false
  return !MONTH_FALSE_POSITIVES.has(text.toUpperCase())
}

export function isValidBankAmountToken(raw: unknown): boolean {
  if (raw === null || raw === undefined) return true
  const text = String(raw).trim()
  if (!text || text === '-' || text === '—' || text === '--') return true
  if (typeof raw === 'number') return Number.isFinite(raw)
  const cleaned = text.replace(/,/g, '').replace(/\$/g, '').trim()
  if (!AMOUNT_RE.test(text) && !/^-?\d+(?:\.\d{1,2})?$/.test(cleaned)) {
    if (/[A-Za-z\u4e00-\u9fff]/.test(cleaned)) return false
  }
  const n = Number(cleaned)
  return Number.isFinite(n)
}

/** Issue codes matching backend `bank_row_placement_issues`. */
export function bankRowPlacementIssues(row: BankRowLike): string[] {
  const desc = pickStr(row, ['備註', 'description', 'particulars'])
  if (desc === '無交易') return []

  const issues: string[] = []
  const dateRaw = pickStr(row, ['date', 'transaction_date', '日期', 'bank_date'])
  const dateIso = parseBankReviewDate(dateRaw)
  if (!dateRaw || !dateIso) {
    issues.push('bank_date_missing_or_unparseable')
  }

  const curRaw = pickStr(row, ['currency', '幣別', '币别'])
  if (curRaw && !isValidBankCurrencyCode(curRaw)) {
    issues.push('bank_currency_misplaced')
  }

  const amountKeys = [
    'deposit',
    '存入',
    'received',
    'withdrawal',
    '提取',
    'spent',
    'balance',
    '原幣結餘',
  ] as const
  for (const key of amountKeys) {
    if (!(key in row)) continue
    const raw = row[key]
    if (raw === null || raw === undefined) continue
    const text = String(raw).trim()
    if (!text || text === '-' || text === '—' || text === '--') continue
    if (!isValidBankAmountToken(raw)) {
      if (!issues.includes('bank_amount_misplaced')) issues.push('bank_amount_misplaced')
      break
    }
  }

  return issues
}

export function bankRowNeedsPlacementCheck(row: BankRowLike): boolean {
  return bankRowPlacementIssues(row).length > 0
}

export function countRowsNeedingPlacementCheck(rows: BankRowLike[]): number {
  return rows.reduce((n, r) => n + (bankRowNeedsPlacementCheck(r) ? 1 : 0), 0)
}

export function placementCheckBannerText(count: number): string {
  if (count <= 0) return ''
  return count === 1 ? '1 row needs checking' : `${count} rows need checking`
}
