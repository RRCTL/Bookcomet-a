import type { ARAPTransaction } from '../components/ARAPReview'
import type { BankTransaction } from '../components/BankStatementReview'
import type { ChartOfAccountItem } from '../types/reconciliation'
import type { GlJournalPayload } from '../services/reconciliation'
import type { GlSupersededDraft } from '../utils/mergeGlJournalsForReport'

// ─── Exported types ───────────────────────────────────────────────────────────

export type TxnGlMeta = {
  journalId: string
  voucherNo: string
  lineId: string
  status: 'draft' | 'posted'
}

export type TxnRecord = {
  id: string
  date: string
  amount: number
  description: string
  isUnmatched: boolean      // kept for backward compat — true when isNoCode || isReconUnmatched
  isNoCode: boolean         // transaction has no account_code assigned
  isReconUnmatched: boolean // transaction went through RECON but had no counterpart
  /** RECON journal line (when TB movement comes from GL). */
  gl?: TxnGlMeta
}

export type TrialBalanceRow = {
  code: string
  name_en: string
  name_zh: string
  category_type: string
  openDr: number
  openCr: number
  movDr: number
  movCr: number
  closeDr: number
  closeCr: number
  transactions: TxnRecord[]
  unmatchedCount: number
  isSuspense?: boolean
}

export type IncomeRow = {
  code: string
  name_en: string
  name_zh: string
  category_type: 'Income' | 'Expense'
  amount: number
  transactions: TxnRecord[]
  unmatchedCount: number
  isSuspense?: boolean
}

export type BalanceRow = {
  code: string
  name_en: string
  name_zh: string
  category_type: 'Asset' | 'Liability' | 'Equity'
  /** Signed net vs TB close columns: Assets & suspense = closeDr − closeCr (+ = net debit). Liability & Equity = closeCr − closeDr (+ = net credit). */
  amount: number
  transactions: TxnRecord[]
  unmatchedCount: number
  isSuspense?: boolean
}

export type ReportSummary = {
  totalMovDr: number
  totalMovCr: number
  totalIncome: number
  totalExpense: number
  netIncome: number
  /** Operating assets only (excludes suspense). Use for the non-suspense BS section subtotal. */
  totalOperatingAssets: number
  /** Signed net suspense (closeDr − closeCr), included in totalAsset. */
  totalSuspenseBalance: number
  /** Sum of signed asset nets incl. suspense; matches L + E + NI when TB balances. */
  totalAsset: number
  /** Sum of signed liability nets (closeCr − closeDr; negative = net debit / abnormal). */
  totalLiability: number
  /** Sum of signed equity nets (closeCr − closeDr). */
  totalEquity: number
  balanced: boolean
  balanceSheetBalanced: boolean
  suspenseNoCodeCount: number         // transactions in suspense because no account_code
  suspenseReconUnmatchedCount: number // transactions in suspense because RECON-unmatched only
  suspenseBothCount: number           // transactions in suspense because both conditions
  suspenseTotalDr: number
  suspenseTotalCr: number
}

export type FinancialReportData = {
  generatedAt: string
  dateFrom: string
  dateTo: string
  suspenseCode: string
  trialBalanceRows: TrialBalanceRow[]
  incomeRows: IncomeRow[]
  balanceRows: BalanceRow[]
  summary: ReportSummary
  /** When set, trial balance movements include RECON GL journals (+ optional OCR supplement). */
  glProvenance?: {
    source: 'gl' | 'gl+ocr' | 'ocr'
    includesDraftJournals: boolean
    activeVoucherNos: string[]
    supersededDrafts: GlSupersededDraft[]
  }
}

export type ReportOptions = {
  dateFrom: string
  dateTo: string
  suspenseCode: string
  arControlCode: string  // offsetting account for AR transactions (e.g. "1100 Accounts Receivable")
  apControlCode: string  // offsetting account for AP transactions (e.g. "2100 Accounts Payable")
  bankCode: string       // offsetting account for bank movements  (e.g. "1000 Bank / Cash")
}

// Internal sentinel key — never exposed as a real CoA account code
const SUSPENSE_KEY = '__SUSPENSE__'

// ─── Internal helpers ─────────────────────────────────────────────────────────

/**
 * Returns 'Dr' for debit-normal accounts (Asset, Expense),
 * 'Cr' for credit-normal accounts (Income, Liability, Equity).
 */
