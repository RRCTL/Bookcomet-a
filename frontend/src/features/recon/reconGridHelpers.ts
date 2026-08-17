import type { BankTransaction, LedgerTransaction } from '../../types/reconciliation'
import type { ReconAiMessage, ReconFilters, ReconGridRow, ReconRawTxn } from './reconTypes'
import { RECON_RESULT_REVIEW_MSG_ID } from './reconTypes'

function normalizeSide(value: unknown): 'Dr' | 'Cr' | undefined {
  const s = String(value ?? '').trim()
  if (s === 'Dr' || s === 'Cr') return s
  return undefined
}

export function bankTxnToGridRow(t: BankTransaction): ReconGridRow {
  const amount = Number(t.amount ?? 0)
  return {
    id: t.id,
    date: t.bank_date ?? '',
    amount,
    currency: t.currency ?? '',
    description: t.description_raw ?? t.description_norm ?? '',
    reference: t.reference ?? '',
    status: t.status ?? 'unreconciled',
    accountId: t.account_id ?? '',
    recordMode: 'BANK',
    drCr: amount >= 0 ? 'Dr' : 'Cr',
    accountCategory: (t.account_category ?? '').trim() || undefined,
  }
}

export function ledgerTxnToGridRow(t: LedgerTransaction): ReconGridRow {
  const module = (t.module ?? '').toUpperCase()
  const amount = Number(t.amount ?? 0)
  const stored = normalizeSide(t.dr_cr)
  const fallback: 'Dr' | 'Cr' = module === 'AP' ? 'Dr' : 'Cr'
  return {
    id: t.id,
    date: t.book_date ?? '',
    amount,
    currency: t.currency ?? '',
    description: t.counterparty ?? '',
    reference: t.reference ?? t.doc_id ?? '',
    status: t.status ?? 'unreconciled',
    docType: t.doc_type ?? '',
    module,
    recordMode: module || (t.doc_type ?? '').toUpperCase(),
    drCr: stored ?? fallback,
  }
}

export function bankTxnToRaw(t: BankTransaction): ReconRawTxn {
  const amount = Number(t.amount ?? 0)
  return {
    id: t.id,
    bank_date: t.bank_date,
    description_raw: t.description_raw,
    amount: t.amount,
    currency: t.currency,
    reference: t.reference,
    account_id: t.account_id,
    account_category: t.account_category,
    status: t.status,
    recordMode: 'BANK',
    dr_cr: amount >= 0 ? 'Dr' : 'Cr',
  }
}

export function ledgerTxnToRaw(t: LedgerTransaction): ReconRawTxn {
  const module = (t.module ?? '').toUpperCase()
  const stored = normalizeSide(t.dr_cr)
  return {
    id: t.id,
    book_date: t.book_date,
    counterparty: t.counterparty,
    amount: t.amount,
    currency: t.currency,
    reference: t.reference,
    doc_type: t.doc_type,
    module,
    status: t.status,
    recordMode: module || (t.doc_type ?? '').toUpperCase(),
    dr_cr: stored ?? (module === 'AP' ? 'Dr' : 'Cr'),
  }
}

function bankAccountMatches(rowAccountId: string, filterAccount: string): boolean {
  const row = rowAccountId.trim().toLowerCase()
  const filt = filterAccount.trim().toLowerCase()
  if (!filt) return true
  if (!row) return false
  return row === filt || row.includes(filt) || filt.includes(row)
}

function ledgerModule(row: ReconGridRow): string {
  const mod = (row.module ?? '').toUpperCase()
  if (mod === 'AP' || mod === 'AR') return mod
  const dt = (row.docType ?? row.recordMode ?? '').toUpperCase()
  if (dt === 'AP' || dt === 'AR') return dt
  return mod
}

export type ReconScopeBankRow = {
  ref: string
  date: string
  amount: number
  account?: string
}

export type ReconNavIntent = {
  scoped: boolean
  sourceMode?: 'BANK' | 'AP' | 'AR'
  syncModes?: ('AP' | 'AR')[]
  scopeBank?: ReconScopeBankRow[]
  scopeBankRows?: Record<string, unknown>[]
  scopeLedgerRefs?: string[]
}

export type ReconWorkspaceSnapshot = ReconNavIntent & {
  selectedBankIds?: string[]
  selectedLedgerIds?: string[]
  filters?: Partial<ReconFilters>
}

function parseJsonArray(raw: string): string[] {
  if (!raw) return []
  const parsed = JSON.parse(raw)
  if (!Array.isArray(parsed)) return []
  return parsed.map(v => String(v).trim()).filter(Boolean)
}

