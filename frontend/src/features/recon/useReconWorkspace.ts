import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { MatchedGroupRow } from '../../components/ReconciliationTable'
import type { ChartOfAccountItem } from '../../types/reconciliation'
import type { BankTransaction, LedgerTransaction } from '../../types/reconciliation'
import { api, taskApi, BG_JOB_STORAGE_PREFIX, type ServerTaskMessage } from '../../services/api'
import { trackTabBackgroundJob, untrackTabBackgroundJob } from '../../services/tabBackgroundJobRegistry'
import { reconciliationApi, type GlJournalPayload } from '../../services/reconciliation'
import { filterSubsumedLedgerPendingGroups, normalizeReconTxnIdList } from '../../utils/reconMatchedSpreadsheet'
import { mapApiReconciliationGroupsToMatched } from '../../utils/reconGroupsFromApi'
import {
  bankTxnToGridRow,
  bankTxnToRaw,
  filterBankRows,
  filterLedgerRows,
  ledgerTxnToGridRow,
  ledgerTxnToRaw,
  buildReconWorkspaceSnapshot,
  consumeReconFreshNav,
  readInitialReconFilters,
  readReconAiChatMessages,
  upsertReconResultReviewMessage,
  writeReconAiChatMessages,
  type ReconNavIntent,
  type ReconWorkspaceSnapshot,
} from './reconGridHelpers'
import type { ReconAiAction, ReconAiMessage, ReconGridRow, ReconRawTxn } from './reconTypes'
import { useReconWorkspaceState } from './useReconWorkspaceState'
import { syncModulesToRecon } from './syncModulesToRecon'
import { normalizeReconCurrency } from './moduleReconKeys'
import { useErpNavigation } from '../erpShell/ErpNavigationContext'

const RECON_SESSION_ID = 'erp_recon_chat'
const RECON_AI_ALLOWED_ID_CAP = 150
const RECON_AI_HISTORY_MAX = 50
// AI Match no longer caps the total set: it batches through everything.
const RECON_AI_BATCH_SIZE = 40 // max rows per side sent to the LLM in one call
const RECON_AI_LARGE_THRESHOLD = 1000 // warn the user above this many candidate rows
/** localStorage job meta kind — resume RECON AI chat after refresh / leaving the page. */
const ERP_RECON_AI_CHAT_JOB_KIND = 'erp_recon_ai_chat'

type ErpReconAiChatJobMeta = {
  kind: typeof ERP_RECON_AI_CHAT_JOB_KIND
  companyId: string
  taskId: string
  progressMessageId: string
}

function isErpReconAiChatJobMeta(raw: unknown): raw is ErpReconAiChatJobMeta {
  if (!raw || typeof raw !== 'object') return false
  const o = raw as Record<string, unknown>
  return (
    o.kind === ERP_RECON_AI_CHAT_JOB_KIND
    && typeof o.companyId === 'string'
    && typeof o.taskId === 'string'
    && typeof o.progressMessageId === 'string'
  )
}

function isInProgressAiMatchContent(content: string): boolean {
  if (content.startsWith('Done.')) return false
  return (
    content.startsWith('AI matching…')
    || content.startsWith('Analyzing duplicates')
    || (content.startsWith('Matching ') && content.includes('batch'))
  )
}

/**
 * Stable per-company chat task id for the RECON assistant. Sending session_id
 * `${taskId}_RECON` makes the backend derive this exact task_id (it strips the
 * trailing `_RECON`), so turns persist to task_messages per company and can be
 * reloaded on any device — not just the browser tab they were typed in.
 */
function reconAiChatTaskId(companyId: string | null): string | null {
  return companyId ? `recon-ai-${companyId}` : null
}

function mapServerMsgsToReconAi(msgs: ServerTaskMessage[]): ReconAiMessage[] {
  return msgs
    .filter(m => m.role === 'user' || m.role === 'assistant')
    .map(m => ({ id: m.id, role: m.role as 'user' | 'assistant', content: m.content_text ?? '' }))
    .filter(m => m.content.trim().length > 0)
    .slice(-RECON_AI_HISTORY_MAX)
}

function applyAiChatResultToStoredMessages(
  companyId: string,
  progressMessageId: string,
  result: { reply?: string; recon_actions?: ReconAiAction[] } | null,
  errorText?: string,
): ReconAiMessage[] {
  const prev = readReconAiChatMessages(companyId)
  const ra = (result?.recon_actions ?? []) as ReconAiAction[]
  const next = prev.map(m => {
    if (m.id !== progressMessageId) return m
    if (errorText) return { ...m, content: `Chat failed: ${errorText}`, reconActions: undefined, reconActionsPending: false }
    return {
      ...m,
      content: result?.reply ?? '',
      reconActions: ra,
      reconActionsPending: ra.length > 0,
    }
  })
  writeReconAiChatMessages(companyId, next)
  return next
}

function hasPendingReconAiChatJob(companyId: string): boolean {
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (!k?.startsWith(BG_JOB_STORAGE_PREFIX)) continue
      const raw = localStorage.getItem(k)
      if (!raw) continue
      try {
        const meta = JSON.parse(raw) as unknown
        if (isErpReconAiChatJobMeta(meta) && meta.companyId === companyId) return true
      } catch {
        /* skip bad meta */
      }
    }
  } catch {
    /* private mode */
  }
  return false
}

function cappedAllowTxnIds(
  selectedIds: string[],
  poolTxns: ReconRawTxn[],
  cap: number,
): { ids: string[]; truncated: boolean } {
  const seen = new Set<string>()
  const ids: string[] = []
  for (const id of selectedIds) {
    const s = String(id)
    if (seen.has(s)) continue
    seen.add(s)
    ids.push(s)
  }
  let truncated = false
  for (const t of poolTxns) {
    const s = String(t.id)
    if (seen.has(s)) continue
    if (ids.length >= cap) {
      truncated = true
      break
    }
    seen.add(s)
    ids.push(s)
  }
  return { ids, truncated }
}

function isOpenTxnStatus(status: string | undefined): boolean {
  const s = (status ?? '').toLowerCase()
  return s === 'unreconciled' || s === 'partial'
}

function normCurrency(c: string | undefined): string {
  return normalizeReconCurrency(c)
}

function rowCurrency(id: string, map: Map<string, ReconGridRow>): string {
  return normCurrency(map.get(id)?.currency)
}

function firstKnownCurrency(ids: string[], map: Map<string, ReconGridRow>): string {
  for (const id of ids) {
    const c = rowCurrency(id, map)
    if (c) return c
  }
  return ''
}

function sameCurrency(bankIds: string[], ledgerIds: string[], bankMap: Map<string, ReconGridRow>, ledgerMap: Map<string, ReconGridRow>): string | null {
  const currencies = new Set<string>()
  bankIds.forEach(id => {
    const c = rowCurrency(id, bankMap)
    if (c) currencies.add(c)
  })
  ledgerIds.forEach(id => {
    const c = rowCurrency(id, ledgerMap)
    if (c) currencies.add(c)
  })
  if (currencies.size <= 1) return null
  return 'Cross-currency matching is not supported in v1. Select transactions in the same currency.'
}

function filterIdsByCurrency(
  ids: string[],
  map: Map<string, ReconGridRow>,
  anchor: string,
): string[] {
  if (!anchor) return ids
  const matched = ids.filter(id => {
    const c = rowCurrency(id, map)
    return !c || c === anchor
  })
  return matched.length ? matched : ids.filter(id => rowCurrency(id, map) === anchor)
}