function getNormalBalance(categoryType: string): 'Dr' | 'Cr' {
  const lower = (categoryType ?? '').toLowerCase()
  if (
    lower.includes('asset') || lower.includes('expense') ||
    lower.includes('資產') || lower.includes('費用')
  ) {
    return 'Dr'
  }
  return 'Cr'
}

/**
 * Resolves the CoA category type to one of 5 canonical values.
 */
function resolveCategory(categoryType: string): 'Asset' | 'Liability' | 'Equity' | 'Income' | 'Expense' {
  const lower = (categoryType ?? '').toLowerCase()
  if (lower.includes('asset') || lower.includes('資產')) return 'Asset'
  if (lower.includes('liability') || lower.includes('負債')) return 'Liability'
  if (lower.includes('equity') || lower.includes('股東') || lower.includes('權益')) return 'Equity'
  if (lower.includes('income') || lower.includes('revenue') || lower.includes('收入')) return 'Income'
  if (lower.includes('expense') || lower.includes('費用')) return 'Expense'
  return 'Asset'
}

export type MovementGroup = { dr: number; cr: number; txns: TxnRecord[] }

function materializeFinancialReport(
  accountGroups: Map<string, MovementGroup>,
  coaList: ChartOfAccountItem[],
  suspenseCode: string,
  dateFrom: string,
  dateTo: string,
  controlCategoryHints: Record<string, string>,
): FinancialReportData {
  const coaMap = new Map(coaList.map(c => [c.code, c]))

  const trialBalanceRows: TrialBalanceRow[] = []
  const incomeRows: IncomeRow[] = []
  const balanceRows: BalanceRow[] = []

  for (const [rawCode, group] of accountGroups.entries()) {
    const isSuspenseRow = rawCode === SUSPENSE_KEY

    const code = isSuspenseRow ? (suspenseCode || '9999') : rawCode
    const coa = isSuspenseRow ? undefined : coaMap.get(rawCode)
    const name_zh = isSuspenseRow
      ? 'Suspense account (uncoded / unmatched transactions)'
      : (coa?.name_zh ?? rawCode)
    const name_en = isSuspenseRow
      ? 'Suspense Account (Uncoded / Unmatched Transactions)'
      : (coa?.name_en ?? rawCode)

    const category_type = isSuspenseRow
      ? 'Asset'
      : (coa?.category_type ?? controlCategoryHints[rawCode] ?? 'Asset')

    const resolved = resolveCategory(category_type)
    const normalBalance = getNormalBalance(category_type)

    const openBal = coa?.opening_balance ?? 0
    const openDrCr = coa?.opening_balance_dr_cr ?? (normalBalance === 'Dr' ? 'Dr' : 'Cr')
    const openDr = openDrCr === 'Dr' ? openBal : 0
    const openCr = openDrCr === 'Cr' ? openBal : 0

    const movDr = group.dr
    const movCr = group.cr

    const totalDr = openDr + movDr
    const totalCr = openCr + movCr
    const closeDr = totalDr > totalCr ? totalDr - totalCr : 0
    const closeCr = totalCr > totalDr ? totalCr - totalDr : 0

    const unmatchedCount = group.txns.filter(t => t.isUnmatched).length

    trialBalanceRows.push({
      code, name_en, name_zh, category_type,
      openDr, openCr, movDr, movCr, closeDr, closeCr,
      transactions: group.txns, unmatchedCount,
      isSuspense: isSuspenseRow || undefined,
    })

    if (!isSuspenseRow && (resolved === 'Income' || resolved === 'Expense')) {
      const amount = resolved === 'Income' ? movCr - movDr : movDr - movCr
      incomeRows.push({
        code, name_en, name_zh, category_type: resolved,
        amount, transactions: group.txns, unmatchedCount,
      })
    } else {
      const amount =
        resolved === 'Asset' || isSuspenseRow
          ? closeDr - closeCr
          : closeCr - closeDr
      const balanceCategory: BalanceRow['category_type'] = isSuspenseRow
        ? 'Asset'
        : resolved === 'Liability'
          ? 'Liability'
          : resolved === 'Equity'
            ? 'Equity'
            : 'Asset'
      balanceRows.push({
        code, name_en, name_zh,
        category_type: balanceCategory,
        amount, transactions: group.txns, unmatchedCount,
        isSuspense: isSuspenseRow || undefined,
      })
    }
  }

  const sortKey = (code: string) =>
    code === (suspenseCode || '9999') ? '\uffff' : code
  trialBalanceRows.sort((a, b) => sortKey(a.code).localeCompare(sortKey(b.code)))
  incomeRows.sort((a, b) => a.code.localeCompare(b.code))
  balanceRows.sort((a, b) => sortKey(a.code).localeCompare(sortKey(b.code)))

  const suspenseGroup = accountGroups.get(SUSPENSE_KEY)
  let suspenseNoCodeCount = 0
  let suspenseReconUnmatchedCount = 0
  let suspenseBothCount = 0
  let suspenseTotalDr = 0
  let suspenseTotalCr = 0
  if (suspenseGroup) {
    for (const t of suspenseGroup.txns) {
      if (!t.description.startsWith('[')) {
        if (t.isNoCode && t.isReconUnmatched) suspenseBothCount++
        else if (t.isNoCode) suspenseNoCodeCount++
        else if (t.isReconUnmatched) suspenseReconUnmatchedCount++
      }
    }
    suspenseTotalDr = suspenseGroup.dr
    suspenseTotalCr = suspenseGroup.cr
  }

  const totalMovDr = trialBalanceRows.reduce((s, r) => s + r.movDr, 0)
  const totalMovCr = trialBalanceRows.reduce((s, r) => s + r.movCr, 0)
  const totalIncome = incomeRows.filter(r => r.category_type === 'Income').reduce((s, r) => s + r.amount, 0)
  const totalExpense = incomeRows.filter(r => r.category_type === 'Expense').reduce((s, r) => s + r.amount, 0)
  const netIncome = totalIncome - totalExpense
  const totalOperatingAssets = balanceRows
    .filter(r => r.category_type === 'Asset' && !r.isSuspense)
    .reduce((s, r) => s + r.amount, 0)
  const totalSuspenseBalance = balanceRows
    .filter(r => r.isSuspense)
    .reduce((s, r) => s + r.amount, 0)
  const totalAsset = totalOperatingAssets + totalSuspenseBalance
  const totalLiability = balanceRows.filter(r => r.category_type === 'Liability').reduce((s, r) => s + r.amount, 0)
  const totalEquity = balanceRows.filter(r => r.category_type === 'Equity').reduce((s, r) => s + r.amount, 0)

  return {
    generatedAt: new Date().toISOString(),
    dateFrom,
    dateTo,
    suspenseCode,
    trialBalanceRows,
    incomeRows,
    balanceRows,
    summary: {
      totalMovDr,
      totalMovCr,
      totalIncome,
      totalExpense,
      netIncome,
      totalOperatingAssets,
      totalSuspenseBalance,
      totalAsset,
      totalLiability,
      totalEquity,
      balanced: Math.abs(totalMovDr - totalMovCr) < 0.005,
      balanceSheetBalanced:
        Math.abs(totalAsset - (totalLiability + totalEquity + netIncome)) < 0.005,
      suspenseNoCodeCount,
      suspenseReconUnmatchedCount,
      suspenseBothCount,
      suspenseTotalDr,
      suspenseTotalCr,
    },
  }
}

