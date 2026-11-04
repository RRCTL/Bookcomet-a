type CropGateFileLike = {
  task_file_id: string
  file_status?: string | null
  error_text?: string | null
  result_summary_json?: Record<string, unknown> | null
}

function resetPreviewBoxesPending(
  preview: Record<string, unknown>,
): Record<string, unknown> {
  const next = { ...preview, phase: 'preview', active_box_id: null }
  const boxes = preview.boxes
  if (Array.isArray(boxes)) {
    next.boxes = boxes.map(b =>
      b && typeof b === 'object'
        ? { ...(b as Record<string, unknown>), extract_status: 'pending' }
        : b,
    )
  }
  const pages = preview.pages
  if (Array.isArray(pages)) {
    next.pages = pages.map(p => {
      if (!p || typeof p !== 'object') return p
      const page = { ...(p as Record<string, unknown>) }
      // Keep confirmed page marks from multipage review; only reopen extract progress.
      if (page.status === 'confirmed') return page
      const pageBoxes = page.boxes
      if (Array.isArray(pageBoxes)) {
        page.boxes = pageBoxes.map(b =>
          b && typeof b === 'object'
            ? { ...(b as Record<string, unknown>), extract_status: 'pending' }
            : b,
        )
      }
      return page
    })
  }
  return next
}

function cropPreviewPhase(file: {
  result_summary_json?: Record<string, unknown> | null
}): string {
  const summary = file.result_summary_json
  if (!summary || typeof summary !== 'object') return ''
  const cp = summary.crop_preview
  if (!cp || typeof cp !== 'object') return ''
  return String((cp as Record<string, unknown>).phase || '')
}

/**
 * True when crop_preview claims "extracting" but the file is no longer running.
 * That stale combo is the Confirm-hang zombie: gate stays blocked forever while
 * Live output may already show Stopped / Needs review.
 */
export function isStaleCropExtracting(file: {
  file_status?: string | null
  result_summary_json?: Record<string, unknown> | null
}): boolean {
  const status = (file.file_status ?? '').toLowerCase()
  if (status === 'running') return false
  return cropPreviewPhase(file) === 'extracting'
}

/**
 * Extract finished (file ok) but summary still says phase=extracting — seal to done
 * so the docked gate closes instead of contradicting Live output.
 */
export function sealCompletedCropExtract<T extends CropGateFileLike>(file: T): T {
  const summary =
    file.result_summary_json && typeof file.result_summary_json === 'object'
      ? { ...file.result_summary_json }
      : null
  if (!summary) return file
  const cp = summary.crop_preview
  if (!cp || typeof cp !== 'object') return file
  const nextPreview = {
    ...(cp as Record<string, unknown>),
    phase: 'done',
    active_box_id: null,
    message:
      (cp as Record<string, unknown>).message ||
      'Extract complete — review crops and rows',
  }
  return {
    ...file,
    error_text:
      file.error_text === 'needs_crop_preview' ? null : file.error_text,
    result_summary_json: {
      ...summary,
      needs_crop_preview: false,
      crop_status: 'done',
      crop_preview: nextPreview,
    },
  }
}

/**
 * Re-open the crop gate after Stop / failed / cancelled mid-extract so Confirm
 * is usable again (and the docked panel is not stuck on "extracting…").
 */
export function reopenCropGateAfterInterrupt<T extends CropGateFileLike>(file: T): T {
  const summary =
    file.result_summary_json && typeof file.result_summary_json === 'object'
      ? { ...file.result_summary_json }
      : null
  if (!summary) return file
  const cp = summary.crop_preview
  if (!cp || typeof cp !== 'object') return file
  const phase = String((cp as Record<string, unknown>).phase || '')
  const hadGate =
    Boolean(summary.needs_crop_preview) ||
    summary.crop_status === 'needs_crop_preview' ||
    file.error_text === 'needs_crop_preview' ||
    phase === 'extracting' ||
    phase === 'preview'
  if (!hadGate) return file
  if (phase !== 'extracting' && phase !== 'preview') return file

  const nextPreview = resetPreviewBoxesPending(cp as Record<string, unknown>)
  return {
    ...file,
    file_status: 'warning',
    error_text: 'needs_crop_preview',
    result_summary_json: {
      ...summary,
      needs_crop_preview: true,
      crop_status: 'needs_crop_preview',
      crop_preview_dismissed: false,
      crop_preview: nextPreview,
    },
  }
}

/**
 * Resolve Stopped-vs-extracting (and ok-vs-extracting) contradictions on a file.
 */
export function normalizeCropGateFile<T extends CropGateFileLike>(file: T): T {
  if (!isStaleCropExtracting(file)) return file
  const status = (file.file_status ?? '').toLowerCase()
  // Successful confirm-crops (incl. after cross-VLM timeout keep-primary) left
  // phase=extracting in a stale client copy — seal closed.
  if (status === 'ok') return sealCompletedCropExtract(file)
  if (status === 'warning') {
    const summary = file.result_summary_json
    const needsGate =
      Boolean(summary && typeof summary === 'object' && summary.needs_crop_preview) ||
      file.error_text === 'needs_crop_preview'
    if (!needsGate) return sealCompletedCropExtract(file)
    return reopenCropGateAfterInterrupt(file)
  }
  if (
    status === 'stopped' ||
    status === 'failed' ||
    status === 'cancelled' ||
    status === 'pending' ||
    status === 'queued' ||
    status === ''
  ) {
    return reopenCropGateAfterInterrupt(file)
  }
  return file
}

/** Normalize every file on a run so Live output and the crop gate agree. */
export function normalizeRunCropGateFiles<
  T extends { files: Array<CropGateFileLike> },
>(run: T): T {
  let changed = false
  const files = run.files.map(f => {
    const next = normalizeCropGateFile(f)
    if (next !== f) changed = true
    return next
  })
  return changed ? { ...run, files } : run
}

/**
 * Optimistic run patch when Confirm / Skip crops starts extract.
 * Enables WS + polling immediately so the gate is not stuck silent on a long HTTP await.
 */
export function applyCropConfirmOptimisticRun<
  T extends {
    run_status: string
    files: Array<CropGateFileLike>
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