function resolveAiMatchCandidates(
  selectedBankIds: string[],
  selectedLedgerIds: string[],
  bankRows: ReconGridRow[],
  ledgerRows: ReconGridRow[],
  bankMap: Map<string, ReconGridRow>,
  ledgerMap: Map<string, ReconGridRow>,
): { bankIds: string[]; ledgerIds: string[] } | null {
  if (!selectedBankIds.length && !selectedLedgerIds.length) return null
  if (bankRows.length === 0 && ledgerRows.length === 0) return null

  // Bank-only path: bank↔bank equal amounts and/or GL-only (no AR/AP pool).
  if (selectedBankIds.length && !selectedLedgerIds.length && ledgerRows.length === 0) {
    const anchor =
      firstKnownCurrency(selectedBankIds, bankMap) ||
      normCurrency(bankRows.find(r => r.currency)?.currency) ||
      'HKD'
    const bankIds = filterIdsByCurrency([...selectedBankIds], bankMap, anchor)
    return bankIds.length ? { bankIds, ledgerIds: [] } : null
  }

  if (bankRows.length === 0 || ledgerRows.length === 0) return null

  const poolIds = (rows: ReconGridRow[], anchor: string) => {
    if (!anchor) return rows.map(r => r.id)
    const matched = rows.filter(r => {
      const c = normCurrency(r.currency)
      return !c || c === anchor
    })
    return (matched.length ? matched : rows).map(r => r.id)
  }

  let bankIds: string[]
  let ledgerIds: string[]

  if (selectedBankIds.length && selectedLedgerIds.length) {
    bankIds = [...selectedBankIds]
    ledgerIds = [...selectedLedgerIds]
  } else if (selectedBankIds.length) {
    bankIds = [...selectedBankIds]
    ledgerIds = poolIds(ledgerRows, rowCurrency(selectedBankIds[0], bankMap))
  } else {
    ledgerIds = [...selectedLedgerIds]
    bankIds = poolIds(bankRows, rowCurrency(selectedLedgerIds[0], ledgerMap))
  }

  const anchor =
    firstKnownCurrency(selectedLedgerIds, ledgerMap) ||
    firstKnownCurrency(selectedBankIds, bankMap) ||
    firstKnownCurrency(ledgerIds, ledgerMap) ||
    firstKnownCurrency(bankIds, bankMap) ||
    normCurrency(ledgerRows.find(r => r.currency)?.currency) ||
    normCurrency(bankRows.find(r => r.currency)?.currency) ||
    'HKD'

  bankIds = filterIdsByCurrency(bankIds, bankMap, anchor)
  ledgerIds = filterIdsByCurrency(ledgerIds, ledgerMap, anchor)

  if (!bankIds.length || !ledgerIds.length) return null

  return { bankIds, ledgerIds }
}

/**
 * Group candidates into batches the LLM can handle. Rows are bucketed by exact
 * amount so a bank line and its ledger counterpart (which must share an amount
 * to match) always land in the same batch — otherwise cross-batch matches would
 * be lost. Buckets with rows on only one side cannot match and are dropped.
 * Buckets are then packed together up to RECON_AI_BATCH_SIZE rows per side.
 */
function buildAiMatchBatches(
  bankIds: string[],
  ledgerIds: string[],
  bankMap: Map<string, ReconGridRow>,
  ledgerMap: Map<string, ReconGridRow>,
  batchSize: number,
): { bankIds: string[]; ledgerIds: string[] }[] {
  const amountKey = (n: number) => Math.round(Math.abs(n || 0) * 100)
  const buckets = new Map<number, { bank: string[]; ledger: string[] }>()
  const bucketFor = (k: number) => {
    let b = buckets.get(k)
    if (!b) {
      b = { bank: [], ledger: [] }
      buckets.set(k, b)
    }
    return b
  }
  for (const id of bankIds) {
    const r = bankMap.get(id)
    if (r) bucketFor(amountKey(r.amount)).bank.push(id)
  }
  for (const id of ledgerIds) {
    const r = ledgerMap.get(id)
    if (r) bucketFor(amountKey(r.amount)).ledger.push(id)
  }

  const batches: { bankIds: string[]; ledgerIds: string[] }[] = []
  let cur = { bankIds: [] as string[], ledgerIds: [] as string[] }
  for (const b of buckets.values()) {
    if (!b.bank.length || !b.ledger.length) continue
    if (
      cur.bankIds.length
      && (cur.bankIds.length + b.bank.length > batchSize
        || cur.ledgerIds.length + b.ledger.length > batchSize)
    ) {
      batches.push(cur)
      cur = { bankIds: [], ledgerIds: [] }
    }
    cur.bankIds.push(...b.bank)
    cur.ledgerIds.push(...b.ledger)
  }
  if (cur.bankIds.length) batches.push(cur)
  return batches
}

