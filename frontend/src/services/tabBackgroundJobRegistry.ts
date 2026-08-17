/**
 * Job IDs started in this browser tab (mirrors WorkspaceApp localBackgroundJobIdsRef).
 * Used to POST /api/jobs/{id}/cancel when the workspace hits a fatal render error.
 */
const _tabOwnedBackgroundJobIds = new Set<string>()

export function trackTabBackgroundJob(jobId: string): void {
  _tabOwnedBackgroundJobIds.add(jobId)
}

export function untrackTabBackgroundJob(jobId: string): void {
  _tabOwnedBackgroundJobIds.delete(jobId)
}

/** Copy current ids and clear — call once when cancelling after a fatal workspace error. */
export function snapshotAndClearTabBackgroundJobIds(): string[] {
  const ids = [..._tabOwnedBackgroundJobIds]
  _tabOwnedBackgroundJobIds.clear()
  return ids
}
