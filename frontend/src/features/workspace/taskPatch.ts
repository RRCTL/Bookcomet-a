import { taskApi, type PatchTaskBody } from '../../services/api'

/** Fire-and-forget PATCH /api/tasks/:id (errors logged, no throw). */
export function patchTaskMetadataFireAndForget(
  id: string,
  patch: PatchTaskBody,
  companyId?: string | null,
): void {
  taskApi.update(id, patch, companyId).catch(err => console.warn('[Tasks] PATCH failed:', err))
}