/** Read persisted RECON scope (not cleared on read — overwritten on next Reconcile). */
export function readReconNav(): ReconNavIntent {
  try {
    const scoped = sessionStorage.getItem('recon_scoped') === '1'
    if (!scoped) return { scoped: false }

    const sourceModeRaw = (sessionStorage.getItem('recon_source_mode') ?? '').toUpperCase()
    const syncRaw = sessionStorage.getItem('recon_sync_modes') ?? ''
    const scopeBankRaw = sessionStorage.getItem('recon_scope_bank') ?? ''
    const scopeBankRowsRaw = sessionStorage.getItem('recon_scope_bank_rows') ?? ''
    const scopeLedgerRaw = sessionStorage.getItem('recon_scope_ledger_refs') ?? ''

    const syncModes = syncRaw
      .split(',')
      .map(s => s.trim().toUpperCase())
      .filter((m): m is 'AP' | 'AR' => m === 'AP' || m === 'AR')

    let scopeBank: ReconScopeBankRow[] = []
    if (scopeBankRaw) {
      const parsed = JSON.parse(scopeBankRaw)
      if (Array.isArray(parsed)) {
        scopeBank = parsed
          .map(row => ({
            ref: String(row.ref ?? '').trim(),
            date: String(row.date ?? '').trim(),
            amount: Number(row.amount),
            account: String(row.account ?? '').trim() || undefined,
          }))
          .filter(row => row.date && Number.isFinite(row.amount))
      }
    }

    let scopeBankRows: Record<string, unknown>[] = []
    if (scopeBankRowsRaw) {
      const parsed = JSON.parse(scopeBankRowsRaw)
      if (Array.isArray(parsed)) {
        scopeBankRows = parsed.filter(row => row && typeof row === 'object') as Record<string, unknown>[]
      }
    }

    const scopeLedgerRefs = parseJsonArray(scopeLedgerRaw)
    const sourceMode =
      sourceModeRaw === 'BANK' || sourceModeRaw === 'AP' || sourceModeRaw === 'AR'
        ? sourceModeRaw
        : undefined

    const resolvedSyncModes =
      syncModes.length > 0
        ? syncModes
        : sourceMode === 'AP' || sourceMode === 'AR'
          ? [sourceMode]
          : []

    return {
      scoped: true,
      sourceMode,
      syncModes: resolvedSyncModes.length ? resolvedSyncModes : undefined,
      scopeBank: scopeBank.length ? scopeBank : undefined,
      scopeBankRows: scopeBankRows.length ? scopeBankRows : undefined,
      scopeLedgerRefs: scopeLedgerRefs.length ? scopeLedgerRefs : undefined,
    }
  } catch {
    return { scoped: false }
  }
}

/** @deprecated alias */
export function readInitialReconNav(): ReconNavIntent {
  return readReconNav()
}

const RECON_FRESH_NAV_KEY = 'recon_nav_fresh'

/** Mark that the user just clicked Reconcile (reset pool from scope once). */
export function markReconFreshNav(): void {
  try {
    sessionStorage.setItem(RECON_FRESH_NAV_KEY, '1')
  } catch {
    // sessionStorage may be unavailable
  }
}

/** True only on the first load after markReconFreshNav(); flag is consumed. */
export function consumeReconFreshNav(): boolean {
  try {
    const fresh = sessionStorage.getItem(RECON_FRESH_NAV_KEY) === '1'
    sessionStorage.removeItem(RECON_FRESH_NAV_KEY)
    return fresh
  } catch {
    return false
  }
}

/** Parse server-persisted workspace snapshot into a nav intent. */
export function parseReconWorkspaceNav(raw: unknown): ReconNavIntent {
  if (!raw || typeof raw !== 'object') return { scoped: false }
  const ws = raw as ReconWorkspaceSnapshot

  const sourceModeRaw = String(ws.sourceMode ?? '').toUpperCase()
  const sourceMode =
    sourceModeRaw === 'BANK' || sourceModeRaw === 'AP' || sourceModeRaw === 'AR'
      ? sourceModeRaw
      : undefined

  const syncModes = (ws.syncModes ?? [])
    .map(m => String(m).trim().toUpperCase())
    .filter((m): m is 'AP' | 'AR' => m === 'AP' || m === 'AR')

  const resolvedSyncModes =
    syncModes.length > 0
      ? syncModes
      : sourceMode === 'AP' || sourceMode === 'AR'
        ? [sourceMode]
        : []

  const scopeBank = Array.isArray(ws.scopeBank)
    ? ws.scopeBank
      .map(row => ({
        ref: String(row.ref ?? '').trim(),
        date: String(row.date ?? '').trim(),
        amount: Number(row.amount),
        account: String(row.account ?? '').trim() || undefined,
      }))
      .filter(row => row.date && Number.isFinite(row.amount))
    : []

  const scopeBankRows = Array.isArray(ws.scopeBankRows)
    ? ws.scopeBankRows.filter(row => row && typeof row === 'object') as Record<string, unknown>[]
    : []

  const scopeLedgerRefs = Array.isArray(ws.scopeLedgerRefs)
    ? ws.scopeLedgerRefs.map(v => String(v).trim()).filter(Boolean)
    : []

  const hasScope =
    Boolean(sourceMode)
    || scopeBank.length > 0
    || scopeBankRows.length > 0
    || scopeLedgerRefs.length > 0
  if (!ws.scoped && !hasScope) return { scoped: false }

  return {
    scoped: true,
    sourceMode,
    syncModes: resolvedSyncModes.length ? resolvedSyncModes : undefined,
    scopeBank: scopeBank.length ? scopeBank : undefined,
    scopeBankRows: scopeBankRows.length ? scopeBankRows : undefined,
    scopeLedgerRefs: scopeLedgerRefs.length ? scopeLedgerRefs : undefined,
  }
}

