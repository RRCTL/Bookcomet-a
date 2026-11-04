/**
 * Optimistic run patch when Confirm / Skip crops starts extract.
 * Enables WS + polling immediately so the gate is not stuck silent on a long HTTP await.
 */
export function applyCropConfirmOptimisticRun<
  T extends {
    run_status: string
    files: Array<{
      task_file_id: string
      file_status?: string | null
      error_text?: string | null
      result_summary_json?: Record<string, unknown> | null
    }>
  },
>(
  run: T,
  taskFileId: string,
  opts: {
    boxes?: Array<Record<string, unknown>>
    phase?: 'extracting' | 'preview'
  } = {},
): T {
  const phase = opts.phase ?? 'extracting'
  return {
    ...run,
    run_status: 'executing',
    files: run.files.map(f => {
      if (f.task_file_id !== taskFileId) return f
      const prevSummary =
        f.result_summary_json && typeof f.result_summary_json === 'object'
          ? { ...f.result_summary_json }
          : {}
      const prevPreview =
        prevSummary.crop_preview && typeof prevSummary.crop_preview === 'object'
          ? { ...(prevSummary.crop_preview as Record<string, unknown>) }
          : {}
      if (opts.boxes) prevPreview.boxes = opts.boxes
      prevPreview.phase = phase
      return {
        ...f,
        file_status: 'running',
        error_text: null,
        result_summary_json: {
          ...prevSummary,
          needs_crop_preview: phase === 'preview',
          crop_status: phase === 'preview' ? 'needs_crop_preview' : 'extracting',
          crop_preview: prevPreview,
        },
      }
    }),
  }
}