/** Sum movements when GL + OCR supplements are combined. */
export function mergeMovementMaps(
  a: Map<string, MovementGroup>,
  b: Map<string, MovementGroup>,
): Map<string, MovementGroup> {
  const out = new Map<string, MovementGroup>()
  const add = (src: Map<string, MovementGroup>) => {
    for (const [k, v] of src) {
      if (!out.has(k)) {
        out.set(k, { dr: 0, cr: 0, txns: [] })
      }
      const g = out.get(k)!
      g.dr += v.dr
      g.cr += v.cr
      g.txns.push(...v.txns)
    }
  }
  add(a)
  add(b)
  return out
}

export function collectGlLinkedTxnIds(journals: GlJournalPayload[]): {
  bank: Set<string>
  ledger: Set<string>
} {
  const bank = new Set<string>()
  const ledger = new Set<string>()
  for (const j of journals) {
    for (const ln of j.lines) {
      const b = (ln.bank_txn_id ?? '').trim()
      const le = (ln.ledger_txn_id ?? '').trim()
      if (b) bank.add(b)
      if (le) ledger.add(le)
    }
  }
  return { bank, ledger }
}

function journalInDateRange(journalDate: string | null | undefined, from: Date | null, to: Date | null): boolean {
  if (!journalDate) return true
  const d = new Date(journalDate.slice(0, 10) + 'T12:00:00')
  if (isNaN(d.getTime())) return true
  if (from && d < from) return false
  if (to && d > to) return false
  return true
}

