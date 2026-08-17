/**
 * Module-folder + merge rules (Phase 3).
 *
 * - A run targets exactly one module (its processing_mode) and its results are
 *   saved into that module's folder.
 * - Tables/results can be merged within the same module, but never across
 *   modules. This mirrors the server-side guard in backend api/workflows.py.
 */

function norm(mode: string | null | undefined): string {
  return (mode || '').trim().toUpperCase()
}

/** True only when both sides belong to the same module. */
export function canMergeModules(a: string | null | undefined, b: string | null | undefined): boolean {
  const na = norm(a)
  const nb = norm(b)
  return na !== '' && na === nb
}

/** True when a run of `runMode` may be placed into a folder of `folderMode`. */
export function canPlaceRunInFolder(
  runMode: string | null | undefined,
  folderMode: string | null | undefined,
): boolean {
  const f = norm(folderMode)
  // Untyped (legacy) folders accept any module.
  return f === '' || f === norm(runMode)
}

export function crossModuleMergeError(a: string | null | undefined, b: string | null | undefined): string {
  return `Cannot merge a ${norm(a) || 'unknown'} item with a ${norm(b) || 'unknown'} item. Records stay within their own module.`
}
