import type { CreateTaskBody } from '../../services/api'
import type { ChatTask } from './types'

/** Body for `taskApi.create` / POST /api/tasks — mirrors optimistic client `ChatTask`. */
export function buildCreateTaskBody(task: ChatTask): CreateTaskBody {
  return {
    id: task.id,
    title: task.title,
    processing_mode: task.processingMode,
    status: task.status,
    file_count: task.fileCount,
    page_count: task.pageCount,
    has_spreadsheet: task.hasSpreadsheet,
    bank_batch_ids: task.bankBatchIds ?? null,
    ledger_batch_ids: task.ledgerBatchIds ?? null,
    dup_warning: task.dupWarning ?? null,
    title_generated: task.titleGenerated ?? false,
  }
}
