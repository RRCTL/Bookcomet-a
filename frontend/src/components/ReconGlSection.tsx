import { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import { reconciliationApi, type GlJournalPayload } from '../services/reconciliation'
import type { MatchedGroupRow } from './ReconciliationTable'
import type { ChartOfAccountItem } from '../types/reconciliation'
import { ReconGlJournalEditModal } from './ReconGlJournalEditModal'
import './ReconGlSection.css'

function fmt(n: number) {
  return (Math.round(n * 100) / 100).toLocaleString('en-HK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** Same keys the AI Apply seed merge uses — bulk/refetch must prefer seed or groupIdsKey reload overwrites PATCH. */
function mergeKeysForAiSeed(gidKey: string, seeded: GlJournalPayload, groups: MatchedGroupRow[]): Set<string> {
  const mergeKeys = new Set<string>([gidKey])
  const rgu = (seeded.reconciliation_group_id || '').trim()
  if (rgu) mergeKeys.add(rgu)
  for (const grp of groups) {
    const pid = grp.id.trim()
    if (pid === gidKey.trim() || (rgu && pid === rgu)) mergeKeys.add(grp.id)
    else if (pid.toLowerCase() === gidKey.trim().toLowerCase()) mergeKeys.add(grp.id)
    else if (rgu && pid.toLowerCase() === rgu.toLowerCase()) mergeKeys.add(grp.id)
  }
  return mergeKeys
}

function pickJournalWithAiApplySeed(
  gid: string,
  fromApi: GlJournalPayload,
  pack: { byGroupId: Record<string, GlJournalPayload> } | null | undefined,
  groups: MatchedGroupRow[],
): GlJournalPayload {
  if (!pack?.byGroupId) return fromApi
  for (const [gidKey, seeded] of Object.entries(pack.byGroupId)) {
    if (mergeKeysForAiSeed(gidKey, seeded, groups).has(gid)) return seeded
  }
  return fromApi
}

export function ReconGlSection({
  matchedGroups,
  coaList,
  onGlAccountCodesSynced,
  glJournalRefetchSignal,
  glApplyPatchSeeds = null,
  onPrimaryJournalStatusByGroup,
  onGlVoucherNoByGroup,
  scrollTargetGroupId = null,
  onScrollTargetConsumed,
  actionRequest = null,
  onActionRequestConsumed,
  collapseTables = false,
}: {
  matchedGroups: MatchedGroupRow[]
  coaList: ChartOfAccountItem[]
  onGlAccountCodesSynced?: (sync: { bank: Record<string, string>; ledger: Record<string, string> }) => void
  glJournalRefetchSignal?: { nonce: number; groupIds: string[] } | null
  /** Runs after glJournalRefetchEffect so PATCH payloads win over racing GETs / OCR-only refetch. */
  glApplyPatchSeeds?: { nonce: number; byGroupId: Record<string, GlJournalPayload> } | null
  /** draft | posted per reconciliation group — used to lock OCR account codes after GL approval */
  onPrimaryJournalStatusByGroup?: (statusByGroupId: Record<string, string>) => void
  /** group_id → GL-000006 when a journal is loaded or draft-ensured (fills gaps vs GET-by-group-only) */
  onGlVoucherNoByGroup?: (voucherByGroupId: Record<string, string>) => void
  /** Deep link from OCR: scroll this group's card into view once */
  scrollTargetGroupId?: string | null
  onScrollTargetConsumed?: () => void
  /** Drive Approve / Edit / Unpost from AI MC choices */
  actionRequest?: { groupId: string; action: 'approve' | 'edit' | 'unpost' } | null
  onActionRequestConsumed?: () => void
  /** When true, hide journal list UI (AI panel is the primary review surface; modals still work). */
  collapseTables?: boolean
}) {
  const [loadingInitial, setLoadingInitial] = useState(false)
  const [loadingGid, setLoadingGid] = useState<string | null>(null)
  const [errMsg, setErrMsg] = useState<string | null>(null)
  const [journalByGroup, setJournalByGroup] = useState<Record<string, GlJournalPayload>>({})
  const [loadErrByGroup, setLoadErrByGroup] = useState<Record<string, string>>({})
  const [postedList, setPostedList] = useState<GlJournalPayload[]>([])
  const [editModalGroupId, setEditModalGroupId] = useState<string | null>(null)
  const [approveConfirmGroupId, setApproveConfirmGroupId] = useState<string | null>(null)
  const [reverseConfirmGroupId, setReverseConfirmGroupId] = useState<string | null>(null)
  const [suspenseReverseConfirmGroupId, setSuspenseReverseConfirmGroupId] = useState<string | null>(null)

  /** Latest generation per group — drops stale GET/ensure results so bulk load never overwrites a fresher refetch (e.g. after AI Apply PATCH). */
  const groupFetchGenRef = useRef<Record<string, number>>({})
  const beginGroupJournalFetch = (gid: string) => {
    const n = (groupFetchGenRef.current[gid] ?? 0) + 1
    groupFetchGenRef.current[gid] = n
    return n
  }
  const isSupersededJournalFetch = (gid: string, gen: number) =>
    groupFetchGenRef.current[gid] !== gen

  const groupIdsKey = useMemo(
    () => matchedGroups.map(g => g.id).sort().join(','),
    [matchedGroups],
  )

  const glApplyPatchSeedsRef = useRef(glApplyPatchSeeds)
  glApplyPatchSeedsRef.current = glApplyPatchSeeds
  const matchedGroupsRef = useRef(matchedGroups)
  matchedGroupsRef.current = matchedGroups

  useEffect(() => {
    if (!scrollTargetGroupId || !matchedGroups.some(g => g.id === scrollTargetGroupId)) return
    const t = window.setTimeout(() => {
      const safe = typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(scrollTargetGroupId) : scrollTargetGroupId
      const el = document.querySelector(`[data-recon-group-id="${safe}"]`)
      if (el instanceof HTMLElement) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        el.classList.add('recon-gl-group--highlight')
        window.setTimeout(() => el.classList.remove('recon-gl-group--highlight'), 2600)
      }
      onScrollTargetConsumed?.()
    }, 120)
    return () => window.clearTimeout(t)
  }, [scrollTargetGroupId, groupIdsKey, journalByGroup, onScrollTargetConsumed, matchedGroups])

  useEffect(() => {
    if (!onGlVoucherNoByGroup) return
    const m: Record<string, string> = {}
    for (const [gid, j] of Object.entries(journalByGroup)) {
      const vn = (j?.voucher_no || '').trim()
      if (vn) m[gid] = vn
    }
    if (Object.keys(m).length > 0) onGlVoucherNoByGroup(m)
  }, [journalByGroup, onGlVoucherNoByGroup])

  const coaByCode = useMemo(() => new Map(coaList.map(c => [c.code, c])), [coaList])
  const coaOptionLabel = (c: ChartOfAccountItem) => {
    const name = (c.name_zh || c.name_en || '').trim()
    if (!name || name === c.code) return c.code
    return `${c.code} — ${name}`
  }

  const accountDisplay = (code: string) => {
    const c = coaByCode.get(code)
    return c ? coaOptionLabel(c) : code
  }

  const loadPosted = useCallback(async () => {
    try {
      const { journals } = await reconciliationApi.glListPosted(80)
      setPostedList(journals)
    } catch {
      setPostedList([])
    }
  }, [])

  useEffect(() => {
    if (matchedGroups.length === 0) {
      groupFetchGenRef.current = {}
      setJournalByGroup({})
      setLoadErrByGroup({})
      setLoadingInitial(false)
      return
    }

    let cancelled = false
    setLoadingInitial(true)
    const ids = matchedGroups.map(g => g.id)

    const run = async () => {
      let nextIdx = 0
      const worker = async () => {
        while (!cancelled) {
          const i = nextIdx++
          if (i >= ids.length) return
          const gid = ids[i]
          const gen = beginGroupJournalFetch(gid)
          try {
            const { journal: existing } = await reconciliationApi.glGetByGroup(gid)
            if (cancelled) return
            if (isSupersededJournalFetch(gid, gen)) {
              return
            }
            if (existing) {
              const merged = pickJournalWithAiApplySeed(gid, existing, glApplyPatchSeedsRef.current, matchedGroupsRef.current)
              setJournalByGroup(prev => ({ ...prev, [gid]: merged }))
              setLoadErrByGroup(prev => {
                const n = { ...prev }
                delete n[gid]
                return n
              })
            } else {
              const j = await reconciliationApi.glEnsureDraft(gid)
              if (cancelled) return
              if (isSupersededJournalFetch(gid, gen)) return
              const merged = pickJournalWithAiApplySeed(gid, j, glApplyPatchSeedsRef.current, matchedGroupsRef.current)
              setJournalByGroup(prev => ({ ...prev, [gid]: merged }))
              setLoadErrByGroup(prev => {
                const n = { ...prev }
                delete n[gid]
                return n
              })
            }
          } catch (e) {
            if (cancelled) return
            if (isSupersededJournalFetch(gid, gen)) return
            const msg = e instanceof Error ? e.message : String(e)
            setLoadErrByGroup(prev => ({ ...prev, [gid]: msg }))
          }
        }
      }

      const nWorkers = Math.min(3, ids.length)
      await Promise.all(Array.from({ length: nWorkers }, () => worker()))
      if (!cancelled) setLoadingInitial(false)
    }

    void run()
    return () => {
      cancelled = true
    }
  }, [groupIdsKey])

  useEffect(() => {
    const sig = glJournalRefetchSignal
    if (!sig?.groupIds?.length) return
    let cancelled = false
    const run = async () => {
      for (const gid of sig.groupIds) {
        if (cancelled) return
        const gen = beginGroupJournalFetch(gid)
        try {
          let { journal } = await reconciliationApi.glGetByGroup(gid)
          if (cancelled) return
          if (isSupersededJournalFetch(gid, gen)) {
            return
          }
          if (!journal) {
            try {
              journal = await reconciliationApi.glEnsureDraft(gid)
            } catch {
              journal = null
            }
            if (cancelled) return
            if (isSupersededJournalFetch(gid, gen)) return
          }
          if (journal) {
            const merged = pickJournalWithAiApplySeed(gid, journal, glApplyPatchSeedsRef.current, matchedGroupsRef.current)
            setJournalByGroup(prev => ({ ...prev, [gid]: merged }))
            setLoadErrByGroup(prev => {
              const n = { ...prev }
              delete n[gid]
              return n
            })
          }
        } catch (e) {
          if (cancelled) return
          if (isSupersededJournalFetch(gid, gen)) return
          const msg2 = e instanceof Error ? e.message : String(e)
          setLoadErrByGroup(prev => ({ ...prev, [gid]: msg2 }))
        }
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [glJournalRefetchSignal?.nonce])

  /** Apply authoritative PATCH bodies after GET-refetch effect runs — avoids OCR `setGlJournalRefetchSignal` clobbering seeds and stale GET winning. */
  useEffect(() => {
    const pack = glApplyPatchSeeds
    if (!pack?.byGroupId || !Object.keys(pack.byGroupId).length) return
    for (const [gidKey, seeded] of Object.entries(pack.byGroupId)) {
      const mergeKeys = mergeKeysForAiSeed(gidKey, seeded, matchedGroups)
      for (const k of mergeKeys) beginGroupJournalFetch(k)
      setJournalByGroup(prev => {
        const next = { ...prev }
        for (const k of mergeKeys) next[k] = seeded
        return next
      })
      setLoadErrByGroup(prev => {
        const n = { ...prev }
        for (const k of mergeKeys) delete n[k]
        return n
      })
    }
  }, [glApplyPatchSeeds?.nonce, groupIdsKey])

  useEffect(() => {
    if (!onPrimaryJournalStatusByGroup) return
    const snap: Record<string, string> = {}
    for (const [gid, j] of Object.entries(journalByGroup)) {
      if (j && typeof j.status === 'string') snap[gid] = j.status
    }
    onPrimaryJournalStatusByGroup(snap)
  }, [journalByGroup, onPrimaryJournalStatusByGroup])

  const syncJournal = (groupId: string, j: GlJournalPayload) => {
    setJournalByGroup(prev => ({ ...prev, [groupId]: j }))
  }

  const loadOrCreate = async (groupId: string) => {
    const gen = beginGroupJournalFetch(groupId)
    setLoadingGid(groupId)
    setErrMsg(null)
    try {
      const { journal: existing } = await reconciliationApi.glGetByGroup(groupId)
      if (isSupersededJournalFetch(groupId, gen)) return
      if (existing) {
        syncJournal(groupId, existing)
      } else {
        const j = await reconciliationApi.glEnsureDraft(groupId)
        if (isSupersededJournalFetch(groupId, gen)) return
        syncJournal(groupId, j)
      }
      if (!isSupersededJournalFetch(groupId, gen)) {
        setLoadErrByGroup(prev => {
          const n = { ...prev }
          delete n[groupId]
          return n
        })
      }
    } catch (e: unknown) {
      if (!isSupersededJournalFetch(groupId, gen)) {
        setErrMsg(e instanceof Error ? e.message : String(e))
      }
    } finally {
      setLoadingGid(null)
    }
  }

  useEffect(() => {
    if (!actionRequest?.groupId) return
    const { groupId, action } = actionRequest
    const j = journalByGroup[groupId]
    const grpCard = matchedGroupsRef.current.find(g => g.id === groupId)?.match_cardinality
    if (action === 'edit') {
      // GL-only drafts: Approve only (offset applied on post).
      if (grpCard === 'GL:1') {
        onActionRequestConsumed?.()
        return
      }
      if (j?.status === 'draft') setEditModalGroupId(groupId)
      else if (!j) void loadOrCreate(groupId).then(() => setEditModalGroupId(groupId))
    } else if (action === 'approve') {
      if (j?.status === 'draft') setApproveConfirmGroupId(groupId)
      else if (!j) void loadOrCreate(groupId).then(() => setApproveConfirmGroupId(groupId))
    } else if (action === 'unpost') {
      if (j?.status === 'posted') setReverseConfirmGroupId(groupId)
      else if (!j) void loadOrCreate(groupId).then(() => setReverseConfirmGroupId(groupId))
    }
    onActionRequestConsumed?.()
    // Intentionally only react to new actionRequest (not journalByGroup churn).
    // eslint-disable-next-line react-hooks/exhaustive-deps -- loadOrCreate / journal snapshot at request time
  }, [actionRequest])

  const discardGroupDraftsAndReload = async (groupId: string) => {
    setLoadingGid(groupId)
    setErrMsg(null)
    try {
      await reconciliationApi.glDeleteDraftByGroup(groupId)
      setJournalByGroup(prev => {
        const next = { ...prev }
        delete next[groupId]
        return next
      })
      await loadOrCreate(groupId)
    } catch (e: unknown) {
      setErrMsg(e instanceof Error ? e.message : String(e))
    } finally {
      setLoadingGid(null)
    }
  }

  const rebuildDraft = async (groupId: string) => {
    const cur = journalByGroup[groupId]
    if (cur?.status === 'posted') {
      setErrMsg('Already posted: use Cancel approval or Advanced → Create reversal draft')
      return
    }
    const gen = beginGroupJournalFetch(groupId)
    setLoadingGid(groupId)
    setErrMsg(null)
    try {
      await reconciliationApi.glDeleteDraftByGroup(groupId)
      setJournalByGroup(prev => {
        const next = { ...prev }
        delete next[groupId]
        return next
      })
      const j = await reconciliationApi.glEnsureDraft(groupId)
      if (isSupersededJournalFetch(groupId, gen)) return
      syncJournal(groupId, j)
    } catch (e: unknown) {
      if (!isSupersededJournalFetch(groupId, gen)) {
        setErrMsg(e instanceof Error ? e.message : String(e))
      }
    } finally {
      setLoadingGid(null)
    }
  }

  const postJournal = async (groupId: string, j: GlJournalPayload, confirmBankCreate = false) => {
    const gen = beginGroupJournalFetch(groupId)
    setLoadingGid(groupId)
    setErrMsg(null)
    try {
      let posted: GlJournalPayload
      try {
        posted = await reconciliationApi.glPostJournal(j.id, { confirmBankCreate })
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e)
        if (!confirmBankCreate && msg.includes('CONFIRM_CREATE_BANK')) {
          const ok = window.confirm(
            `${msg}\n\nCreate the counterpart bank transaction and post?`,
          )
          if (!ok) return
          posted = await reconciliationApi.glPostJournal(j.id, { confirmBankCreate: true })
        } else {
          throw e
        }
      }
      if (isSupersededJournalFetch(groupId, gen)) return
      syncJournal(groupId, posted)
      await loadPosted()
    } catch (e: unknown) {
      if (!isSupersededJournalFetch(groupId, gen)) {
        setErrMsg(e instanceof Error ? e.message : String(e))
      }
    } finally {
      setLoadingGid(null)
    }
  }

  const unpostPosted = async (groupId: string, postedJournalId: string) => {
    const gen = beginGroupJournalFetch(groupId)
    setLoadingGid(groupId)
    setErrMsg(null)
    try {
      const { journal: fresh } = await reconciliationApi.glGetByGroup(groupId)
      if (isSupersededJournalFetch(groupId, gen)) return
      if (!fresh || fresh.status !== 'posted') {
        if (fresh) syncJournal(groupId, fresh)
        setErrMsg(
          fresh
            ? 'This group is not posted; the list was refreshed. Try again.'
            : 'No GL voucher found for this group. Reload and try again.',
        )
        return
      }
      if (fresh.id !== postedJournalId) {
        syncJournal(groupId, fresh)
        setErrMsg(
          `This match group now shows voucher ${fresh.voucher_no}. Click Cancel approval again.`,
        )
        return
      }
      const draft = await reconciliationApi.glUnpostToDraft(postedJournalId)
      if (isSupersededJournalFetch(groupId, gen)) return
      syncJournal(groupId, draft)
      await loadPosted()
    } catch (e: unknown) {
      if (!isSupersededJournalFetch(groupId, gen)) {
        setErrMsg(e instanceof Error ? e.message : String(e))
      }
    } finally {
      setLoadingGid(null)
    }
  }

  /** Optional: suspense reversal creates a separate draft (many lines); use 進階. */
  const suspenseReversePosted = async (groupId: string, postedJournalId: string) => {
    const gen = beginGroupJournalFetch(groupId)
    setLoadingGid(groupId)
    setErrMsg(null)
    try {
      const { journal: fresh } = await reconciliationApi.glGetByGroup(groupId)
      if (isSupersededJournalFetch(groupId, gen)) return
      if (!fresh || fresh.status !== 'posted') {
        if (fresh) syncJournal(groupId, fresh)
        setErrMsg(
          fresh
            ? 'This group is not posted; the list was refreshed.'
            : 'No GL voucher found for this group. Reload and try again.',
        )
        return
      }
      if (fresh.id !== postedJournalId) {
        syncJournal(groupId, fresh)
        setErrMsg(
          `This match group now shows voucher ${fresh.voucher_no}. Try Create reversal draft in Advanced again.`,
        )
        return
      }
      const rev = await reconciliationApi.glReverseDraft(postedJournalId)
      if (isSupersededJournalFetch(groupId, gen)) return
      syncJournal(groupId, rev)
      await loadPosted()
    } catch (e: unknown) {
      if (!isSupersededJournalFetch(groupId, gen)) {
        setErrMsg(e instanceof Error ? e.message : String(e))
      }
    } finally {
      setLoadingGid(null)
    }
  }

  const editJournal = editModalGroupId ? journalByGroup[editModalGroupId] : null
  const approvePendingJournal =
    approveConfirmGroupId && journalByGroup[approveConfirmGroupId]?.status === 'draft'
      ? journalByGroup[approveConfirmGroupId]
      : null
  const approveBusy = Boolean(approveConfirmGroupId && loadingGid === approveConfirmGroupId)
  const reversePendingJournal =
    reverseConfirmGroupId && journalByGroup[reverseConfirmGroupId]?.status === 'posted'
      ? journalByGroup[reverseConfirmGroupId]
      : null
  const reverseBusy = Boolean(reverseConfirmGroupId && loadingGid === reverseConfirmGroupId)
  const suspensePendingJournal =
    suspenseReverseConfirmGroupId && journalByGroup[suspenseReverseConfirmGroupId]?.status === 'posted'
      ? journalByGroup[suspenseReverseConfirmGroupId]
      : null
  const suspenseReverseBusy = Boolean(suspenseReverseConfirmGroupId && loadingGid === suspenseReverseConfirmGroupId)

  if (matchedGroups.length === 0) return null

  return (
    <div className={`recon-gl-section${collapseTables ? ' recon-gl-section--headless' : ''}`}>
      {errMsg && <div className="recon-gl-error">{errMsg}</div>}

      <ReconGlJournalEditModal
        open={Boolean(editModalGroupId && editJournal?.status === 'draft')}
        groupId={editModalGroupId || ''}
        journal={editJournal?.status === 'draft' ? editJournal : null}
        coaList={coaList}
        coaOptionLabel={coaOptionLabel}
        busy={loadingGid === editModalGroupId}
        onClose={() => setEditModalGroupId(null)}
        onSaved={next => {
          if (editModalGroupId) {
            beginGroupJournalFetch(editModalGroupId)
            syncJournal(editModalGroupId, next)
          }
        }}
        onAccountCodesSynced={onGlAccountCodesSynced}
      />

      {approveConfirmGroupId && approvePendingJournal && (
        <div
          className="recon-gl-modal-overlay"
          role="presentation"
          onMouseDown={e => {
            if (e.target === e.currentTarget && !approveBusy) setApproveConfirmGroupId(null)
          }}
        >
          <div
            className="recon-gl-modal recon-gl-modal--confirm"
            role="dialog"
            aria-labelledby="recon-gl-approve-title"
            aria-modal="true"
            onMouseDown={e => e.stopPropagation()}
          >
            <div className="recon-gl-modal-header">
              <h3 id="recon-gl-approve-title">Post journal?</h3>
              <button
                type="button"
                className="recon-gl-modal-close"
                onClick={() => !approveBusy && setApproveConfirmGroupId(null)}
                disabled={approveBusy}
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <p className="recon-gl-modal-meta">
              Group <code>{approveConfirmGroupId.slice(0, 8)}…</code>
              {approvePendingJournal.voucher_no ? ` · ${approvePendingJournal.voucher_no}` : null}
            </p>
            <p className="recon-gl-modal-confirm-body">
              This will <strong>post</strong> this balanced draft to the ledger. Cancel to keep it as a draft.
            </p>
            <p className="recon-gl-modal-meta" style={{ marginBottom: 12 }}>
              {approvePendingJournal.currency}{' '}
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                Dr {fmt(approvePendingJournal.total_debit)} · Cr {fmt(approvePendingJournal.total_credit)}
              </span>
            </p>
            <div className="recon-gl-modal-footer">
              <button type="button" onClick={() => !approveBusy && setApproveConfirmGroupId(null)} disabled={approveBusy}>
                Cancel
              </button>
              <button
                type="button"
                className="primary"
                disabled={approveBusy}
                onClick={() => {
                  void (async () => {
                    const gid = approveConfirmGroupId
                    if (!gid) return
                    const jj = journalByGroup[gid]
                    if (!jj || jj.status !== 'draft') {
                      setApproveConfirmGroupId(null)
                      return
                    }
                    await postJournal(gid, jj)
                    setApproveConfirmGroupId(null)
                  })()
                }}
              >
                {approveBusy ? 'Posting…' : 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}

      {reverseConfirmGroupId && reversePendingJournal && (
        <div
          className="recon-gl-modal-overlay"
          role="presentation"
          onMouseDown={e => {
            if (e.target === e.currentTarget && !reverseBusy) setReverseConfirmGroupId(null)
          }}
        >
          <div
            className="recon-gl-modal recon-gl-modal--confirm recon-gl-modal--reverse-confirm"
            role="dialog"
            aria-labelledby="recon-gl-reverse-title"
            aria-modal="true"
            onMouseDown={e => e.stopPropagation()}
          >
            <div className="recon-gl-modal-header">
              <h3 id="recon-gl-reverse-title">Cancel approval</h3>
              <button
                type="button"
                className="recon-gl-modal-close"
                onClick={() => !reverseBusy && setReverseConfirmGroupId(null)}
                disabled={reverseBusy}
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <p className="recon-gl-modal-meta">
              Group <code>{reverseConfirmGroupId.slice(0, 8)}…</code>
              {reversePendingJournal.voucher_no ? ` · ${reversePendingJournal.voucher_no}` : null}
            </p>
            <p className="recon-gl-modal-confirm-body">
              This sets the <strong>same voucher</strong> back to <strong>draft</strong>. No extra GL voucher is
              created and lines are unchanged. If a reversal voucher already depends on this one, unpost or remove that
              reversal first.
            </p>
            <p className="recon-gl-modal-confirm-body recon-gl-modal-desc-muted" style={{ marginTop: 8 }}>
              Advanced → Create reversal draft if you still need a traditional suspense reversal (new voucher, more lines).
            </p>
            <p className="recon-gl-modal-meta" style={{ marginBottom: 12 }}>
              {reversePendingJournal.currency}{' '}
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                Dr {fmt(reversePendingJournal.total_debit)} · Cr {fmt(reversePendingJournal.total_credit)}
              </span>
            </p>
            <div className="recon-gl-modal-footer">
              <button type="button" onClick={() => !reverseBusy && setReverseConfirmGroupId(null)} disabled={reverseBusy}>
                Cancel
              </button>
              <button
                type="button"
                className="primary recon-gl-modal-primary-dark"
                disabled={reverseBusy}
                onClick={() => {
                  void (async () => {
                    const gid = reverseConfirmGroupId
                    if (!gid) return
                    const jj = journalByGroup[gid]
                    if (!jj || jj.status !== 'posted') {
                      setReverseConfirmGroupId(null)
                      return
                    }
                    await unpostPosted(gid, jj.id)
                    setReverseConfirmGroupId(null)
                  })()
                }}
              >
                {reverseBusy ? 'Working…' : 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}

      {suspenseReverseConfirmGroupId && suspensePendingJournal && (
        <div
          className="recon-gl-modal-overlay"
          role="presentation"
          onMouseDown={e => {
            if (e.target === e.currentTarget && !suspenseReverseBusy) setSuspenseReverseConfirmGroupId(null)
          }}
        >
          <div
            className="recon-gl-modal recon-gl-modal--confirm recon-gl-modal--reverse-confirm"
            role="dialog"
            aria-labelledby="recon-gl-suspense-title"
            aria-modal="true"
            onMouseDown={e => e.stopPropagation()}
          >
            <div className="recon-gl-modal-header">
              <h3 id="recon-gl-suspense-title">Suspense reversal draft</h3>
              <button
                type="button"
                className="recon-gl-modal-close"
                onClick={() => !suspenseReverseBusy && setSuspenseReverseConfirmGroupId(null)}
                disabled={suspenseReverseBusy}
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <p className="recon-gl-modal-meta">
              Group <code>{suspenseReverseConfirmGroupId.slice(0, 8)}…</code>
              {suspensePendingJournal.voucher_no ? ` · ${suspensePendingJournal.voucher_no}` : null}
            </p>
            <p className="recon-gl-modal-confirm-body">
              Creates a <strong>new</strong> draft that offsets this posted voucher through suspense (1999). Line count is
              roughly doubled vs the source voucher. The posted voucher stays on the ledger until you post the reversal.
            </p>
            <p className="recon-gl-modal-meta" style={{ marginBottom: 12 }}>
              {suspensePendingJournal.currency}{' '}
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                Dr {fmt(suspensePendingJournal.total_debit)} · Cr {fmt(suspensePendingJournal.total_credit)}
              </span>
            </p>
            <div className="recon-gl-modal-footer">
              <button
                type="button"
                onClick={() => !suspenseReverseBusy && setSuspenseReverseConfirmGroupId(null)}
                disabled={suspenseReverseBusy}
              >
                Cancel
              </button>
              <button
                type="button"
                className="primary recon-gl-modal-primary-dark"
                disabled={suspenseReverseBusy}
                onClick={() => {
                  void (async () => {
                    const gid = suspenseReverseConfirmGroupId
                    if (!gid) return
                    const jj = journalByGroup[gid]
                    if (!jj || jj.status !== 'posted') {
                      setSuspenseReverseConfirmGroupId(null)
                      return
                    }
                    await suspenseReversePosted(gid, jj.id)
                    setSuspenseReverseConfirmGroupId(null)
                  })()
                }}
              >
                {suspenseReverseBusy ? 'Working…' : 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}

      {!collapseTables && matchedGroups.map(grp => {
        const j = journalByGroup[grp.id]
        const busy = loadingGid === grp.id
        const loadErr = loadErrByGroup[grp.id]
        const cur = j?.currency || 'HKD'
        const isGlOnly = grp.match_cardinality === 'GL:1'

        return (
          <div key={grp.id} className="recon-gl-group" data-recon-group-id={grp.id}>
            <div className="recon-gl-group-h recon-gl-group-h-static">
              <strong className="recon-gl-title">GL voucher · {cur}</strong>
              <span style={{ fontFamily: 'monospace', fontSize: '0.75rem' }} title={grp.id}>
                {grp.id.slice(0, 8)}…
              </span>
              {isGlOnly ? <span className="recon-gl-badge">GL only</span> : null}
              <span className={`recon-gl-badge${j?.status === 'posted' ? ' posted' : ''}`}>
                {j?.status === 'posted' ? 'Posted' : j?.status === 'draft' ? 'Draft' : loadingInitial && !j ? 'Loading' : '—'}
              </span>
              {j?.voucher_no && <span className="recon-gl-voucher">{j.voucher_no}</span>}
              {j && !j.balanced && <span style={{ color: '#b45309' }}>Unbalanced</span>}
            </div>

            {loadErr && <div className="recon-gl-error" style={{ padding: '4px 10px' }}>{loadErr}</div>}

            {j && (
              <div className="recon-gl-body">
                <table className="recon-gl-table recon-gl-table-main">
                  <thead>
                    <tr>
                      <th>Account</th>
                      <th className="recon-gl-amt">Debit</th>
                      <th className="recon-gl-amt">Credit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {j.lines.map(ln => {
                      const code = ln.account_code
                      const dr = ln.debit
                      const cr = ln.credit
                      return (
                        <tr key={ln.id}>
                          <td>
                            <span className="recon-gl-account-readonly">{accountDisplay(code)}</span>
                          </td>
                          <td className="recon-gl-amt">{dr ? `${cur} ${fmt(dr)}` : '—'}</td>
                          <td className="recon-gl-amt">{cr ? `${cur} ${fmt(cr)}` : '—'}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                  <tfoot>
                    <tr>
                      <td>Total</td>
                      <td className="recon-gl-amt">{cur} {fmt(j.total_debit)}</td>
                      <td className="recon-gl-amt">{cur} {fmt(j.total_credit)}</td>
                    </tr>
                  </tfoot>
                </table>

                <p className="recon-gl-balance-hint">
                  {j.balanced ? '✓ Balanced' : `Δ ${fmt(j.total_debit - j.total_credit)} (${cur})`}
                </p>

                {j.status === 'draft' && (
                  <div className="recon-gl-main-actions">
                    {!isGlOnly && (
                      <button type="button" onClick={() => setEditModalGroupId(grp.id)} disabled={busy}>
                        Edit
                      </button>
                    )}
                    <button
                      type="button"
                      className="primary"
                      title="Post"
                      onClick={() => setApproveConfirmGroupId(grp.id)}
                      disabled={busy || !j.balanced}
                    >
                      Approve
                    </button>
                  </div>
                )}

                {j.status === 'posted' && (
                  <div className="recon-gl-main-actions">
                    <button
                      type="button"
                      className="primary recon-gl-primary-posted"
                      title="Create a reversal draft through suspense — the posted voucher stays on the ledger"
                      onClick={() => setReverseConfirmGroupId(grp.id)}
                      disabled={busy}
                    >
                      Cancel approval
                    </button>
                  </div>
                )}

                <details className="recon-gl-advanced">
                  <summary>Advanced</summary>
                  <div className="recon-gl-advanced-body">
                    <table className="recon-gl-table recon-gl-table-meta">
                      <thead>
                        <tr>
                          <th>#</th>
                          <th>Account</th>
                          <th>Memo</th>
                        </tr>
                      </thead>
                      <tbody>
                        {j.lines.map(ln => (
                          <tr key={`m-${ln.id}`}>
                            <td>{ln.line_no}</td>
                            <td>{accountDisplay(ln.account_code)}</td>
                            <td>{ln.memo || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>

                    <div className="recon-gl-meta" style={{ marginTop: 8 }}>
                      <button type="button" onClick={() => loadOrCreate(grp.id)} disabled={busy}>
                        Reload
                      </button>
                      <button type="button" onClick={() => rebuildDraft(grp.id)} disabled={busy}>
                        Rebuild draft
                      </button>
                      {j.status === 'draft' && (
                        <button
                          type="button"
                          onClick={() => discardGroupDraftsAndReload(grp.id)}
                          disabled={busy}
                          title="Delete unposted drafts in this group, then reload the posted voucher or create a new draft"
                        >
                          Delete drafts and reload
                        </button>
                      )}
                    </div>

                    {j.status === 'posted' && (
                      <div className="recon-gl-actions">
                        <button
                          type="button"
                          onClick={() => setSuspenseReverseConfirmGroupId(grp.id)}
                          disabled={busy}
                          title="Create a reversal draft through suspense 1999; the posted voucher stays"
                        >
                          Create reversal draft (via suspense)
                        </button>
                      </div>
                    )}

                  </div>
                </details>
              </div>
            )}

            {!j && !loadErr && loadingInitial && (
              <div className="recon-gl-body recon-gl-loading">Loading GL draft…</div>
            )}
          </div>
        )
      })}

      {!collapseTables && (
        <details
          className="recon-gl-advanced recon-gl-posted-wrap"
          onToggle={e => {
            const el = e.target as HTMLDetailsElement
            if (el.open) void loadPosted()
          }}
        >
          <summary>Advanced · Posted GL list</summary>
          <div className="recon-gl-posted">
            <div className="recon-gl-posted-list">
              {postedList.length === 0 && <div className="recon-empty" style={{ padding: 8 }}>None yet (loads when opened)</div>}
              {postedList.map(p => (
                <div key={p.id} className="recon-gl-posted-row">
                  <span>{p.voucher_no}</span>
                  <span>{(p.journal_date || '').slice(0, 10)}</span>
                  <span>{p.currency} {fmt(p.total_debit)}</span>
                </div>
              ))}
            </div>
          </div>
        </details>
      )}
    </div>
  )
}
