import { bankSourceFileStem } from './bankSourceFile'

const ACCOUNT_TYPES_VALID = ['HKD CURRENT', 'HKD STATEMENT SAVINGS', 'FCY SAVINGS', 'CASH'] as const

const REF_LIKE = /^(NC\d+|HC\d+|[A-Z]{2,3}\d{5,})/i

const TRANSACTION_TYPE_LABELS = new Set([
  '轉帳收入',
  '轉賬收入',
  '轉帳支出',
  '轉賬支出',
  '利息收入',
  '利息支出',
  'CHARGES',
  'CREDIT INTEREST',
  'NET BILPYT',
  'WITHDRAWL',
  'WITHDRAWAL',
  'SAL',
  'ITEM(S)',
  'ITEM(S) AMOUNT',
])

const ACCOUNT_HEADER_HINTS = [
  'SAVINGS',
  'CURRENT',
  'STATEMENT',
  'ACCOUNT',
  'HKD',
  'FCY',
  'CASH',
  'BUSINESS DIRECT',
  'SPRINT',
  '儲蓄',
  '往來',
  '外幣',
  '港元',
  '支票',
]

function isTransactionTypeLabel(raw: string): boolean {
  const text = (raw || '').trim()
  if (!text) return false
  if (TRANSACTION_TYPE_LABELS.has(text)) return true
  if (TRANSACTION_TYPE_LABELS.has(text.toUpperCase())) return true
  return false
}

function looksLikeAccountSectionHeader(raw: string): boolean {
  const text = (raw || '').trim()
  if (!text || isTransactionTypeLabel(text)) return false
  const upper = text.toUpperCase()
  return ACCOUNT_HEADER_HINTS.some(hint => upper.includes(hint) || text.includes(hint))
}

function looksLikeGarbageAccountType(raw: string): boolean {
  const text = (raw || '').trim()
  if (!text) return false
  if (isTransactionTypeLabel(text)) return true
  const upper = text.toUpperCase()
  if (REF_LIKE.test(text)) return true
  if (/^\d/.test(text)) return true
  if (upper.includes('CHEQUE DEPOSIT') || upper.startsWith('CHEQUE DE')) return true
  if (text.length > 35 && !looksLikeAccountSectionHeader(text)) return true
  return false
}

export function normalizeBankAccountType(raw: string): string {
  const text = (raw || '').trim()
  if (!text || looksLikeGarbageAccountType(text)) return ''
  if ((ACCOUNT_TYPES_VALID as readonly string[]).includes(text)) return text

  const upper = text.toUpperCase()
  if (upper.includes('HSBC') && upper.includes('SAVINGS')) return 'HKD STATEMENT SAVINGS'
  if (upper.includes('HSBC') && upper.includes('CURRENT')) return 'HKD CURRENT'
  if (upper.includes('FOREIGN CURRENCY') || text.includes('外幣')) return 'FCY SAVINGS'
  if (upper.startsWith('HKD STATEM') || upper.includes('STATEMENT SAVINGS')) return 'HKD STATEMENT SAVINGS'
  if (upper.startsWith('HKD CURRE') || (upper.startsWith('HKD') && upper.includes('CURRENT'))) return 'HKD CURRENT'
  if (text.includes('儲蓄') || upper.includes('SAVINGS') || upper.includes('STMT')) return 'HKD STATEMENT SAVINGS'
  if (text.includes('往來') || upper.includes('CURRENT') || upper === 'CHQ' || upper === 'CHEQUE') return 'HKD CURRENT'
  if (upper === 'CASH' || upper.startsWith('CASH')) return 'CASH'
  return looksLikeAccountSectionHeader(text) ? text : ''
}

function pickAccountType(row: Record<string, unknown>): string {
  for (const key of ['賬戶類型', '帳戶類型', '账户类型', 'account_type']) {
    const value = row[key]
    if (value !== undefined && value !== null && String(value).trim() !== '') return String(value)
  }
  return ''
}

function pickAccountNumber(row: Record<string, unknown>): string {
  const value = row.account_number
  if (value !== undefined && value !== null && String(value).trim() !== '') return String(value).trim()
  return ''
}

export function coalesceBankAccountTypeRows<T extends Record<string, unknown>>(rows: T[]): T[] {
  let lastLabel = ''
  let lastAccountNumber = ''
  let lastSourceStem = ''
  for (const row of rows) {
    const stem = bankSourceFileStem(String(row.source_file ?? ''))
    if (stem !== lastSourceStem) {
      lastLabel = ''
      lastAccountNumber = ''
      lastSourceStem = stem
    }
    let label = normalizeBankAccountType(pickAccountType(row))
    let accountNumber = pickAccountNumber(row)
    if (label) {
      lastLabel = label
    } else if (lastLabel) {
      label = lastLabel
    }
    if (accountNumber) {
      lastAccountNumber = accountNumber
    } else if (lastAccountNumber) {
      accountNumber = lastAccountNumber
    }
    if (label) {
      row.account_type = label
      row['賬戶類型'] = label
      row['帳戶類型'] = label
    }
    if (accountNumber) {
      row.account_number = accountNumber
    }
  }
  return rows
}

export { ACCOUNT_TYPES_VALID as BANK_ACCOUNT_TYPES_VALID }
