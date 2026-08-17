import { api } from '../../services/api'
import { workflowApi, type WorkflowRun } from './workflowApi'

/** After first successful VLM run, generate an AI tab title from filenames. */
export async function maybeGenerateWorkflowTitle(run: WorkflowRun): Promise<WorkflowRun | null> {
  if (run.title && run.title !== 'Untitled') return null
  const names = run.files
    .map(f => f.original_filename)
    .filter((n): n is string => Boolean(n))
  if (!names.length) return null
  const content = names.slice(0, 5).join(', ')
  const title = await api.generateTitle([{ role: 'user', content }], run.processing_mode)
  if (!title || title === run.title) return null
  return workflowApi.patchRun(run.company_id, run.id, run.graph_json, title)
}