/**
 * Build TB movement map from merged RECON GL journals (HKD lines). Each line is one-sided Dr or Cr.
 */
export function buildMovementMapFromGl(
  journals: GlJournalPayload[],
  dateFrom: string,
  dateTo: string,
): Map<string, MovementGroup> {
  const from = dateFrom ? new Date(dateFrom + 'T00:00:00') : null
  const to = dateTo ? new Date(dateTo + 'T23:59:59') : null
  const accountGroups = new Map<string, MovementGroup>()
  const getGroup = (code: string) => {
    if (!accountGroups.has(code)) {
      accountGroups.set(code, { dr: 0, cr: 0, txns: [] })
    }
    return accountGroups.get(code)!
  }

  for (const j of journals) {
    if (!journalInDateRange(j.journal_date, from, to)) continue
    if ((j.currency || 'HKD').toUpperCase() !== 'HKD') continue

    const st: 'draft' | 'posted' = (j.status || '').toLowerCase() === 'posted' ? 'posted' : 'draft'
    for (const ln of j.lines) {
      const code = (ln.account_code ?? '').trim()
      if (!code) continue
      const dr = typeof ln.debit === 'number' ? ln.debit : 0
      const cr = typeof ln.credit === 'number' ? ln.credit : 0
      const baseMeta: TxnGlMeta = {
        journalId: j.id,
        voucherNo: j.voucher_no,
        lineId: ln.id,
        status: st,
      }
      if (dr > 0.0005) {
        getGroup(code).dr += dr
        getGroup(code).txns.push({
          id: ln.id,
          date: (j.journal_date || '').slice(0, 10),
          amount: dr,
          description: `[GL ${j.voucher_no}] ${(ln.memo || '').trim()} (${st})`.trim(),
          isUnmatched: false,
          isNoCode: false,
          isReconUnmatched: false,
          gl: baseMeta,
        })
      }
      if (cr > 0.0005) {
        getGroup(code).cr += cr
        getGroup(code).txns.push({
          id: ln.id,
          date: (j.journal_date || '').slice(0, 10),
          amount: cr,
          description: `[GL ${j.voucher_no}] ${(ln.memo || '').trim()} (${st})`.trim(),
          isUnmatched: false,
          isNoCode: false,
          isReconUnmatched: false,
          gl: baseMeta,
        })
      }
    }
  }

  return accountGroups
}

/**
 * Mutates `accountGroups` with OCR-style AR/AP + bank double-entry (same rules as {@link computeReportData}).
 */
