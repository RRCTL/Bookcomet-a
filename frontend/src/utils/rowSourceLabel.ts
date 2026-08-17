import { formatBankSourceFile } from './bankSourceFile'

type RunFile = { task_file_id: string; original_filename?: string | null }

export function txSourceLabel(tx: Record<string, unknown>, fallbackFilename = ''): string {
  const existing = String(tx.source_file ?? tx.file_position ?? '').trim()
  const page = tx._page ?? tx.page
  return formatBankSourceFile(fallbackFilename, page, existing)
}

export function filesByIdFromRun(files: RunFile[]): Map<string, RunFile> {
  return new Map(files.map(f => [f.task_file_id, f]))
}

export function assetSourceLabel(
  record: Record<string, unknown>,
  filesById: Map<string, RunFile>,
): string {
  const existing = String(record.source_file ?? record.file_position ?? '').trim()
  const fid = String(record.source_file_id ?? '').trim()
  const fallback = fid ? filesById.get(fid)?.original_filename?.trim() || '' : ''
  const page = record._page ?? record.page
  return formatBankSourceFile(fallback, page, existing)
}
