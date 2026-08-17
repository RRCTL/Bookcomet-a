import type { ServerChatTask } from '../../services/api'
import type { ProcessingMode } from '../../components/ModeSelector'
import type { ChatTask } from './types'

/** RECON mode is no longer a first-class UI folder; legacy server rows map to BANK. */
export function normalizeClientProcessingMode(pm: ProcessingMode | string | undefined): ProcessingMode {
  const raw = String(pm ?? 'AR').trim().toUpperCase()
  if (raw === 'RECON') return 'BANK'
  // Legacy branding: Assets & Liabilities → Other
  if (raw === 'ASSET_LIA') return 'OTHER'
  const allowed: ProcessingMode[] = ['AR', 'AP', 'BANK', 'REPORT', 'OTHER']
  if (allowed.includes(raw as ProcessingMode)) return raw as ProcessingMode
  return 'AR'
}

/** localStorage key for task list metadata (per user, per workspace). */
export function workspaceTasksCacheKey(userId: string, companyId: string): string {
  return `tasks_v1_${userId}_${companyId}`
}

/** Map server task (snake_case) → frontend ChatTask (camelCase). */
export function serverTaskToFrontend(st: ServerChatTask): ChatTask {
  return {
    id: st.id,
    title: st.title,
    createdAt: st.created_at,
    // Keep server status as-is. Mid-batch OCR can set has_spreadsheet before status flips
    // to completed; mapping that to "completed" showed a false green tick in the sidebar.
    status: st.status as ChatTask['status'],
    processingMode: normalizeClientProcessingMode(st.processing_mode),
    messages: [],
    fileQueue: [],
    fileCount: st.file_count,
    pageCount: st.page_count,
    hasSpreadsheet: st.has_spreadsheet,
    bankBatchIds: st.bank_batch_ids ?? undefined,
    ledgerBatchIds: st.ledger_batch_ids ?? undefined,
    dupWarning: st.dup_warning ?? undefined,
    titleGenerated: st.title_generated,
  }
}