export function accumulateOcrTransactionsIntoMap(
  accountGroups: Map<string, MovementGroup>,
  arapTransactions: ARAPTransaction[],
  bankTransactions: BankTransaction[],
  opts: ReportOptions,
  unmatchedBankIds?: Set<string>,
  unmatchedLedgerIds?: Set<string>,
): void {
  const { dateFrom, dateTo, arControlCode, apControlCode, bankCode } = opts

  const from = dateFrom ? new Date(dateFrom + 'T00:00:00') : null
  const to = dateTo ? new Date(dateTo + 'T23:59:59') : null

  const inRange = (dateStr: string | undefined | null): boolean => {
    if (!dateStr) return true
    const d = new Date(dateStr)
    if (isNaN(d.getTime())) return true
    if (from && d < from) return false
    if (to && d > to) return false
    return true
  }

  const getGroup = (code: string) => {
    if (!accountGroups.has(code)) {
      accountGroups.set(code, { dr: 0, cr: 0, txns: [] })
    }
    return accountGroups.get(code)!
  }

  for (const t of arapTransactions) {
    if (!inRange(t.date)) continue

    const amount = typeof t.amount === 'number' ? Math.abs(t.amount) : 0
    if (amount === 0) continue

    const isNoCode = !(t.account_code ?? '').trim()
    const isReconUnmatch = unmatchedLedgerIds
      ? unmatchedLedgerIds.has(t.id_number ?? '') || unmatchedLedgerIds.has((t as ARAPTransaction & { voucher_no?: string }).voucher_no ?? '')
      : false
    const isSuspenseTxn = isNoCode || isReconUnmatch
    const assignedCode = isSuspenseTxn ? SUSPENSE_KEY : (t.account_code ?? '').trim()

    const txnType = (t.transaction_type ?? 'AR').toUpperCase()
    const narrative = [t.payer, t.payee, t.memo]
      .map(s => String(s ?? '').trim())
      .filter(Boolean)
      .join(' ')
      .replace(/\s+/g, ' ')
      .trim()
    const codeLabel = isSuspenseTxn ? '' : (t.account_code ?? '').trim()
    const baseDesc =
      [codeLabel, narrative].filter(Boolean).join(' · ').trim() ||
      (t.id_number ?? '').trim() ||
      '—'

    const txnBase: TxnRecord = {
      id: t.id_number ?? '',
      date: t.date ?? '',
      amount,
      description: baseDesc,
      isUnmatched: isSuspenseTxn,
      isNoCode,
      isReconUnmatched: isReconUnmatch,
    }

    if (txnType === 'AP') {
      getGroup(assignedCode).dr += amount
      getGroup(assignedCode).txns.push(txnBase)
      getGroup(apControlCode).cr += amount
      getGroup(apControlCode).txns.push({
        ...txnBase,
        description: `[${apControlCode}] ${baseDesc}`,
      })
    } else {
      getGroup(arControlCode).dr += amount
      getGroup(arControlCode).txns.push({
        ...txnBase,
        description: `[${arControlCode}] ${baseDesc}`,
      })
      getGroup(assignedCode).cr += amount
      getGroup(assignedCode).txns.push(txnBase)
    }
  }

  for (const t of bankTransactions) {
    if (t._duplicateConfirmed) continue
    const txnDate = t.transaction_date ?? t.date
    if (!inRange(txnDate)) continue

    const deposit = typeof t.deposit === 'number' && t.deposit > 0 ? t.deposit : 0
    const withdrawal = typeof t.withdrawal === 'number' && t.withdrawal > 0 ? t.withdrawal : 0
    if (deposit === 0 && withdrawal === 0) continue

    const isNoCode = !(t.account_code ?? '').trim()
    const isReconUnmatch = unmatchedBankIds
      ? unmatchedBankIds.has(t.id_number ?? '')
      : false
    const isSuspenseTxn = isNoCode || isReconUnmatch
    const assignedCode = isSuspenseTxn ? SUSPENSE_KEY : (t.account_code ?? '').trim()
    const baseDesc = t.particulars ?? t.description ?? ''

    if (deposit > 0) {
      const txnRec: TxnRecord = {
        id: t.id_number ?? '',
        date: txnDate ?? '',
        amount: deposit,
        description: `BANK deposit ${baseDesc}`.trim(),
        isUnmatched: isSuspenseTxn,
        isNoCode,
        isReconUnmatched: isReconUnmatch,
      }
      getGroup(bankCode).dr += deposit
      getGroup(bankCode).txns.push(txnRec)
      getGroup(assignedCode).cr += deposit
      getGroup(assignedCode).txns.push({ ...txnRec, description: `[Bank deposit] ${baseDesc}`.trim() })
    }

    if (withdrawal > 0) {
      const txnRec: TxnRecord = {
        id: t.id_number ?? '',
        date: txnDate ?? '',
        amount: withdrawal,
        description: `BANK withdrawal ${baseDesc}`.trim(),
        isUnmatched: isSuspenseTxn,
        isNoCode,
        isReconUnmatched: isReconUnmatch,
      }
      getGroup(assignedCode).dr += withdrawal
      getGroup(assignedCode).txns.push({ ...txnRec, description: `[Bank withdrawal] ${baseDesc}`.trim() })
      getGroup(bankCode).cr += withdrawal
      getGroup(bankCode).txns.push(txnRec)
    }
  }
}

