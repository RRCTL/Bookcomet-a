import type {
  BankTransaction,
  LedgerTransaction,
  AutoMatchResponse,
  UnmatchRequest,
  ChartOfAccountItem,
  MultiMatchRequest,
  MultiMatchResponse,
  GroupUnmatchMemberRequest,
  GroupUnmatchMemberResponse,
  ClearBankRequest,
  ClearBankResponse,
  GlOnlyMatchRequest,
  LedgerPendingMatchRequest,
} from '../types/reconciliation'
import { apiFetch } from './api'

function apiErrorDetailMessage(body: unknown, fallback: string): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const d = (body as { detail: unknown }).detail
    if (typeof d === 'string' && d.trim()) return d.trim()
    if (Array.isArray(d)) {
      const parts = d
        .map(item =>
          item && typeof item === 'object' && 'msg' in item && typeof (item as { msg: unknown }).msg === 'string'
            ? (item as { msg: string }).msg
            : JSON.stringify(item),
        )
        .filter(Boolean)
      if (parts.length) return parts.join('; ')
    }
  }
  return fallback
}

export const reconciliationApi = {
  async getChartOfAccounts(mode?: string): Promise<{ mode?: string; accounts: ChartOfAccountItem[] }> {
    const params = new URLSearchParams()
    if (mode) params.set('mode', mode)
    const query = params.toString()
    const response = await apiFetch(`/reconciliation/chart-of-accounts${query ? `?${query}` : ''}`)

    if (!response.ok) {
      throw new Error(`Failed to fetch chart of accounts: ${response.statusText}`)
    }

    return response.json()
  },

  async autoMatchSelected(
    bankTxnIds: string[],
    ledgerTxnIds: string[]
  ): Promise<AutoMatchResponse> {
    const response = await apiFetch('/reconciliation/auto-match-selected', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        bank_txn_ids: bankTxnIds,
        ledger_txn_ids: ledgerTxnIds,
      }),
    })

    if (!response.ok) {
      throw new Error(`Auto-match(selected) failed: ${response.statusText}`)
    }

    return response.json()
  },

  // Auto-match transactions
  // Import ledger transactions
  async importLedgerTransactions(
    rows: any[],
    importBatchId?: string,
    module?: string,
  ): Promise<{
    import_batch_id: string
    stored_count: number
    updated_count?: number
    created_rows?: Array<Record<string, any>>
  }> {
    const response = await apiFetch('/reconciliation/ledger-import', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        import_batch_id: importBatchId,
        module,
        rows,
      }),
    })

    if (!response.ok) {
      throw new Error(`Ledger import failed: ${response.statusText}`)
    }

    return response.json()
  },

  async importBankTransactions(
    rows: Record<string, unknown>[],
    importBatchId?: string,
  ): Promise<{ import_batch_id: string; stored_count: number; created_rows?: Array<Record<string, unknown>> }> {
    const response = await apiFetch('/reconciliation/bank-import', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        import_batch_id: importBatchId,
        rows,
      }),
    })

    if (!response.ok) {
      throw new Error(`Bank import failed: ${response.statusText}`)
    }

    return response.json()
  },

  /** Delete unreconciled recon rows that no longer exist in Books modules. */
  async purgeUnreconciled(req: {
    bank_txn_ids?: string[]
    ledger_txn_ids?: string[]
  }): Promise<{ purged_bank: number; purged_ledger: number }> {
    const response = await apiFetch('/reconciliation/purge-unreconciled', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        bank_txn_ids: req.bank_txn_ids ?? [],
        ledger_txn_ids: req.ledger_txn_ids ?? [],
      }),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error(apiErrorDetailMessage(err, response.statusText))
    }
    return response.json()
  },

  /** Permanently wipe all unreconciled bank/ledger rows (matched/partial kept). */
  async clearUnreconciledPool(req?: {
    bank?: boolean
    ledger?: boolean
  }): Promise<{ purged_bank: number; purged_ledger: number }> {
    const response = await apiFetch('/reconciliation/clear-unreconciled-pool', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        bank: req?.bank ?? true,
        ledger: req?.ledger ?? true,
      }),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error(apiErrorDetailMessage(err, response.statusText))
    }
    return response.json()
  },

  /** Delete every bank/ledger row not in keep lists (any status), so pool can mirror Books. */
  async purgeExceptKept(req: {
    keep_bank_txn_ids?: string[]
    keep_ledger_txn_ids?: string[]
  }): Promise<{ purged_bank: number; purged_ledger: number }> {
    const response = await apiFetch('/reconciliation/purge-except-kept', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        keep_bank_txn_ids: req.keep_bank_txn_ids ?? [],
        keep_ledger_txn_ids: req.keep_ledger_txn_ids ?? [],
      }),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error(apiErrorDetailMessage(err, response.statusText))
    }
    return response.json()
  },

  // Multi-to-multi manual match (1:1, 1:N, N:1, N:N)
  async multiManualMatch(req: MultiMatchRequest): Promise<MultiMatchResponse> {
    const response = await apiFetch('/reconciliation/multi-manual-match', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error((err as any).detail || response.statusText)
    }
    return response.json()
  },

  // Mark selected bank transactions as cleared without a ledger counterpart
  async clearBankTransactions(req: ClearBankRequest): Promise<ClearBankResponse> {
    const response = await apiFetch('/reconciliation/clear-bank-transactions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error((err as any).detail || response.statusText)
    }
    return response.json()
  },

  /** Bank + GL code (no AR/AP yet): GL:1 draft = cash + 1999; offset on bank until approve. */
  async glOnlyMatch(req: GlOnlyMatchRequest): Promise<ClearBankResponse> {
    const response = await apiFetch('/reconciliation/gl-only-match', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error(apiErrorDetailMessage(err, response.statusText))
    }
    return response.json()
  },

  /** Ledger-only (no bank yet): match to suspense / pending bank; GL draft follows. */
  async ledgerPendingMatch(req: LedgerPendingMatchRequest): Promise<ClearBankResponse> {
    const response = await apiFetch('/reconciliation/ledger-pending-match', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error((err as any).detail || response.statusText)
    }
    return response.json()
  },

  // Remove a single member from a multi-match group
  async groupUnmatchMember(req: GroupUnmatchMemberRequest): Promise<GroupUnmatchMemberResponse> {
    const response = await apiFetch('/reconciliation/group-unmatch-member', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error((err as any).detail || response.statusText)
    }
    return response.json()
  },

  /** Dissolve entire recon group (works for orphan 0-member groups too). */
  async dissolveGroup(req: { group_id: string; reason?: string }): Promise<{
    group_id: string
    group_dissolved: boolean
    remaining_members: number
    restored_bank_txn_ids?: string[]
    restored_ledger_txn_ids?: string[]
  }> {
    const response = await apiFetch('/reconciliation/dissolve-group', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ group_id: req.group_id, reason: req.reason || '' }),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error(apiErrorDetailMessage(err, response.statusText))
    }
    return response.json()
  },

  // Unmatch transactions
  async unmatch(data: UnmatchRequest): Promise<{ status: string; message: string }> {
    const response = await apiFetch(`/reconciliation/unmatch?match_id=${data.match_id}&reason=${encodeURIComponent(data.reason)}&user_id=${data.user_id || 'system'}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
    })
    
    if (!response.ok) {
      throw new Error(`Unmatch failed: ${response.statusText}`)
    }
    
    return response.json()
  },

  // Get all bank transactions
  async getBankTransactions(): Promise<BankTransaction[]> {
    const response = await apiFetch('/reconciliation/bank-transactions')
    
    if (!response.ok) {
      throw new Error(`Failed to fetch bank transactions: ${response.statusText}`)
    }
    
    return response.json()
  },

  // Get all ledger transactions
  async getLedgerTransactions(): Promise<LedgerTransaction[]> {
    const response = await apiFetch('/reconciliation/ledger-transactions')
    
    if (!response.ok) {
      throw new Error(`Failed to fetch ledger transactions: ${response.statusText}`)
    }
    
    return response.json()
  },

  // AI-powered duplicate detection + smart matching in one pass
  async aiMatch(
    bankTxnIds:   string[],
    ledgerTxnIds: string[],
    taskId?:      string,
  ): Promise<{
    duplicates: { txn_ids: string[]; reason: string; level: 1 | 2 | 3 | 4 }[]
    matches:    { bank_txn_id: string; ledger_txn_id: string; score: number; match_type: string; ai_reason: string }[]
    summary:    string
  }> {
    const response = await apiFetch('/reconciliation/ai-match', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      timeoutMs: 180_000,
      body:    JSON.stringify({
        bank_txn_ids:   bankTxnIds,
        ledger_txn_ids: ledgerTxnIds,
        task_id:        taskId,
      }),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      const detail = typeof err.detail === 'string' ? err.detail : response.statusText
      throw new Error(detail)
    }
    return response.json()
  },

  // ── Chart of Accounts CRUD ────────────────────────────────────────────────

  async createCoA(entry: {
    code: string
    name_en: string
    name_zh?: string
    category_type: string
    allowed_modes: string[]
  }): Promise<{ status: string; account: ChartOfAccountItem }> {
    const response = await apiFetch('/reconciliation/chart-of-accounts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(entry),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error((err as any).detail || response.statusText)
    }
    return response.json()
  },

  async updateCoA(
    code: string,
    patch: { name_en?: string; name_zh?: string; category_type?: string; allowed_modes?: string[] }
  ): Promise<{ status: string; account: ChartOfAccountItem }> {
    const response = await apiFetch(`/reconciliation/chart-of-accounts/${encodeURIComponent(code)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error((err as any).detail || response.statusText)
    }
    return response.json()
  },

  async deleteCoA(
    code: string,
    referencedCodes?: string[]
  ): Promise<{ status: string; code: string }> {
    const response = await apiFetch(`/reconciliation/chart-of-accounts/${encodeURIComponent(code)}`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ referenced_codes: referencedCodes ?? [] }),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error((err as any).detail || response.statusText)
    }
    return response.json()
  },

  async deployAccountCodes(
    transactions: Array<{
      id_number?: string
      date?: string
      amount?: number | null
      payer?: string
      payee?: string
      memo?: string
      transaction_type?: string
      category?: string
    }>,
    mode: string,
    companyProfile?: string
  ): Promise<{ results: Array<{ id_number: string; suggested_code: string | null; confidence: number }> }> {
    const response = await apiFetch('/reconciliation/account-codes/deploy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transactions, mode, company_profile: companyProfile }),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error((err as any).detail || response.statusText)
    }
    return response.json()
  },

  async getPartialTransactions(): Promise<{ partial_transactions: any[]; count: number }> {
    const response = await apiFetch('/reconciliation/partial-transactions')
    if (!response.ok) {
      throw new Error(`Failed to fetch partial transactions: ${response.statusText}`)
    }
    return response.json()
  },

  /** Fetch all reconciliation groups with full transaction details from the DB.
   *  Used on RECON mode entry to reconstruct matched-results display after a
   *  browser refresh, independent of localStorage state. */
  async fetchGroups(): Promise<{ groups: any[] }> {
    const response = await apiFetch('/reconciliation/groups')
    if (!response.ok) {
      throw new Error(`Failed to fetch groups: ${response.statusText}`)
    }
    return response.json()
  },

  /** Fetch the current RECON unmatched pool (session) from the DB.
   *  Used on RECON mode entry to restore workspace after refresh or on another device. */
  async fetchSession(): Promise<{
    bank_txns: any[]
    ledger_txns: any[]
    bank_rows: any[]
    ledger_rows: any[]
    workspace?: Record<string, unknown> | null
  }> {
    const response = await apiFetch('/reconciliation/session')
    if (!response.ok) {
      throw new Error(`Failed to fetch session: ${response.statusText}`)
    }
    return response.json()
  },

  /** Hard-reset ALL reconciliation data in the DB for the current company.
   *  Deletes groups, matches, session rows and resets all transaction statuses to UNRECONCILED.
   *  Called when the user deletes a task while RECON state is active. */
  async resetRecon(): Promise<{ status: string; deleted_groups: number; deleted_matches: number; deleted_session: number }> {
    const response = await apiFetch('/reconciliation/reset', { method: 'DELETE' })
    if (!response.ok) {
      throw new Error(`Failed to reset reconciliation: ${response.statusText}`)
    }
    return response.json()
  },

  /** Replace the RECON unmatched pool in the DB.
   *  Called after every Match run and whenever the pool changes (unmatch / group delete). */
  async saveSession(payload: {
    entries?: Array<{
      txn_id: string
      txn_type: 'bank' | 'ledger'
      raw_txn_data?: any
      display_row?: any
    }>
    workspace?: Record<string, unknown> | null
  }): Promise<{ status: string; count: number }> {
    const response = await apiFetch('/reconciliation/session', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!response.ok) {
      throw new Error(`Failed to save session: ${response.statusText}`)
    }
    return response.json()
  },

  /** GL journal — recon group draft or module/manual journal */
  async glEnsureDraft(groupId: string): Promise<GlJournalPayload> {
    const response = await apiFetch(
      `/reconciliation/gl/ensure-draft?group_id=${encodeURIComponent(groupId)}`,
      { method: 'POST' }
    )
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error((err as any).detail || response.statusText)
    }
    return response.json()
  },

  async glList(params?: {
    status?: string
    currency?: string
    source?: string
    date_from?: string
    date_to?: string
    limit?: number
  }): Promise<{ journals: GlJournalListItem[] }> {
    const q = new URLSearchParams()
    if (params?.status) q.set('status', params.status)
    if (params?.currency) q.set('currency', params.currency)
    if (params?.source) q.set('source', params.source)
    if (params?.date_from) q.set('date_from', params.date_from.slice(0, 10))
    if (params?.date_to) q.set('date_to', params.date_to.slice(0, 10))
    if (params?.limit != null) q.set('limit', String(params.limit))
    const qs = q.toString()
    const response = await apiFetch(`/reconciliation/gl${qs ? `?${qs}` : ''}`)
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error((err as any).detail || response.statusText)
    }
    return response.json()
  },

  async glCreateManual(body: {
    journal_date: string
    currency: string
    narration?: string | null
    voucher_no?: string | null
    lines: Array<{ account_code: string; debit?: number; credit?: number; memo?: string | null }>
  }): Promise<GlJournalPayload> {
    const response = await apiFetch('/reconciliation/gl/manual', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error((err as any).detail || response.statusText)
    }
    return response.json()
  },

  async glEnsureDraftTxn(body: {
    bank_txn_id?: string | null
    ledger_txn_id?: string | null
  }): Promise<GlJournalPayload> {
    const response = await apiFetch('/reconciliation/gl/ensure-draft-txn', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error((err as any).detail || response.statusText)
    }
    return response.json()
  },

  async glGetByGroup(groupId: string): Promise<{ journal: GlJournalPayload | null }> {
    const response = await apiFetch(`/reconciliation/gl/by-group/${encodeURIComponent(groupId)}`)
    if (!response.ok) throw new Error(`GL fetch failed: ${response.statusText}`)
    return response.json()
  },

  async glListPosted(limit = 100): Promise<{ journals: GlJournalPayload[] }> {
    const response = await apiFetch(`/reconciliation/gl/posted?limit=${limit}`)
    if (!response.ok) throw new Error(`GL list failed: ${response.statusText}`)
    return response.json()
  },

  /** Draft + posted HKD journals in journal_date range (REPORT mode TB). */
  async glListForReport(dateFrom: string, dateTo: string, limit = 500): Promise<{ journals: GlJournalPayload[] }> {
    const q = new URLSearchParams({ date_from: dateFrom.slice(0, 10), date_to: dateTo.slice(0, 10), limit: String(limit) })
    const response = await apiFetch(`/reconciliation/gl/for-report?${q}`)
    if (!response.ok) throw new Error(`GL for-report failed: ${response.statusText}`)
    return response.json()
  },

  async glPatchJournal(
    journalId: string,
    body: {
      journal_date?: string
      balancing_account_code?: string | null
      deleted_line_ids?: string[]
      lines?: Array<{
        id?: string
        account_code?: string
        debit?: number
        credit?: number
        memo?: string
      }>
    }
  ): Promise<GlJournalPayload> {
    const response = await apiFetch(`/reconciliation/gl/${encodeURIComponent(journalId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error((err as any).detail || response.statusText)
    }
    return response.json()
  },

  async glPostJournal(
    journalId: string,
    opts?: { confirmBankCreate?: boolean },
  ): Promise<GlJournalPayload> {
    const response = await apiFetch(`/reconciliation/gl/${encodeURIComponent(journalId)}/post`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm_bank_create: Boolean(opts?.confirmBankCreate) }),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error(apiErrorDetailMessage(err, response.statusText))
    }
    return response.json()
  },

  async glUnpostToDraft(journalId: string): Promise<GlJournalPayload> {
    const response = await apiFetch(`/reconciliation/gl/unpost`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ journal_id: journalId }),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error(apiErrorDetailMessage(err, response.statusText))
    }
    return response.json()
  },

  async glReverseDraft(journalId: string): Promise<GlJournalPayload> {
    const response = await apiFetch(`/reconciliation/gl/${encodeURIComponent(journalId)}/reverse-draft`, { method: 'POST' })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error(apiErrorDetailMessage(err, response.statusText))
    }
    return response.json()
  },

  async glDeleteDraftByGroup(groupId: string): Promise<{ deleted: number }> {
    const response = await apiFetch(`/reconciliation/gl/draft-by-group/${encodeURIComponent(groupId)}`, { method: 'DELETE' })
    if (!response.ok) return { deleted: 0 }
    return response.json()
  },

  async bulkTxnAccountCategory(body: {
    updates: Array<{ source: 'bank' | 'ledger'; txn_id: string; account_category: string }>
    rebuild_draft_journals?: boolean
  }): Promise<{ updated_count: number; rebuilt_group_ids: string[] }> {
    const response = await apiFetch('/reconciliation/transactions/account-category-bulk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        updates: body.updates,
        rebuild_draft_journals: body.rebuild_draft_journals === true,
      }),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error((err as any).detail || response.statusText)
    }
    return response.json()
  },

  async bulkLedgerDocType(body: {
    updates: Array<{ txn_id: string; doc_type: string }>
    rebuild_draft_journals?: boolean
  }): Promise<{ updated_count: number; rebuilt_group_ids: string[] }> {
    const response = await apiFetch('/reconciliation/transactions/ledger-doc-type-bulk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        updates: body.updates,
        rebuild_draft_journals: body.rebuild_draft_journals === true,
      }),
    })
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error((err as any).detail || response.statusText)
    }
    return response.json()
  },

  async glSyncJournalLinesToTransactions(journalId: string): Promise<{
    sync: {
      bank: Record<string, string>
      ledger: Record<string, string>
      bank_updates?: Array<Record<string, unknown>>
      ledger_updates?: Array<Record<string, unknown>>
      module_fields_rewritten?: boolean
    }
    journal: GlJournalPayload
  }> {
    const response = await apiFetch(
      `/reconciliation/gl/${encodeURIComponent(journalId)}/sync-lines-to-transactions`,
      { method: 'POST' },
    )
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: response.statusText }))
      throw new Error((err as any).detail || response.statusText)
    }
    return response.json()
  },
}

export interface GlJournalLinePayload {
  id: string
  line_no: number
  account_code: string
  debit: number
  credit: number
  memo?: string | null
  bank_txn_id?: string | null
  ledger_txn_id?: string | null
}

export interface GlJournalPayload {
  id: string
  reconciliation_group_id: string | null
  status: string
  journal_date: string | null
  currency: string
  voucher_no: string
  narration?: string | null
  balancing_account_code?: string | null
  reversal_of_journal_id?: string | null
  source?: string | null
  created_at?: string | null
  posted_at?: string | null
  lines: GlJournalLinePayload[]
  total_debit: number
  total_credit: number
  balanced: boolean
}

/** Company journal index row (GET /reconciliation/gl). */
export interface GlJournalListItem extends GlJournalPayload {
  recon_status?: string
  module?: string | null
  bank_txn_ids?: string[]
  ledger_txn_ids?: string[]
}
