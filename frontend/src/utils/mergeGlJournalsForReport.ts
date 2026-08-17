import type { GlJournalPayload } from '../services/reconciliation'

export type GlSupersededDraft = {
  journalId: string
  voucherNo: string
  journalDate: string | null
  supersededByVoucherNo: string
  supersededByJournalId: string
  lines: Array<{ account_code: string; debit: number; credit: number; memo?: string | null }>
}

export type GlDraftConflict = {
  /** reconciliation_group_id or `orphan:${journalId}` */
  groupKey: string
  options: GlJournalPayload[]
}

export type MergeGlJournalsResult = {
  activeJournals: GlJournalPayload[]
  supersededDrafts: GlSupersededDraft[]
  conflicts: GlDraftConflict[]
}

function groupKeyFor(j: GlJournalPayload): string {
  const gid = (j.reconciliation_group_id ?? '').trim()
  return gid || `orphan:${j.id}`
}

function pickLatestPosted(posted: GlJournalPayload[]): GlJournalPayload {
  return [...posted].sort((a, b) => {
    const pa = a.posted_at ?? ''
    const pb = b.posted_at ?? ''
    if (pa !== pb) return pa.localeCompare(pb)
    return a.id.localeCompare(b.id)
  })[posted.length - 1]
}

/**
 * Per reconciliation group: posted wins (latest posted_at).
 * Drafts for that group become superseded entries for UI transparency.
 * Multiple drafts, no posted → conflict unless `picks[groupKey]` selects a journal id.
 */
export function mergeGlJournalsForReport(
  journals: GlJournalPayload[],
  picksByGroup: Record<string, string> | undefined,
): MergeGlJournalsResult {
  const byKey = new Map<string, GlJournalPayload[]>()
  for (const j of journals) {
    const key = groupKeyFor(j)
    if (!byKey.has(key)) byKey.set(key, [])
    byKey.get(key)!.push(j)
  }

  const activeJournals: GlJournalPayload[] = []
  const supersededDrafts: GlSupersededDraft[] = []
  const conflicts: GlDraftConflict[] = []

  for (const [key, group] of byKey) {
    const posted = group.filter((j) => (j.status || '').toLowerCase() === 'posted')
    const drafts = group.filter((j) => (j.status || '').toLowerCase() === 'draft')

    if (posted.length > 0) {
      const winner = pickLatestPosted(posted)
      activeJournals.push(winner)
      const loserPosted = posted.filter((p) => p.id !== winner.id)
      for (const lp of loserPosted) {
        supersededDrafts.push({
          journalId: lp.id,
          voucherNo: lp.voucher_no,
          journalDate: lp.journal_date,
          supersededByVoucherNo: winner.voucher_no,
          supersededByJournalId: winner.id,
          lines: lp.lines.map((ln) => ({
            account_code: ln.account_code,
            debit: ln.debit,
            credit: ln.credit,
            memo: ln.memo,
          })),
        })
      }
      for (const d of drafts) {
        supersededDrafts.push({
          journalId: d.id,
          voucherNo: d.voucher_no,
          journalDate: d.journal_date,
          supersededByVoucherNo: winner.voucher_no,
          supersededByJournalId: winner.id,
          lines: d.lines.map((ln) => ({
            account_code: ln.account_code,
            debit: ln.debit,
            credit: ln.credit,
            memo: ln.memo,
          })),
        })
      }
      continue
    }

    if (drafts.length === 0) continue

    if (drafts.length === 1) {
      activeJournals.push(drafts[0])
      continue
    }

    const pick = picksByGroup?.[key]?.trim()
    const chosen = pick ? drafts.find((d) => d.id === pick) : undefined
    if (chosen) {
      activeJournals.push(chosen)
      for (const d of drafts) {
        if (d.id === chosen.id) continue
        supersededDrafts.push({
          journalId: d.id,
          voucherNo: d.voucher_no,
          journalDate: d.journal_date,
          supersededByVoucherNo: chosen.voucher_no,
          supersededByJournalId: chosen.id,
          lines: d.lines.map((ln) => ({
            account_code: ln.account_code,
            debit: ln.debit,
            credit: ln.credit,
            memo: ln.memo,
          })),
        })
      }
      continue
    }

    conflicts.push({
      groupKey: key,
      options: [...drafts].sort(
        (a, b) => (a.created_at ?? '').localeCompare(b.created_at ?? '') || a.id.localeCompare(b.id),
      ),
    })
  }

  return { activeJournals, supersededDrafts, conflicts }
}