export function buildHybridReportFromGlAndOcr(params: {
  mergedActiveJournals: GlJournalPayload[]
  supersededDrafts: GlSupersededDraft[]
  arapTransactions: ARAPTransaction[]
  bankTransactions: BankTransaction[]
  coaList: ChartOfAccountItem[]
  opts: ReportOptions
  unmatchedBankIds?: Set<string>
  unmatchedLedgerIds?: Set<string>
}): FinancialReportData {
  const {
    mergedActiveJournals,
    supersededDrafts,
    arapTransactions,
    bankTransactions,
    coaList,
    opts,
    unmatchedBankIds,
    unmatchedLedgerIds,
  } = params
  const { suspenseCode, arControlCode, apControlCode, bankCode, dateFrom, dateTo } = opts
  const controlCategoryHints: Record<string, string> = {
    [arControlCode]: 'Asset',
    [apControlCode]: 'Liability',
    [bankCode]: 'Asset',
  }

  const glMap = buildMovementMapFromGl(mergedActiveJournals, dateFrom, dateTo)
  const { bank: glBank, ledger: glLedger } = collectGlLinkedTxnIds(mergedActiveJournals)

  const arapFiltered = arapTransactions.filter(t => {
    const idn = String(t.id_number ?? '').trim()
    const vn = String((t as ARAPTransaction & { voucher_no?: string }).voucher_no ?? '').trim()
    if (idn && glLedger.has(idn)) return false
    if (vn && glLedger.has(vn)) return false
    return true
  })
  const bankFiltered = bankTransactions.filter(
    t => !t._duplicateConfirmed && !glBank.has(String(t.id_number ?? '').trim()),
  )

  const combined = mergeMovementMaps(glMap, new Map())
  accumulateOcrTransactionsIntoMap(
    combined,
    arapFiltered,
    bankFiltered,
    opts,
    unmatchedBankIds,
    unmatchedLedgerIds,
  )

  const base = materializeFinancialReport(
    combined,
    coaList,
    suspenseCode,
    dateFrom,
    dateTo,
    controlCategoryHints,
  )
  const includesDraft = mergedActiveJournals.some(
    j => (j.status || '').toLowerCase() === 'draft',
  )
  const hasGl = mergedActiveJournals.length > 0
  const ocrHasRows = arapFiltered.length > 0 || bankFiltered.length > 0

  return {
    ...base,
    glProvenance: {
      source: hasGl ? (ocrHasRows ? 'gl+ocr' : 'gl') : 'ocr',
      includesDraftJournals: includesDraft,
      activeVoucherNos: [...new Set(mergedActiveJournals.map(j => j.voucher_no))],
      supersededDrafts,
    },
  }
}

/** GL-only materialized report (no OCR supplement). */
export function buildReportFromGlJournalsOnly(
  mergedActiveJournals: GlJournalPayload[],
  supersededDrafts: GlSupersededDraft[],
  coaList: ChartOfAccountItem[],
  opts: ReportOptions,
): FinancialReportData {
  return buildHybridReportFromGlAndOcr({
    mergedActiveJournals,
    supersededDrafts,
    arapTransactions: [],
    bankTransactions: [],
    coaList,
    opts,
  })
}

// ─── Main export ──────────────────────────────────────────────────────────────

/**
 * Pure function — aggregates all session transactions into financial report data
 * using double-entry bookkeeping.
 *
 * Every transaction posts TWO legs (Dr + Cr of equal amount), guaranteeing
 * totalMovDr === totalMovCr and a balanced trial balance:
 *
 *   AR transaction  →  Dr arControlCode   |  Cr assigned_code (or Suspense)
 *   AP transaction  →  Dr assigned_code   |  Cr apControlCode (or Suspense)
 *   Bank deposit    →  Dr bankCode        |  Cr assigned_code (or Suspense)
 *   Bank withdrawal →  Dr assigned_code   |  Cr bankCode      (or Suspense)
 *
 * @param unmatchedBankIds   Bank transaction id_numbers that went through RECON
 *                           but could not be matched → forced to suspense account.
 * @param unmatchedLedgerIds Same for AR/AP transactions.
 *
 * Balance sheet `BalanceRow.amount` is a signed net aligned with closing Dr/Cr: assets & suspense use
 * (closeDr − closeCr); liabilities & equity use (closeCr − closeDr). When movement plus opening ties the
 * trial balance, `balanceSheetBalanced` reflects Assets ≈ Liabilities + Equity (CoA) + netIncome.
 */
export function computeReportData(
  arapTransactions: ARAPTransaction[],
  bankTransactions: BankTransaction[],
  coaList: ChartOfAccountItem[],
  opts: ReportOptions,
  unmatchedBankIds?: Set<string>,
  unmatchedLedgerIds?: Set<string>,
): FinancialReportData {
  const { suspenseCode, dateFrom, dateTo, arControlCode, apControlCode, bankCode } = opts
  const controlCategoryHints: Record<string, string> = {
    [arControlCode]: 'Asset',
    [apControlCode]: 'Liability',
    [bankCode]: 'Asset',
  }
  const accountGroups = new Map<string, MovementGroup>()
  accumulateOcrTransactionsIntoMap(
    accountGroups,
    arapTransactions,
    bankTransactions,
    opts,
    unmatchedBankIds,
    unmatchedLedgerIds,
  )
  return materializeFinancialReport(
    accountGroups,
    coaList,
    suspenseCode,
    dateFrom,
    dateTo,
    controlCategoryHints,
  )
}