export function buildReconWorkspaceSnapshot(
  nav: ReconNavIntent,
  selectedBankIds: string[],
  selectedLedgerIds: string[],
  filters: ReconFilters,
): ReconWorkspaceSnapshot {
  return {
    scoped: true,
    sourceMode: nav.sourceMode,
    syncModes: nav.syncModes,
    scopeBank: nav.scopeBank,
    scopeBankRows: nav.scopeBankRows,
    scopeLedgerRefs: nav.scopeLedgerRefs,
    selectedBankIds,
    selectedLedgerIds,
    filters,
  }
}

function refMatchesScope(value: string, scopeRef: string): boolean {
  const v = value.trim().toLowerCase()
  const s = scopeRef.trim().toLowerCase()
  if (!v || !s) return false
  return v === s || v.includes(s) || s.includes(v)
}

export function bankTxnMatchesScope(
  t: BankTransaction,
  scope: ReconScopeBankRow[],
): boolean {
  if (!scope.length) return false
  const ref = String(t.reference ?? t.description_raw ?? t.description_norm ?? '')
  const date = String(t.bank_date ?? '').slice(0, 10)
  const amt = Number(t.amount ?? 0)
  const acct = String(t.account_id ?? '').toLowerCase()
  return scope.some(s => {
    const dateMatch = !s.date || date === s.date.slice(0, 10)
    const amtMatch = Math.abs(amt - s.amount) < 0.01
    const acctMatch =
      !s.account ||
      acct === s.account.toLowerCase() ||
      acct.includes(s.account.toLowerCase()) ||
      s.account.toLowerCase().includes(acct)
    if (!(dateMatch && amtMatch && acctMatch)) return false
    if (!s.ref) return true
    return refMatchesScope(ref, s.ref)
  })
}

/** At most one DB bank row per Books scope row (avoids flooding the workspace). */
export function filterBankToScopeRows(
  all: BankTransaction[],
  scope: ReconScopeBankRow[],
): BankTransaction[] {
  if (!scope.length) return []
  const out: BankTransaction[] = []
  const used = new Set<string>()
  for (const s of scope) {
    const match = all.find(t => !used.has(t.id) && bankTxnMatchesScope(t, [s]))
    if (match) {
      out.push(match)
      used.add(match.id)
    }
  }
  return out
}

export function ledgerTxnMatchesScope(
  t: LedgerTransaction,
  refs: string[],
): boolean {
  if (!refs.length) return false
  const reference = String(t.reference ?? '')
  const docId = String(t.doc_id ?? '')
  return refs.some(r => refMatchesScope(reference, r) || refMatchesScope(docId, r))
}

/** At most one DB ledger row per selected voucher ref. */
export function filterLedgerToScopeRows(
  all: LedgerTransaction[],
  refs: string[],
): LedgerTransaction[] {
  if (!refs.length) return []
  const out: LedgerTransaction[] = []
  const used = new Set<string>()
  for (const ref of refs) {
    const match = all.find(t => !used.has(t.id) && ledgerTxnMatchesScope(t, [ref]))
    if (match) {
      out.push(match)
      used.add(match.id)
    }
  }
  return out
}

