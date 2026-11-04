/**
 * Helpers that keep Processing Live-output review tables scoped to one run.
 * Sticky editedRows / payloads across run switches caused cross-run bleed.
 */

export type ReviewTableSession = {
  /** Run id the current payloads / edited overlay belong to (null while loading). */
  boundRunId: string | null
  payloads: Record<string, Record<string, unknown>>
  editedRows: unknown[] | null
  /** True after a run switch until that run's payloads arrive. */
  loading: boolean
}

export function emptyReviewTableSession(): ReviewTableSession {
  return { boundRunId: null, payloads: {}, editedRows: null, loading: false }
}

/**
 * Clear table state immediately when the user selects another run.
 * `loading: true` forces an empty loading grid — never flash the previous run's rows.
 */
export function clearReviewTableOnRunSwitch(
  nextRunId: string | null,
): ReviewTableSession {
  return {
    boundRunId: null,
    payloads: {},
    editedRows: null,
    loading: nextRunId != null,
  }
}

/**
 * Clear table state when "Rebuild from this run's files" starts.
 * Same empty-loader spirit as a run switch — never leave the old wrong rows visible.
 */
export function clearReviewTableForRebuild(
  runId: string | null,
): ReviewTableSession {
  return clearReviewTableOnRunSwitch(runId)
}

/**
 * Apply a fetch result only if it still matches the active run.
 * Ignores stale responses from a previously selected run (race).
 */
export function applyPayloadsIfCurrentRun(
  session: ReviewTableSession,
  activeRunId: string | null,
  fetchedRunId: string,
  payloads: Record<string, Record<string, unknown>>,
): ReviewTableSession {
  if (!activeRunId || fetchedRunId !== activeRunId) return session
  return {
    boundRunId: fetchedRunId,
    payloads,
    editedRows: null,
    loading: false,
  }
}

/**
 * Rows shown in the grid. While loading (or bound to another run), always [].
 * Prefer edited overlay only when it belongs to the active run.
 */
export function resolveDisplayRowsForRun<T>(
  activeRunId: string | null,
  boundRunId: string | null,
  canonicalRows: T[],
  editedRows: T[] | null,
  loading = false,
): T[] {
  if (loading) return []
  if (!activeRunId || boundRunId !== activeRunId) return []
  if (!editedRows) return canonicalRows
  return editedRows
}

/** Whether Approve may use the current edited overlay for this run. */
export function editedRowsSafeForApprove(
  activeRunId: string | null,
  boundRunId: string | null,
  editedRows: unknown[] | null,
  loading = false,
): boolean {
  if (loading) return false
  if (!editedRows) return true
  return Boolean(activeRunId && boundRunId === activeRunId)
}

/** True when the Live output grid must show an empty loading state. */
export function reviewTableShowingLoader(
  activeRunId: string | null,
  session: Pick<ReviewTableSession, 'boundRunId' | 'loading'>,
): boolean {
  if (!activeRunId) return false
  return session.loading || session.boundRunId !== activeRunId
}
