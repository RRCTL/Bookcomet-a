import { useState } from 'react'
import {
  AP_RECEIPT_OPTIONS_ORDER,
  AP_TABLE_OPTIONS_ORDER,
  type ApVlmReceiptSignal,
  type ApVlmTablePreset,
} from '../../workspace/apComposerOptions'
import type { WorkflowRunFile } from '../workflowApi'
import { formatFilePageCount } from '../filePageLabel'
import { CloudAiNotice } from '../../../components/CloudAiNotice'
import {
  ComposerFilesOverlay,
  handleComposerDragOver,
  handleComposerDrop,
} from './ComposerFilesOverlay'

type Props = {
  mode: string
  files: WorkflowRunFile[]
  workflowLabel?: string
  receiptSignal: ApVlmReceiptSignal | null
  tablePreset: ApVlmTablePreset | null
  showReceiptOptions: boolean
  canRun: boolean
  vlmActive: boolean
  busy: boolean
  onAttach: () => void
  onDropFiles: (files: FileList) => void
  onReceiptChange: (signal: ApVlmReceiptSignal) => void
  onTablePresetChange: (preset: ApVlmTablePreset) => void
  onRun: () => void
  onStop: () => void
  onReVlm: () => void
  onRemoveFile: (taskFileId: string) => void
  onPreviewFile?: (taskFileId: string) => void
  reVlmFileCount: number
}

const RECEIPT_LABELS: Record<ApVlmReceiptSignal, string> = {
  guess: 'Guess (auto)',
  single_per_page: 'Single / page',
  multi_per_page: 'Multi / page',
  single_span_pages: 'Span pages',
}

const TABLE_LABELS: Record<ApVlmTablePreset, string> = {
  default: 'Default columns',
  ap_table: 'AP table',
}

export function RunComposer({
  mode,
  files,
  workflowLabel,
  receiptSignal,
  tablePreset,
  showReceiptOptions,
  canRun,
  vlmActive,
  busy,
  onAttach,
  onDropFiles,
  onReceiptChange,
  onTablePresetChange,
  onRun,
  onStop,
  onReVlm,
  onRemoveFile,
  onPreviewFile,
  reVlmFileCount,
}: Props) {
  const [dragging, setDragging] = useState(false)
  const upperMode = mode.toUpperCase()
  const receiptBlocked =
    showReceiptOptions && (!receiptSignal || !tablePreset)
  const tableOptions =
    upperMode === 'AR'
      ? AP_TABLE_OPTIONS_ORDER.filter(opt => opt === 'default')
      : AP_TABLE_OPTIONS_ORDER

  return (
    <div
      className="relative ow-composer"
      onDragEnter={e => {
        handleComposerDragOver(e)
        setDragging(true)
      }}
      onDragLeave={e => {
        if (e.currentTarget.contains(e.relatedTarget as Node)) return
        setDragging(false)
      }}
      onDragOver={handleComposerDragOver}
      onDrop={e => {
        handleComposerDrop(e, onDropFiles)
        setDragging(false)
      }}
    >
      <ComposerFilesOverlay visible={dragging} onDropFiles={onDropFiles} />

      {files.length > 0 ? (
        <ul className="mb-3 flex flex-wrap gap-2">
          {files.map(f => {
            const pageLabel = formatFilePageCount(f.page_count)
            return (
            <li
              key={f.task_file_id}
              className="flex items-center gap-1 rounded-full border border-gray-200 bg-gray-50 py-1 pl-3 pr-1 text-xs dark:border-gray-700 dark:bg-gray-850"
            >
              <button
                type="button"
                className="font-medium hover:underline"
                onClick={() => onPreviewFile?.(f.task_file_id)}
                disabled={!onPreviewFile}
              >
                {f.original_filename ?? f.task_file_id}
              </button>
              {pageLabel ? <span className="text-gray-500">{pageLabel}</span> : null}
              <span className="text-gray-500">{f.file_status}</span>
              <button
                type="button"
                className="ml-1 rounded-full px-1.5 text-gray-400 hover:bg-gray-200 hover:text-gray-700 dark:hover:bg-gray-700 dark:hover:text-gray-200"
                aria-label="Remove file"
                onClick={() => onRemoveFile(f.task_file_id)}
              >
                ×
              </button>
            </li>
            )
          })}
        </ul>
      ) : null}

      {showReceiptOptions ? (
        <div className="mb-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-400">
            Workflow
            <div className="ow-input min-w-[180px] truncate bg-gray-50 text-gray-700 dark:bg-gray-850 dark:text-gray-300">
              {workflowLabel || 'Custom workflow'}
            </div>
          </label>
          <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-400">
            Receipt layout
            <select
              className="ow-input min-w-[160px]"
              value={receiptSignal ?? ''}
              onChange={e => onReceiptChange(e.target.value as ApVlmReceiptSignal)}
            >
              <option value="" disabled>
                Select…
              </option>
              {AP_RECEIPT_OPTIONS_ORDER.map(opt => (
                <option key={opt} value={opt}>
                  {RECEIPT_LABELS[opt]}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-gray-600 dark:text-gray-400">
            Table style
            <select
              className="ow-input min-w-[160px]"
              value={tablePreset ?? ''}
              onChange={e => onTablePresetChange(e.target.value as ApVlmTablePreset)}
            >
              <option value="" disabled>
                Select…
              </option>
              {tableOptions.map(opt => (
                <option key={opt} value={opt}>
                  {TABLE_LABELS[opt]}
                </option>
              ))}
            </select>
          </label>
          {receiptBlocked ? (
            <span className="text-xs text-amber-700 dark:text-amber-400">
              Receipt layout and table style are required before Run.
            </span>
          ) : null}
        </div>
      ) : null}

      <CloudAiNotice className="cloud-ai-notice--compact mb-2" />
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn-ghost rounded-full px-3" onClick={onAttach}>
          Attach
        </button>
        <button
          type="button"
          className="btn-ghost rounded-full px-3"
          onClick={onReVlm}
          disabled={busy || vlmActive || reVlmFileCount === 0}
        >
          Re-VLM
        </button>
        <span className="flex-1 text-sm text-gray-500">
          {files.length} file{files.length === 1 ? '' : 's'} queued
        </span>
        {vlmActive ? (
          <button
            type="button"
            className="btn-ghost min-w-[100px] rounded-full text-red-600 dark:text-red-400"
            onClick={onStop}
          >
            Stop
          </button>
        ) : (
          <button
            type="button"
            className="btn-primary min-w-[100px] rounded-full"
            disabled={!canRun || busy}
            onClick={onRun}
          >
            Run
          </button>
        )}
      </div>
    </div>
  )
}