export function formatReconChipLabel(row: ReconGridRow): { title: string; meta: string } {
  const date = row.date?.slice(0, 10) || '-'
  const mag = Math.abs(Number(row.amount ?? 0)).toLocaleString('en-HK', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  const side = row.drCr ?? (Number(row.amount ?? 0) >= 0 ? 'Dr' : 'Cr')
  const ref = row.reference?.trim() || row.description?.trim().slice(0, 32) || 'No reference'
  const cur = row.currency ? `${row.currency} ` : ''
  const mode = row.recordMode || row.module || 'TXN'
  return {
    title: `${ref} · ${side} ${cur}${mag}`,
    meta: `${date} · ${mode}`,
  }
}

function inDateRange(iso: string, from: string, to: string): boolean {
  if (!iso) return true
  const d = iso.slice(0, 10)
  if (from && d < from.slice(0, 10)) return false
  if (to && d > to.slice(0, 10)) return false
  return true
}

export function filterBankRows(rows: ReconGridRow[], filters: ReconFilters): ReconGridRow[] {
  return rows.filter(r => {
    if (filters.bankAccount && !bankAccountMatches(r.accountId ?? '', filters.bankAccount)) return false
    return inDateRange(r.date, filters.dateFrom, filters.dateTo)
  })
}

export function filterLedgerRows(rows: ReconGridRow[], filters: ReconFilters): ReconGridRow[] {
  const ledgerFilter =
    filters.ledgerType === 'AP' || filters.ledgerType === 'AR' ? filters.ledgerType : 'all'
  return rows.filter(r => {
    if (ledgerFilter !== 'all') {
      const mod = ledgerModule(r)
      if (mod && mod !== ledgerFilter) return false
    }
    return inDateRange(r.date, filters.dateFrom, filters.dateTo)
  })
}

export function readInitialReconFilters(): Partial<ReconFilters> {
  try {
    const account = sessionStorage.getItem('recon_filter_account') ?? ''
    const dateFrom = sessionStorage.getItem('recon_filter_date_from') ?? ''
    const dateTo = sessionStorage.getItem('recon_filter_date_to') ?? ''
    const ledgerType = sessionStorage.getItem('recon_filter_ledger_type') ?? ''
    sessionStorage.removeItem('recon_filter_account')
    sessionStorage.removeItem('recon_filter_date_from')
    sessionStorage.removeItem('recon_filter_date_to')
    sessionStorage.removeItem('recon_filter_ledger_type')
    const out: Partial<ReconFilters> = {}
    if (account) out.bankAccount = account
    if (dateFrom) out.dateFrom = dateFrom
    if (dateTo) out.dateTo = dateTo
    if (ledgerType === 'AP' || ledgerType === 'AR') out.ledgerType = ledgerType
    return out
  } catch {
    return {}
  }
}

const RECON_AI_CHAT_MAX = 50

function reconAiChatStorageKey(companyId: string): string {
  return `erp_recon_ai_chat_${companyId}`
}

function isPersistableReconAiMessage(message: ReconAiMessage): boolean {
  // Keep "Thinking…" / progress bubbles so refresh or page switch can restore in-flight UI.
  if (message.isTyping) return false
  return true
}

export function readReconAiChatMessages(companyId: string | null): ReconAiMessage[] {
  if (!companyId) return []
  try {
    const raw = sessionStorage.getItem(reconAiChatStorageKey(companyId))
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter(
        (item): item is ReconAiMessage =>
          !!item
          && typeof item === 'object'
          && typeof (item as ReconAiMessage).id === 'string'
          && ((item as ReconAiMessage).role === 'user' || (item as ReconAiMessage).role === 'assistant')
          && typeof (item as ReconAiMessage).content === 'string',
      )
      .slice(-RECON_AI_CHAT_MAX)
  } catch {
    return []
  }
}

export function writeReconAiChatMessages(companyId: string | null, messages: ReconAiMessage[]): void {
  if (!companyId) return
  try {
    const toSave = messages.filter(isPersistableReconAiMessage).slice(-RECON_AI_CHAT_MAX)
    sessionStorage.setItem(reconAiChatStorageKey(companyId), JSON.stringify(toSave))
  } catch {
    // sessionStorage quota or private mode
  }
}

/** Upsert the single paginated MC review bubble for matched groups (or remove if empty). */
export function upsertReconResultReviewMessage(
  prev: ReconAiMessage[],
  groupIds: string[],
  options?: { markProcessedAt?: boolean },
): ReconAiMessage[] {
  if (groupIds.length === 0) {
    return prev.filter(m => m.id !== RECON_RESULT_REVIEW_MSG_ID)
  }
  const existing = prev.find(m => m.id === RECON_RESULT_REVIEW_MSG_ID)
  const prevPage = existing?.resultReview?.pageIndex ?? 0
  const pageIndex = Math.max(0, Math.min(prevPage, groupIds.length - 1))
  const now = new Date().toISOString()
  const processedAt = options?.markProcessedAt
    ? now
    : (existing?.resultReview?.processedAt ?? now)
  const content =
    groupIds.length === 1
      ? 'Reconciliation result ready. Choose an action below.'
      : `Reconciliation results: ${groupIds.length} matched groups. Review each page and choose an action.`
  const nextMsg: ReconAiMessage = {
    id: RECON_RESULT_REVIEW_MSG_ID,
    role: 'assistant',
    content,
    resultReview: { groupIds, pageIndex, processedAt },
  }
  if (existing) {
    return prev.map(m => (m.id === RECON_RESULT_REVIEW_MSG_ID ? nextMsg : m))
  }
  return [...prev, nextMsg]
}