export function useReconWorkspace(companyId: string | null) {
  const state = useReconWorkspaceState(companyId)
  const { reconNavTick } = useErpNavigation()
  const [bankAll, setBankAll] = useState<ReconGridRow[]>([])
  const [ledgerAll, setLedgerAll] = useState<ReconGridRow[]>([])
  const [coaList, setCoaList] = useState<ChartOfAccountItem[]>([])
  const [scopedNav, setScopedNav] = useState<ReconNavIntent | null>(null)
  const [needsSelection, setNeedsSelection] = useState(true)
  const sessionHydratedRef = useRef(false)
  const chatHistoryLoadedForRef = useRef<string | null>(null)
  const mountedRef = useRef(true)
  const aiMatchAbortRef = useRef(false)
  const resumingAiChatRef = useRef(false)
  const setAiMessagesRef = useRef(state.setAiMessages)
  const setAiThinkingRef = useRef(state.setAiThinking)
  setAiMessagesRef.current = state.setAiMessages
  setAiThinkingRef.current = state.setAiThinking

  useEffect(() => {
    mountedRef.current = true
    aiMatchAbortRef.current = false
    return () => {
      mountedRef.current = false
      aiMatchAbortRef.current = true
    }
  }, [])

  const bankMap = useMemo(() => new Map(bankAll.map(r => [r.id, r])), [bankAll])
  const ledgerMap = useMemo(() => new Map(ledgerAll.map(r => [r.id, r])), [ledgerAll])

  const matchedTxnIds = useMemo(() => {
    const ids = new Set<string>()
    state.reconMatchedGroups.forEach(g => {
      normalizeReconTxnIdList(g.bank_txn_ids).forEach(id => ids.add(id))
      normalizeReconTxnIdList(g.ledger_txn_ids).forEach(id => ids.add(id))
    })
    return ids
  }, [state.reconMatchedGroups])

  const bankRows = useMemo(() => filterBankRows(bankAll, state.filters), [bankAll, state.filters])
  const ledgerRows = useMemo(() => filterLedgerRows(ledgerAll, state.filters), [ledgerAll, state.filters])

  const bankRowById = useMemo(() => new Map(bankAll.map(r => [r.id, r])), [bankAll])
  const ledgerRowById = useMemo(() => new Map(ledgerAll.map(r => [r.id, r])), [ledgerAll])

  const bankAccounts = useMemo(() => {
    const set = new Set<string>()
    bankAll.forEach(r => {
      if (r.accountId) set.add(r.accountId)
    })
    return Array.from(set).sort()
  }, [bankAll])

  const persistSession = useCallback(async (
    nav?: ReconNavIntent | null,
    overrides?: {
      selectedBankIds?: string[]
      selectedLedgerIds?: string[]
      unmatched?: { bank: ReconRawTxn[]; ledger: ReconRawTxn[] }
      filters?: typeof state.filters
    },
  ) => {
    const activeNav = nav ?? scopedNav
    const unmatched = overrides?.unmatched ?? state.reconUnmatchedTxns
    const entries = [
      ...unmatched.bank.map(t => ({
        txn_id: String(t.id),
        txn_type: 'bank' as const,
        raw_txn_data: t,
      })),
      ...unmatched.ledger.map(t => ({
        txn_id: String(t.id),
        txn_type: 'ledger' as const,
        raw_txn_data: t,
      })),
    ]
    const selectedBankIds = overrides?.selectedBankIds ?? state.selectedBankIds
    const selectedLedgerIds = overrides?.selectedLedgerIds ?? state.selectedLedgerIds
    const filters = overrides?.filters ?? state.filters
    try {
      await reconciliationApi.saveSession({
        entries,
        workspace: activeNav?.scoped
          ? buildReconWorkspaceSnapshot(
              activeNav,
              selectedBankIds,
              selectedLedgerIds,
              filters,
            )
          : undefined,
      })
    } catch (e) {
      console.warn('[RECON] save session failed:', e)
    }
  }, [scopedNav, state.reconUnmatchedTxns, state.selectedBankIds, state.selectedLedgerIds, state.filters])

  const refreshGroups = useCallback(async () => {
    try {
      const { groups } = await reconciliationApi.fetchGroups()
      const mapped = groups.length ? mapApiReconciliationGroupsToMatched(groups) : []
      const next = mapped.length ? filterSubsumedLedgerPendingGroups(mapped) : []
      state.reconMatchedGroupsRef.current = next
      state.setReconMatchedGroups(next)
      state.setAiMessages(prev => upsertReconResultReviewMessage(prev, next.map(g => g.id)))
    } catch (e) {
      console.warn('[RECON] refresh groups failed:', e)
    }
  }, [state])

  const loadTransactions = useCallback(async (
    _nav?: ReconNavIntent,
    sessionData?: Awaited<ReturnType<typeof reconciliationApi.fetchSession>> | null,
    resetFromScope = false,
  ) => {
    if (!companyId) {
      setNeedsSelection(true)
      return
    }
    // Full Books mirror — no Reconcile-button scope required.
    const intent: ReconNavIntent = { scoped: true }
    sessionHydratedRef.current = false
    setScopedNav(intent)
    state.setLoading(true)
    setNeedsSelection(false)
    try {
      let syncWarning: string | null = null
      try {
        await syncModulesToRecon(companyId)
      } catch (err) {
        syncWarning = err instanceof Error ? err.message : String(err)
        console.warn('[RECON] module sync failed; loading panels from recon DB:', err)
      }

      const [ledgerData, bankData, coa] = await Promise.all([
        reconciliationApi.getLedgerTransactions() as Promise<LedgerTransaction[]>,
        reconciliationApi.getBankTransactions() as Promise<BankTransaction[]>,
        // Full chart: RECON journals use BANK/AR/AP/suspense codes; mode=RECON matches nothing in allowed_modes.
        reconciliationApi.getChartOfAccounts(),
      ])
      setCoaList(coa.accounts ?? [])

      const scopedBank = bankData
      const scopedLedger = ledgerData

      setBankAll(scopedBank.map(bankTxnToGridRow))
      setLedgerAll(scopedLedger.map(ledgerTxnToGridRow))

      const openBank = scopedBank.filter(t => isOpenTxnStatus(t.status))
      const openLedger = scopedLedger.filter(t => isOpenTxnStatus(t.status))
      const openBankIds = new Set(openBank.map(t => t.id))
      const openLedgerIds = new Set(openLedger.map(t => t.id))

      let unmatchedBank = openBank.map(bankTxnToRaw)
      let unmatchedLedger = openLedger.map(ledgerTxnToRaw)
      const savedBank = sessionData?.bank_txns ?? []
      const savedLedger = sessionData?.ledger_txns ?? []
      if (!resetFromScope && (savedBank.length || savedLedger.length)) {
        const restoredBank = savedBank.filter(t => openBankIds.has(String(t.id)))
        const restoredLedger = savedLedger.filter(t => openLedgerIds.has(String(t.id)))
        if (restoredBank.length || restoredLedger.length) {
          unmatchedBank = restoredBank
          unmatchedLedger = restoredLedger
        }
      }
      state.setReconUnmatchedTxns({ bank: unmatchedBank, ledger: unmatchedLedger })

      const ws = sessionData?.workspace as ReconWorkspaceSnapshot | null | undefined
      let selectedBankIds: string[] = []
      let selectedLedgerIds: string[] = []
      if (!resetFromScope && ws?.selectedBankIds?.length) {
        selectedBankIds = ws.selectedBankIds.filter(id => openBankIds.has(id))
      }
      if (!resetFromScope && ws?.selectedLedgerIds?.length) {
        selectedLedgerIds = ws.selectedLedgerIds.filter(id => openLedgerIds.has(id))
      }
      state.setSelectedBankIds(selectedBankIds)
      state.setSelectedLedgerIds(selectedLedgerIds)

      if (syncWarning) {
        state.setStatusText(`Books sync failed: ${syncWarning}`)
      } else if (scopedBank.length === 0 && scopedLedger.length === 0) {
        state.setStatusText('No Books transactions yet. Approve Bank/AP/AR runs, then Refresh.')
      } else {
        state.setStatusText('')
      }

      await refreshGroups()
      const partial = await reconciliationApi.getPartialTransactions()
      state.setReconPartialTxns(partial.partial_transactions ?? [])
      sessionHydratedRef.current = true
      void persistSession(intent, {
        selectedBankIds,
        selectedLedgerIds,
        unmatched: { bank: unmatchedBank, ledger: unmatchedLedger },
      })
    } catch (e) {
      state.setStatusText(e instanceof Error ? e.message : String(e))
    } finally {
      state.setLoading(false)
    }
  }, [companyId, persistSession, refreshGroups, state])

  useEffect(() => {
    if (!companyId) return
    let cancelled = false
    sessionHydratedRef.current = false

    void (async () => {
      const initial = readInitialReconFilters()
      let sessionData: Awaited<ReturnType<typeof reconciliationApi.fetchSession>> | null = null

      try {
        sessionData = await reconciliationApi.fetchSession()
      } catch (e) {
        console.warn('[RECON] fetch session failed:', e)
      }

      if (cancelled) return

      if (Object.keys(initial).length) {
        state.setFilters(prev => ({ ...prev, ...initial }))
      } else if (sessionData?.workspace && typeof sessionData.workspace === 'object') {
        const ws = sessionData.workspace as ReconWorkspaceSnapshot
        if (ws.filters && typeof ws.filters === 'object') {
          state.setFilters(prev => ({ ...prev, ...ws.filters! }))
        }
      }

      consumeReconFreshNav()
      await loadTransactions({ scoped: true }, sessionData, true)
    })()

    return () => {
      cancelled = true
    }
  }, [companyId, reconNavTick])

  // Load AI assistant chat history from the server so it is visible across
  // devices/browsers (sessionStorage only seeds an instant local cache).
  // Skip overwrite while a chat job is still pending — keeps Thinking… / progress.
  useEffect(() => {
    if (!companyId) return
    if (chatHistoryLoadedForRef.current === companyId) return
    chatHistoryLoadedForRef.current = companyId

    // Mark abandoned AI Match progress left by a previous unmount/refresh.
    const local = readReconAiChatMessages(companyId)
    if (local.some(m => m.role === 'assistant' && isInProgressAiMatchContent(m.content))) {
      const marked = local.map(m =>
        m.role === 'assistant' && isInProgressAiMatchContent(m.content)
          ? {
              ...m,
              content:
                'AI Match was interrupted (page refresh or navigation). Re-run AI Match to continue.',
            }
          : m,
      )
      writeReconAiChatMessages(companyId, marked)
      setAiMessagesRef.current(marked)
    }

    if (hasPendingReconAiChatJob(companyId)) return

    const taskId = reconAiChatTaskId(companyId)
    if (!taskId) return
    let cancelled = false
    void (async () => {
      try {
        const serverMsgs = await taskApi.getMessages(taskId, companyId)
        if (cancelled || !mountedRef.current) return
        if (hasPendingReconAiChatJob(companyId)) return
        const mapped = mapServerMsgsToReconAi(serverMsgs)
        if (mapped.length) setAiMessagesRef.current(mapped)
      } catch {
        // 404 (no chat yet) or transient error — keep the locally cached messages
      }
    })()
    return () => {
      cancelled = true
    }
  }, [companyId])

  // Resume RECON AI chat background jobs after refresh or returning to this page.
  useEffect(() => {
    if (!companyId) return
    let cancelled = false

    const resumeOne = async (storageKey: string, jobId: string, meta: ErpReconAiChatJobMeta) => {
      if (meta.companyId !== companyId) return
      setAiThinkingRef.current(true)
      trackTabBackgroundJob(jobId)
      try {
        const st = await api.getBackgroundJob(jobId, companyId)
        if (cancelled) return
        if (st.status === 'failed' || st.status === 'cancelled') {
          const next = applyAiChatResultToStoredMessages(
            companyId,
            meta.progressMessageId,
            null,
            st.error_text || (st.status === 'cancelled' ? 'cancelled' : 'failed'),
          )
          if (mountedRef.current) setAiMessagesRef.current(next)
          localStorage.removeItem(storageKey)
          return
        }
        let result: { reply?: string; recon_actions?: ReconAiAction[] }
        if (st.status === 'completed' && st.result_json) {
          result = st.result_json as { reply?: string; recon_actions?: ReconAiAction[] }
        } else {
          result = await api.waitForBackgroundJob(jobId, {
            companyId,
            isCancelled: () => cancelled,
          })
        }
        if (cancelled) return
        const next = applyAiChatResultToStoredMessages(companyId, meta.progressMessageId, result)
        if (mountedRef.current) setAiMessagesRef.current(next)
        localStorage.removeItem(storageKey)
      } catch (e) {
        if (cancelled) return
        if ((e as DOMException)?.name === 'AbortError') return
        const err = e instanceof Error ? e.message : String(e)
        const next = applyAiChatResultToStoredMessages(companyId, meta.progressMessageId, null, err)
        if (mountedRef.current) setAiMessagesRef.current(next)
        localStorage.removeItem(storageKey)
      } finally {
        untrackTabBackgroundJob(jobId)
        if (mountedRef.current && !cancelled) setAiThinkingRef.current(false)
      }
    }

    const keys: string[] = []
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i)
        if (k?.startsWith(BG_JOB_STORAGE_PREFIX)) keys.push(k)
      }
    } catch {
      return
    }

    void (async () => {
      if (resumingAiChatRef.current) return
      resumingAiChatRef.current = true
      try {
        for (const key of keys) {
          if (cancelled) break
          const raw = localStorage.getItem(key)
          if (!raw) continue
          let meta: unknown
          try {
            meta = JSON.parse(raw)
          } catch {
            continue
          }
          if (!isErpReconAiChatJobMeta(meta) || meta.companyId !== companyId) continue
          const jobId = key.slice(BG_JOB_STORAGE_PREFIX.length)
          await resumeOne(key, jobId, meta)
        }
      } finally {
        resumingAiChatRef.current = false
      }
    })()

    return () => {
      cancelled = true
    }
  }, [companyId])

  useEffect(() => {
    if (!companyId || !scopedNav?.scoped || state.loading || !sessionHydratedRef.current) return
    void persistSession(scopedNav)
  }, [
    companyId,
    scopedNav,
    state.loading,
    state.reconUnmatchedTxns,
    state.selectedBankIds,
    state.selectedLedgerIds,
    state.filters,
    persistSession,
  ])

  const applyMultiManualMatchResult = useCallback(
    (matchedBankIds: string[], matchedLedgerIds: string[], result: { group_id?: string; match_cardinality?: string; total_bank_amount?: number; total_ledger_amount?: number; difference?: number } | null) => {
      const bankSet = new Set(matchedBankIds.map(String))
      const ledgerSet = new Set(matchedLedgerIds.map(String))
      const snapshotBank = state.reconUnmatchedTxns.bank.filter(t => bankSet.has(String(t.id)))
      const snapshotLedger = state.reconUnmatchedTxns.ledger.filter(t => ledgerSet.has(String(t.id)))
      const allMatched = new Set([...bankSet, ...ledgerSet])

      state.setReconUnmatchedTxns(prev => ({
        bank: prev.bank.filter(t => !allMatched.has(String(t.id))),
        ledger: prev.ledger.filter(t => !allMatched.has(String(t.id))),
      }))

      if (result?.group_id) {
        const ledgerPendingOnly = matchedBankIds.length === 0 && matchedLedgerIds.length > 0
        const newGroup: MatchedGroupRow = {
          id: result.group_id,
          match_cardinality: result.match_cardinality ?? '1:1',
          bank_vouchers: snapshotBank.map((t: ReconRawTxn) => String(t.reference ?? t.id)),
          ledger_vouchers: snapshotLedger.map((t: ReconRawTxn) => String(t.reference ?? t.id)),
          bank_txn_ids: matchedBankIds,
          ledger_txn_ids: matchedLedgerIds,
          bank_total: result.total_bank_amount ?? 0,
          ledger_total: result.total_ledger_amount ?? 0,
          difference: result.difference ?? 0,
          confidence: null,
          rule_hit: 'manual',
          is_legacy: false,
          currency: String(snapshotBank[0]?.currency ?? snapshotLedger[0]?.currency ?? ''),
          bank_txn_snapshots: snapshotBank,
          ledger_txn_snapshots: snapshotLedger,
          is_same_mode: ledgerPendingOnly ? false : (snapshotBank[0] as ReconRawTxn)?.recordMode === (snapshotLedger[0] as ReconRawTxn)?.recordMode,
          created_at: new Date().toISOString(),
        }
        const nextGroups = filterSubsumedLedgerPendingGroups([
          ...state.reconMatchedGroupsRef.current,
          newGroup,
        ])
        state.reconMatchedGroupsRef.current = nextGroups
        state.setReconMatchedGroups(nextGroups)
        reconciliationApi.glEnsureDraft(result.group_id).catch(err => console.warn('[RECON GL]', err))
      }

      state.setSelectedBankIds([])
      state.setSelectedLedgerIds([])
      state.setAiMessages(prev =>
        upsertReconResultReviewMessage(
          prev,
          state.reconMatchedGroupsRef.current.map(g => g.id),
          { markProcessedAt: true },
        ),
      )
      void persistSession()
      reconciliationApi.getPartialTransactions()
        .then(res => state.setReconPartialTxns(res.partial_transactions ?? []))
        .catch(() => {})
    },
    [persistSession, state],
  )

  const handleMatch = useCallback(async () => {
    const bankIds = [...state.selectedBankIds]
    const ledgerIds = [...state.selectedLedgerIds]
    const currencyErr = sameCurrency(bankIds, ledgerIds, bankMap, ledgerMap)
    if (currencyErr) {
      state.setStatusText(currencyErr)
      return
    }

    if (bankIds.length === 0 && ledgerIds.length === 1) {
      try {
        const res = await reconciliationApi.ledgerPendingMatch({ ledger_txn_ids: ledgerIds })
        applyMultiManualMatchResult([], ledgerIds, res)
        state.setStatusText('Ledger pending bank match created.')
        if (companyId) void syncModulesToRecon(companyId).catch(() => {})
      } catch (e) {
        state.setStatusText(e instanceof Error ? e.message : String(e))
      }
      return
    }

    if (ledgerIds.length === 0 && bankIds.length === 1) {
      try {
        const glCode = (bankMap.get(bankIds[0])?.accountCategory ?? '').trim()
        if (glCode) {
          const res = await reconciliationApi.glOnlyMatch({ bank_txn_ids: bankIds })
          applyMultiManualMatchResult(bankIds, [], res)
          state.setStatusText(`Bank GL match created (${glCode}).`)
        } else {
          const res = await reconciliationApi.clearBankTransactions({ bank_txn_ids: bankIds })
          applyMultiManualMatchResult(bankIds, [], res)
          state.setStatusText('Bank transaction marked cleared.')
        }
        await refreshGroups()
        state.setAiMessages(prev =>
          upsertReconResultReviewMessage(
            prev,
            state.reconMatchedGroupsRef.current.map(g => g.id),
          ),
        )
        if (companyId) void syncModulesToRecon(companyId).catch(() => {})
      } catch (e) {
        state.setStatusText(e instanceof Error ? e.message : String(e))
      }
      return
    }

    if (bankIds.length === 0 || ledgerIds.length === 0) {
      state.setStatusText('Select at least one bank and one ledger row, or use single-side flows.')
      return
    }

    try {
      const auto = await reconciliationApi.autoMatchSelected(bankIds, ledgerIds)
      const autoPairs = auto.matches.filter(m => m.decision === 'auto')
      if (autoPairs.length > 0) {
        for (const m of autoPairs) {
          const res = await reconciliationApi.multiManualMatch({
            bank_txn_ids: [m.bank_txn_id],
            ledger_txn_ids: [m.ledger_txn_id],
          })
          applyMultiManualMatchResult([m.bank_txn_id], [m.ledger_txn_id], res)
        }
        state.setStatusText(`Matched ${autoPairs.length} pair(s) via rules.`)
        await refreshGroups()
        state.setAiMessages(prev =>
          upsertReconResultReviewMessage(
            prev,
            state.reconMatchedGroupsRef.current.map(g => g.id),
          ),
        )
        if (companyId) void syncModulesToRecon(companyId).catch(() => {})
        return
      }
      const res = await reconciliationApi.multiManualMatch({ bank_txn_ids: bankIds, ledger_txn_ids: ledgerIds })
      applyMultiManualMatchResult(bankIds, ledgerIds, res)
      state.setStatusText('Manual match created.')
      await refreshGroups()
      state.setAiMessages(prev =>
        upsertReconResultReviewMessage(
          prev,
          state.reconMatchedGroupsRef.current.map(g => g.id),
        ),
      )
      if (companyId) void syncModulesToRecon(companyId).catch(() => {})
    } catch (e) {
      state.setStatusText(e instanceof Error ? e.message : String(e))
    }
  }, [applyMultiManualMatchResult, bankMap, companyId, ledgerMap, refreshGroups, state])

  const handleAIMatch = useCallback(async () => {
    const resolved = resolveAiMatchCandidates(
      state.selectedBankIds,
      state.selectedLedgerIds,
      bankRows,
      ledgerRows,
      bankMap,
      ledgerMap,
    )
    if (!resolved) {
      const hint =
        state.selectedBankIds.length || state.selectedLedgerIds.length
          ? 'No matching-currency rows on the other side for AI Match.'
          : 'Select rows on at least one side for AI Match.'
      state.setStatusText(hint)
      state.setAiMessages(prev => [
        ...prev,
        { id: `ai-hint-${Date.now()}`, role: 'assistant', content: hint },
      ])
      return
    }

    const { bankIds, ledgerIds } = resolved
    const currencyErr = sameCurrency(bankIds, ledgerIds, bankMap, ledgerMap)
    if (currencyErr) {
      state.setStatusText(currencyErr)
      state.setAiMessages(prev => [
        ...prev,
        { id: `ai-hint-${Date.now()}`, role: 'assistant', content: currencyErr },
      ])
      return
    }

    const batches = ledgerIds.length
      ? buildAiMatchBatches(bankIds, ledgerIds, bankMap, ledgerMap, RECON_AI_BATCH_SIZE)
      : []
    const totalCandidates = bankIds.length + ledgerIds.length

    const progressId = `ai-match-${Date.now()}`
    const startContent = totalCandidates > RECON_AI_LARGE_THRESHOLD
      ? `Matching ${totalCandidates} transactions in ${batches.length || 1} batch(es). This may take a few minutes — progress will update here.`
      : 'Analyzing duplicates and matches…'
    state.setAiMessages(prev => [
      ...prev,
      { id: progressId, role: 'assistant', content: startContent },
    ])
    state.setAiThinking(true)
    aiMatchAbortRef.current = false

    const setProgress = (text: string) => {
      if (mountedRef.current) {
        state.setAiMessages(prev => prev.map(m => (m.id === progressId ? { ...m, content: text } : m)))
      } else if (companyId) {
        const prev = readReconAiChatMessages(companyId)
        writeReconAiChatMessages(
          companyId,
          prev.map(m => (m.id === progressId ? { ...m, content: text } : m)),
        )
      }
    }

    let applied = 0
    let failed = 0
    let skippedUnequal = 0
    let bankBankApplied = 0
    let glOnlyApplied = 0
    const usedBank = new Set<string>()
    const amountCents = (n: number) => Math.round(Math.abs(n || 0) * 100)
    try {
      for (let i = 0; i < batches.length; i++) {
        if (aiMatchAbortRef.current) {
          setProgress(
            `AI Match interrupted after batch ${i}/${batches.length} (${applied} pair(s) matched). Re-run AI Match to continue.`,
          )
          break
        }
        setProgress(`AI matching… batch ${i + 1}/${batches.length} (${applied} pair(s) matched so far)`)
        try {
          const result = await reconciliationApi.aiMatch(batches[i].bankIds, batches[i].ledgerIds)
          for (const m of result.matches ?? []) {
            if (aiMatchAbortRef.current) break
            const bankRow = bankMap.get(String(m.bank_txn_id))
            const ledgerRow = ledgerMap.get(String(m.ledger_txn_id))
            // Accounting rule: only tick the same economic event (equal absolute amounts).
            if (
              !bankRow
              || !ledgerRow
              || amountCents(bankRow.amount) !== amountCents(ledgerRow.amount)
            ) {
              skippedUnequal += 1
              continue
            }
            try {
              const res = await reconciliationApi.multiManualMatch({
                bank_txn_ids: [m.bank_txn_id],
                ledger_txn_ids: [m.ledger_txn_id],
              })
              applyMultiManualMatchResult([m.bank_txn_id], [m.ledger_txn_id], res)
              usedBank.add(String(m.bank_txn_id))
              applied += 1
            } catch {
              failed += 1
            }
          }
        } catch {
          failed += 1
        }
      }

      // After AR/AP: bank↔bank equal abs amounts, then bank+GL → GL-only.
      if (!aiMatchAbortRef.current) {
        setProgress('Checking bank↔bank and bank GL fallbacks…')
        const remaining = bankIds.filter(id => !usedBank.has(id))
        const byAmt = new Map<number, string[]>()
        for (const id of remaining) {
          const row = bankMap.get(id)
          if (!row) continue
          const k = amountCents(row.amount)
          const list = byAmt.get(k) ?? []
          list.push(id)
          byAmt.set(k, list)
        }
        for (const ids of byAmt.values()) {
          if (aiMatchAbortRef.current) break
          const pool = [...ids]
          while (pool.length >= 2) {
            const a = pool.shift()!
            const aRow = bankMap.get(a)
            if (!aRow) continue
            // Prefer opposite-sign transfer pair when available.
            let bi = pool.findIndex(id => {
              const r = bankMap.get(id)
              return r != null && Math.sign(r.amount) !== Math.sign(aRow.amount)
            })
            if (bi < 0) bi = 0
            const b = pool.splice(bi, 1)[0]
            if (!b) break
            try {
              const res = await reconciliationApi.multiManualMatch({
                bank_txn_ids: [a],
                ledger_txn_ids: [b],
              })
              applyMultiManualMatchResult([a], [b], res)
              usedBank.add(a)
              usedBank.add(b)
              bankBankApplied += 1
              applied += 1
            } catch {
              failed += 1
            }
          }
        }
        for (const id of bankIds.filter(x => !usedBank.has(x))) {
          if (aiMatchAbortRef.current) break
          const gl = (bankMap.get(id)?.accountCategory ?? '').trim()
          if (!gl) continue
          try {
            const res = await reconciliationApi.glOnlyMatch({ bank_txn_ids: [id] })
            applyMultiManualMatchResult([id], [], res)
            usedBank.add(id)
            glOnlyApplied += 1
            applied += 1
          } catch {
            failed += 1
          }
        }
      }

      if (!aiMatchAbortRef.current) {
        await refreshGroups()
        const failNote = failed ? ` ${failed} apply failure(s).` : ''
        const skipNote = skippedUnequal ? ` Skipped ${skippedUnequal} unequal-amount pair(s).` : ''
        const fbNote =
          bankBankApplied || glOnlyApplied
            ? ` Fallbacks: ${bankBankApplied} bank↔bank, ${glOnlyApplied} GL-only.`
            : ''
        setProgress(
          `Done. Matched ${applied} pair(s)${batches.length ? ` across ${batches.length} batch(es)` : ''}.${failNote}${skipNote}${fbNote}`,
        )
        if (mountedRef.current) {
          state.setStatusText(`AI match applied ${applied} pair(s).`)
          state.setAiMessages(prev =>
            upsertReconResultReviewMessage(
              prev,
              state.reconMatchedGroupsRef.current.map(g => g.id),
            ),
          )
        }
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setProgress(msg)
      if (mountedRef.current) state.setStatusText(msg)
    } finally {
      if (mountedRef.current) state.setAiThinking(false)
    }
  }, [applyMultiManualMatchResult, bankMap, bankRows, companyId, ledgerMap, ledgerRows, refreshGroups, state])

  const canAiMatch = useMemo(() => {
    return (
      resolveAiMatchCandidates(
        state.selectedBankIds,
        state.selectedLedgerIds,
        bankRows,
        ledgerRows,
        bankMap,
        ledgerMap,
      ) !== null
    )
  }, [state.selectedBankIds, state.selectedLedgerIds, bankRows, ledgerRows, bankMap, ledgerMap])

  const aiMatchHint = useMemo(() => {
    if (bankRows.length === 0 && ledgerRows.length === 0) {
      return 'Load bank or ledger rows for AI Match.'
    }
    if (!state.selectedBankIds.length && !state.selectedLedgerIds.length) {
      return 'Select rows on at least one side for AI Match.'
    }
    if (state.selectedBankIds.length && !state.selectedLedgerIds.length && ledgerRows.length === 0) {
      return 'Bank-only AI Match: equal-amount bank↔bank, then bank+GL → GL-only.'
    }
    if (bankRows.length === 0 || ledgerRows.length === 0) {
      return 'Both bank and ledger panels need rows for AI Match (or select bank-only).'
    }
    const resolved = resolveAiMatchCandidates(
      state.selectedBankIds,
      state.selectedLedgerIds,
      bankRows,
      ledgerRows,
      bankMap,
      ledgerMap,
    )
    if (!resolved) return 'No matching-currency rows on the other side for AI Match.'
    if (!state.selectedBankIds.length || !state.selectedLedgerIds.length) {
      return 'Unselected side uses same-currency visible rows.'
    }
    return null
  }, [state.selectedBankIds, state.selectedLedgerIds, bankRows, ledgerRows, bankMap, ledgerMap])

  const buildReconAiContext = useCallback((): Record<string, unknown> => {
    const cap = 24
    const sampleTxn = (t: ReconRawTxn) => ({
      id: t.id,
      reference: t.reference,
      amount: t.amount,
      dr_cr: t.dr_cr,
      currency: t.currency,
      bank_date: t.bank_date ?? t.book_date,
      recordMode: t.recordMode,
    })
    const allowedBank = cappedAllowTxnIds(
      state.selectedBankIds,
      state.reconUnmatchedTxns.bank,
      RECON_AI_ALLOWED_ID_CAP,
    )
    const allowedLedger = cappedAllowTxnIds(
      state.selectedLedgerIds,
      state.reconUnmatchedTxns.ledger,
      RECON_AI_ALLOWED_ID_CAP,
    )
    return {
      summary: {
        unmatched_bank_count: state.reconUnmatchedTxns.bank.length,
        unmatched_ledger_count: state.reconUnmatchedTxns.ledger.length,
        matched_groups_count: state.reconMatchedGroups.length,
        selected_bank_count: state.selectedBankIds.length,
        selected_ledger_count: state.selectedLedgerIds.length,
        allowed_bank_txn_ids_truncated: allowedBank.truncated,
        allowed_ledger_txn_ids_truncated: allowedLedger.truncated,
      },
      selected: {
        bank: state.selectedBankIds.map(id => bankMap.get(id)).filter(Boolean),
        ledger: state.selectedLedgerIds.map(id => ledgerMap.get(id)).filter(Boolean),
      },
      unmatched_samples: {
        bank: state.reconUnmatchedTxns.bank.slice(0, cap).map(sampleTxn),
        ledger: state.reconUnmatchedTxns.ledger.slice(0, cap).map(sampleTxn),
      },
      matched_groups_summary: state.reconMatchedGroups.map(g => ({
        group_id: g.id,
        bank_txn_count: normalizeReconTxnIdList(g.bank_txn_ids).length,
        ledger_txn_count: normalizeReconTxnIdList(g.ledger_txn_ids).length,
        bank_total: g.bank_total,
        ledger_total: g.ledger_total,
        difference: g.difference,
      })),
      matched_gl_summary: state.reconMatchedGroups.map(g => {
        const meta = state.glJournalMetaByGroupId[g.id]
        return {
          group_id: g.id,
          voucher_no: meta?.voucher_no ?? state.glVoucherNoByGroupId[g.id] ?? '',
          status: meta?.status ?? state.glStatusByGroupId[g.id] ?? '',
          journal_id: meta?.journal_id ?? null,
          draft_lines: meta?.lines ?? [],
        }
      }),
      allowed_bank_txn_ids: allowedBank.ids,
      allowed_ledger_txn_ids: allowedLedger.ids,
      allowed_group_ids: state.reconMatchedGroups.map(g => g.id),
    }
  }, [bankMap, ledgerMap, state])

  const sendAiChat = useCallback(
    async (message: string) => {
      const trimmed = message.trim()
      if (!trimmed || !companyId) return
      const userMsg: ReconAiMessage = { id: `u-${Date.now()}`, role: 'user', content: trimmed }
      const thinkingId = `think-${Date.now()}`
      state.setAiMessages(prev => [...prev, userMsg, { id: thinkingId, role: 'assistant', content: 'Thinking…' }])
      state.setAiThinking(true)
      const taskId = reconAiChatTaskId(companyId) ?? `${RECON_SESSION_ID}-default`
      let storageKey: string | null = null
      try {
        const chatPayload = {
          session_id: `${taskId}_RECON`,
          mode: 'RECON',
          message: trimmed,
          context: {
            transactions: [],
            coa: coaList.map(c => ({
              code: c.code,
              name_en: c.name_en,
              name_zh: c.name_zh,
              category_type: c.category_type,
            })),
            recon: buildReconAiContext(),
          },
        }
        const { job_id } = await api.createAiChatBackgroundJob(chatPayload, companyId)
        storageKey = BG_JOB_STORAGE_PREFIX + job_id
        const meta: ErpReconAiChatJobMeta = {
          kind: ERP_RECON_AI_CHAT_JOB_KIND,
          companyId,
          taskId,
          progressMessageId: thinkingId,
        }
        localStorage.setItem(storageKey, JSON.stringify(meta))
        trackTabBackgroundJob(job_id)
        let result: { reply?: string; recon_actions?: ReconAiAction[] }
        try {
          result = await api.waitForBackgroundJob(job_id, { companyId })
        } finally {
          untrackTabBackgroundJob(job_id)
        }
        const next = applyAiChatResultToStoredMessages(companyId, thinkingId, result)
        if (mountedRef.current) state.setAiMessages(next)
        if (storageKey) localStorage.removeItem(storageKey)
      } catch (e) {
        const err = e instanceof Error ? e.message : String(e)
        const next = applyAiChatResultToStoredMessages(companyId, thinkingId, null, err)
        if (mountedRef.current) state.setAiMessages(next)
        if (storageKey) localStorage.removeItem(storageKey)
      } finally {
        if (mountedRef.current) state.setAiThinking(false)
      }
    },
    [buildReconAiContext, coaList, companyId, state],
  )

  const handleApplyReconAiActions = useCallback(
    async (messageId: string) => {
      const msg = state.aiMessages.find(m => m.id === messageId)
      if (!msg?.reconActions?.length) return
      try {
        const glRefetchGroupIds: string[] = []
        const glSeedByGroupId: Record<string, GlJournalPayload> = {}
        for (const act of msg.reconActions) {
          const op = (act.op || '').toLowerCase()
          if (op === 'match' && act.bank_txn_ids?.length && act.ledger_txn_ids?.length) {
            const res = await reconciliationApi.multiManualMatch({
              bank_txn_ids: act.bank_txn_ids,
              ledger_txn_ids: act.ledger_txn_ids,
            })
            applyMultiManualMatchResult(act.bank_txn_ids, act.ledger_txn_ids, res)
          } else if (op === 'ledger_pending' && act.ledger_txn_ids?.length) {
            const res = await reconciliationApi.ledgerPendingMatch({ ledger_txn_ids: act.ledger_txn_ids })
            applyMultiManualMatchResult([], act.ledger_txn_ids, res)
          } else if (op === 'unmatch' && act.group_id) {
            await reconciliationApi.glDeleteDraftByGroup(act.group_id)
            state.setReconMatchedGroups(prev => prev.filter(g => g.id !== act.group_id))
          } else if (op === 'gl_draft_patch' && act.group_id) {
            let journalId = act.journal_id?.trim() || ''
            if (!journalId) {
              journalId = state.glJournalMetaByGroupId[act.group_id]?.journal_id ?? ''
              if (!journalId) {
                const jr = await reconciliationApi.glGetByGroup(act.group_id)
                journalId = jr.journal?.id ?? ''
              }
            }
            if (!journalId) throw new Error('No journal for this reconciliation group.')
            const lines = (act.gl_lines ?? []).map(ln => {
              const o: { id?: string; account_code?: string; debit?: number; credit?: number; memo?: string } = {}
              const lid = String(ln.line_id ?? '').trim()
              if (lid) o.id = lid
              if (ln.account_code) o.account_code = String(ln.account_code).trim()
              if (ln.memo != null) o.memo = String(ln.memo)
              if (ln.debit != null) o.debit = Number(ln.debit)
              if (ln.credit != null) o.credit = Number(ln.credit)
              return o
            })
            const patched = await reconciliationApi.glPatchJournal(journalId, {
              lines: lines.length ? lines : undefined,
              deleted_line_ids: act.deleted_line_ids,
            })
            state.setGlJournalMetaByGroupId(prev => ({
              ...prev,
              [act.group_id!]: {
                journal_id: patched.id,
                voucher_no: patched.voucher_no ?? '',
                status: String(patched.status ?? ''),
                lines: patched.lines ?? [],
              },
            }))
            glRefetchGroupIds.push(act.group_id)
            glSeedByGroupId[act.group_id] = patched
          }
        }
        if (glRefetchGroupIds.length) {
          state.setGlJournalRefetchSignal(prev => ({
            nonce: (prev?.nonce ?? 0) + 1,
            groupIds: [...new Set(glRefetchGroupIds)],
          }))
          if (Object.keys(glSeedByGroupId).length) {
            state.setGlApplyPatchSeeds(prev => ({
              nonce: (prev?.nonce ?? 0) + 1,
              byGroupId: glSeedByGroupId,
            }))
          }
        }
        await refreshGroups()
        state.setAiMessages(prev => [
          ...prev.map(m => (m.id === messageId ? { ...m, reconActionsPending: false, reconActions: undefined } : m)),
          { id: `applied-${Date.now()}`, role: 'assistant', content: 'Applied AI reconciliation actions.' },
        ])
      } catch (e) {
        const err = e instanceof Error ? e.message : String(e)
        state.setAiMessages(prev => [...prev, { id: `apply-err-${Date.now()}`, role: 'assistant', content: `Apply failed: ${err}` }])
      }
    },
    [applyMultiManualMatchResult, refreshGroups, state],
  )

  const handleResultPageChange = useCallback(
    (messageId: string, pageIndex: number) => {
      state.setAiMessages(prev =>
        prev.map(m =>
          m.id === messageId && m.resultReview
            ? {
                ...m,
                resultReview: {
                  ...m.resultReview,
                  pageIndex: Math.max(0, Math.min(pageIndex, m.resultReview.groupIds.length - 1)),
                },
              }
            : m,
        ),
      )
    },
    [state],
  )

  /** Cancel a matched group: dissolve it, restore UNRECONCILED, return rows to the unmatched pool. */
  const handleCancelMatchedGroup = useCallback(
    async (groupId: string) => {
      const grp = state.reconMatchedGroupsRef.current.find(g => g.id === groupId)
      if (!grp) {
        state.setStatusText('Group not found.')
        return
      }
      const glStatus = (state.glStatusByGroupId[groupId] || '').toLowerCase()
      if (glStatus === 'posted') {
        state.setStatusText('Unpost the GL journal before cancelling this match.')
        return
      }

      let bankIds = normalizeReconTxnIdList(grp.bank_txn_ids)
      let ledgerIds = normalizeReconTxnIdList(grp.ledger_txn_ids)
      // Local card can show 0 members while DB still has match rows (or vice versa).
      if (!bankIds.length && !ledgerIds.length) {
        try {
          const { groups } = await reconciliationApi.fetchGroups()
          const fresh = (groups ?? []).find((g: { id?: string }) => g.id === groupId) as
            | { bank_txn_ids?: unknown; ledger_txn_ids?: unknown }
            | undefined
          if (fresh) {
            bankIds = normalizeReconTxnIdList(fresh.bank_txn_ids)
            ledgerIds = normalizeReconTxnIdList(fresh.ledger_txn_ids)
          }
        } catch {
          /* keep local ids */
        }
      }
      const members: { txn_id: string; txn_type: 'bank' | 'ledger' }[] = [
        ...bankIds.map(txn_id => ({ txn_id, txn_type: 'bank' as const })),
        ...ledgerIds.map(txn_id => ({ txn_id, txn_type: 'ledger' as const })),
      ]

      const apiErrors: string[] = []
      try {
        if (!members.length) {
          // Orphan (0 members): existing group-unmatch-member dissolves when no rows remain.
          // dissolve-group is preferred but may 404 until backend reload.
          try {
            await reconciliationApi.groupUnmatchMember({
              group_id: groupId,
              txn_id: '__orphan_cancel__',
              txn_type: 'bank',
              reason: 'cancel_orphan_recon_result',
            })
          } catch (e) {
            apiErrors.push(e instanceof Error ? e.message : String(e))
            try {
              await reconciliationApi.dissolveGroup({
                group_id: groupId,
                reason: 'cancel_orphan_recon_result',
              })
            } catch (e2) {
              apiErrors.push(e2 instanceof Error ? e2.message : String(e2))
            }
          }
          try {
            await reconciliationApi.glDeleteDraftByGroup(groupId)
          } catch {
            /* draft may already be gone */
          }
        } else {
          let dissolved = false
          for (const m of members) {
            try {
              const res = await reconciliationApi.groupUnmatchMember({
                group_id: groupId,
                txn_id: m.txn_id,
                txn_type: m.txn_type,
                reason: 'cancel_recon_result',
              })
              if (res.group_dissolved) {
                dissolved = true
                break
              }
            } catch (e) {
              apiErrors.push(e instanceof Error ? e.message : String(e))
            }
          }
          if (!dissolved) {
            try {
              await reconciliationApi.dissolveGroup({
                group_id: groupId,
                reason: 'cancel_recon_result_fallback',
              })
              dissolved = true
            } catch (e) {
              apiErrors.push(e instanceof Error ? e.message : String(e))
            }
            try {
              await reconciliationApi.glDeleteDraftByGroup(groupId)
            } catch {
              /* ignore */
            }
          }
        }

        const bankSnaps = (grp.bank_txn_snapshots ?? []).filter(Boolean) as ReconRawTxn[]
        const ledgerSnaps = (grp.ledger_txn_snapshots ?? []).filter(Boolean) as ReconRawTxn[]
        state.setReconUnmatchedTxns(prev => {
          const bankSeen = new Set(prev.bank.map(t => String(t.id)))
          const ledgerSeen = new Set(prev.ledger.map(t => String(t.id)))
          const bankAdd = bankSnaps.filter(t => {
            const id = String(t.id ?? '')
            if (!id || bankSeen.has(id)) return false
            bankSeen.add(id)
            return true
          })
          const ledgerAdd = ledgerSnaps.filter(t => {
            const id = String(t.id ?? '')
            if (!id || ledgerSeen.has(id)) return false
            ledgerSeen.add(id)
            return true
          })
          return {
            bank: [...prev.bank, ...bankAdd],
            ledger: [...prev.ledger, ...ledgerAdd],
          }
        })

        const stripCancelled = (groups: typeof state.reconMatchedGroupsRef.current) =>
          groups.filter(g => g.id !== groupId)

        let nextGroups = stripCancelled(state.reconMatchedGroupsRef.current)
        state.reconMatchedGroupsRef.current = nextGroups
        state.setReconMatchedGroups(nextGroups)

        // Badge falls back to row.status when matchedIds no longer contains the id —
        // clear stale "matched" on both grids immediately (no Refresh required).
        const bankIdSet = new Set(bankIds.map(String))
        const ledgerIdSet = new Set(ledgerIds.map(String))
        if (bankIdSet.size) {
          setBankAll(prev =>
            prev.map(r => (bankIdSet.has(r.id) ? { ...r, status: 'unreconciled' } : r)),
          )
        }
        if (ledgerIdSet.size) {
          setLedgerAll(prev =>
            prev.map(r => (ledgerIdSet.has(r.id) ? { ...r, status: 'unreconciled' } : r)),
          )
        }

        state.setGlJournalMetaByGroupId(prev => {
          const { [groupId]: _removed, ...rest } = prev
          return rest
        })
        state.setGlStatusByGroupId(prev => {
          const { [groupId]: _removed, ...rest } = prev
          return rest
        })
        state.setGlVoucherNoByGroupId(prev => {
          const { [groupId]: _removed, ...rest } = prev
          return rest
        })
        state.setAiMessages(prev => upsertReconResultReviewMessage(prev, nextGroups.map(g => g.id)))
        state.setStatusText(
          apiErrors.length
            ? `Match cancelled locally. Server note: ${apiErrors[apiErrors.length - 1]}`
            : 'Match cancelled. Transactions restored to unreconciled.',
        )
        await refreshGroups()
        // refreshGroups can resurrect orphans if server dissolve failed — strip again.
        nextGroups = stripCancelled(state.reconMatchedGroupsRef.current)
        if (nextGroups.length !== state.reconMatchedGroupsRef.current.length) {
          state.reconMatchedGroupsRef.current = nextGroups
          state.setReconMatchedGroups(nextGroups)
          state.setAiMessages(prev => upsertReconResultReviewMessage(prev, nextGroups.map(g => g.id)))
        }
        // Await sync so Books matched_id clears; sync keeps unreconciled-in-module
        // rows (same IDs) so rematch does not hit "No valid transactions found".
        if (companyId) {
          try {
            await syncModulesToRecon(companyId)
          } catch (err) {
            console.warn('[RECON] post-cancel module sync failed:', err)
          }
        }
        void persistSession()
      } catch (e) {
        state.setStatusText(e instanceof Error ? e.message : String(e))
      }
    },
    [companyId, persistSession, refreshGroups, state],
  )

  const toggleBankSelection = useCallback(
    (id: string) => {
      state.setSelectedBankIds(prev => (prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]))
    },
    [state],
  )

  const toggleBankSelectionAll = useCallback(
    (ids: string[], select: boolean) => {
      state.setSelectedBankIds(prev => {
        const next = new Set(prev)
        ids.forEach(id => (select ? next.add(id) : next.delete(id)))
        return [...next]
      })
    },
    [state],
  )

  const toggleLedgerSelection = useCallback(
    (id: string) => {
      state.setSelectedLedgerIds(prev => (prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]))
    },
    [state],
  )

  const toggleLedgerSelectionAll = useCallback(
    (ids: string[], select: boolean) => {
      state.setSelectedLedgerIds(prev => {
        const next = new Set(prev)
        ids.forEach(id => (select ? next.add(id) : next.delete(id)))
        return [...next]
      })
    },
    [state],
  )

  const poolBankTotal = useMemo(
    () => state.reconUnmatchedTxns.bank.reduce((s, t) => s + Number(t.amount ?? 0), 0),
    [state.reconUnmatchedTxns.bank],
  )
  const poolLedgerTotal = useMemo(
    () => state.reconUnmatchedTxns.ledger.reduce((s, t) => s + Number(t.amount ?? 0), 0),
    [state.reconUnmatchedTxns.ledger],
  )
  const variance = poolBankTotal - poolLedgerTotal
  const totalEligible = bankRows.length + ledgerRows.length
  const matchedCount = state.reconMatchedGroups.length

  return {
    ...state,
    bankRows,
    ledgerRows,
    bankRowById,
    ledgerRowById,
    bankAccounts,
    coaList,
    matchedTxnIds,
    loadTransactions,
    refreshGroups,
    needsSelection,
    scopedNav,
    handleMatch,
    handleAIMatch,
    canAiMatch,
    aiMatchHint,
    sendAiChat,
    handleApplyReconAiActions,
    handleResultPageChange,
    handleCancelMatchedGroup,
    buildReconAiContext,
    toggleBankSelection,
    toggleBankSelectionAll,
    toggleLedgerSelection,
    toggleLedgerSelectionAll,
    poolBankTotal,
    poolLedgerTotal,
    variance,
    totalEligible,
    matchedCount,
  }
}

export type ReconWorkspace = ReturnType<typeof useReconWorkspace>
