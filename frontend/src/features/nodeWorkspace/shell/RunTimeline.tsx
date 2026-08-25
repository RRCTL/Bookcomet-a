import { useEffect, useState, useCallback } from 'react'
import { ARAPReview, type ARAPTransaction } from '../../../components/ARAPReview'
import { BankStatementReview, type BankTransaction } from '../../../components/BankStatementReview'
import {
  runDeployAccountCodes,
  applyCodeMapToArap,
  applyCodeMapToBank,
  loadCoaNameByCode,
} from '../../workspace/deployCodes'
import { reconciliationApi } from '../../../services/reconciliation'
import { coaOptionLabel } from '../../../utils/coaDisplay'
import type { WorkflowRun, WorkflowRunFile } from '../workflowApi'
import {
  batchPayloadRowCount,
  frozenPresetForBatch,
  tablePresetLabel,
} from '../batchTableSnapshots'
import { tablePayloadHasRows } from '../tablePayloadMerge'
import {
  committedTimelineBatches,
  composerStagingFiles,
  formatBatchUploadedAt,
} from '../runFileBatches'
import { RunEmptyPlaceholder } from './RunEmptyPlaceholder'
import { FileStatusIcon } from './FileStatusIcon'

type Props = {
  run: WorkflowRun | null
  suggestedMode?: string
  batchTablePayloads: Record<string, Record<string, unknown>>
  /** Increment to expand all batch file lists and table sections. */
  expandAllTablesNonce?: number
  onBatchTableChange: (uploadBatchId: string, payload: Record<string, unknown>) => void
  onMoveFileToBatch: (sourceBatchId: string, targetBatchId: string, fileId: string) => void
  onApprove: () => void
  onSkipCoa: () => void
  onRetryFile: (taskFileId: string) => void
  onPreviewFile?: (taskFileId: string) => void
  onForceProcess: (taskFileId: string) => void
  coaBusy: boolean
  canApprove: boolean
  onNewRun?: () => void
  /** When true, Retry / Force (Re-VLM paths) are disabled. */
  reVlmLocked?: boolean
}

function batchFilesSummary(files: WorkflowRunFile[]): string {
  if (files.length === 0) return 'No files'
  const counts: Record<string, number> = {}
  for (const f of files) {
    counts[f.file_status] = (counts[f.file_status] ?? 0) + 1
  }
  const parts = Object.entries(counts).map(([status, n]) => `${n} ${status}`)
  return `${files.length} file${files.length === 1 ? '' : 's'} · ${parts.join(' · ')}`
}

export function RunTimeline({
  run,
  suggestedMode,
  batchTablePayloads,
  expandAllTablesNonce,
  onBatchTableChange,
  onMoveFileToBatch,
  onApprove,
  onSkipCoa,
  onRetryFile,
  onPreviewFile,
  onForceProcess,
  coaBusy,
  canApprove,
  onNewRun,
  reVlmLocked = false,
}: Props) {
  const [batchExpandedById, setBatchExpandedById] = useState<Record<string, boolean>>({})
  const [tableExpandedById, setTableExpandedById] = useState<Record<string, boolean>>({})

  useEffect(() => {
    if (!run || expandAllTablesNonce == null || expandAllTablesNonce === 0) return
    const ids = committedTimelineBatches(run.files).map(b => b.uploadBatchId)
    setBatchExpandedById(prev => {
      const next = { ...prev }
      for (const id of ids) next[id] = true
      return next
    })
    setTableExpandedById(prev => {
      const next = { ...prev }
      for (const id of ids) next[id] = true
      return next
    })
  }, [expandAllTablesNonce, run])

  if (!run) {
    return <RunEmptyPlaceholder suggestedMode={suggestedMode} onNewRun={onNewRun ?? (() => {})} />
  }

  const coaState = run.node_states_json?.coa as { status?: string; detail?: unknown } | undefined
  const saveState = run.node_states_json?.save as { status?: string } | undefined
  const timelineBatches = committedTimelineBatches(run.files)
  const stagedCount = composerStagingFiles(run.files).length
  const anyTableRows = timelineBatches.some(
    b => batchPayloadRowCount(batchTablePayloads[b.uploadBatchId], run.processing_mode) > 0,
  )
  const showBatchTables =
    run.run_status === 'awaiting_review' ||
    run.run_status === 'coa_running' ||
    run.run_status === 'completed' ||
    run.run_status === 'executing' ||
    (run.run_status === 'draft' && anyTableRows)
  const showApproveBar = anyTableRows && run.run_status === 'awaiting_review'

  return (
    <div className="space-y-6 p-4 md:p-6">
      {timelineBatches.length === 0 ? (
        <section className="ow-card overflow-hidden p-4">
          <p className="text-sm text-gray-500">
            {stagedCount > 0
              ? `${stagedCount} file${stagedCount === 1 ? '' : 's'} staged in the composer. Click Run to start VLM.`
              : 'Attach files in the composer, then Run.'}
          </p>
        </section>
      ) : (
        timelineBatches.map((batch, index) => {
          const batchPayload = batchTablePayloads[batch.uploadBatchId] ?? {}
          const preset = frozenPresetForBatch(run, batch.uploadBatchId)
          const hasRows = tablePayloadHasRows(batchPayload, run.processing_mode)
          const moveTargets = timelineBatches.filter(b => b.uploadBatchId !== batch.uploadBatchId)
          const batchOpen = batchExpandedById[batch.uploadBatchId] ?? true
          const tableOpen = tableExpandedById[batch.uploadBatchId] ?? true

          return (
            <section key={batch.uploadBatchId} className="ow-card overflow-hidden">
              <button
                type="button"
                className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-semibold hover:bg-gray-50 dark:hover:bg-gray-800"
                onClick={() =>
                  setBatchExpandedById(prev => ({
                    ...prev,
                    [batch.uploadBatchId]: !(prev[batch.uploadBatchId] ?? true),
                  }))
                }
              >
                <span>
                  Batch {index + 1}
                  <span className="ml-2 text-xs font-normal text-gray-500">
                    {tablePresetLabel(preset, run.processing_mode)}
                  </span>
                  {batch.uploadedAt ? (
                    <span className="ml-2 text-xs font-normal text-gray-500">
                      Uploaded {formatBatchUploadedAt(batch.uploadedAt)}
                    </span>
                  ) : null}
                </span>
                <span className="text-xs font-normal text-gray-500">
                  {batchOpen ? 'Collapse' : 'Expand'}
                </span>
              </button>
              {batchOpen ? (
                <ul className="space-y-2 border-t border-gray-100 px-4 pb-4 pt-2 dark:border-gray-800">
                  {batch.files.map(f => (
                    <FileCard
                      key={f.task_file_id}
                      file={f}
                      moveTargets={moveTargets.map(b => ({
                        id: b.uploadBatchId,
                        label: `Batch ${timelineBatches.findIndex(x => x.uploadBatchId === b.uploadBatchId) + 1}`,
                      }))}
                      onMoveToBatch={targetBatchId =>
                        onMoveFileToBatch(batch.uploadBatchId, targetBatchId, f.task_file_id)
                      }
                      onRetry={() => onRetryFile(f.task_file_id)}
                      onPreview={() => onPreviewFile?.(f.task_file_id)}
                      onForce={() => onForceProcess(f.task_file_id)}
                      reVlmLocked={reVlmLocked}
                    />
                  ))}
                </ul>
              ) : (
                <p className="border-t border-gray-100 px-4 pb-4 text-sm text-gray-500 dark:border-gray-800">
                  {batchFilesSummary(batch.files)}
                </p>
              )}

              {showBatchTables && hasRows ? (
                <div className="border-t border-gray-100 dark:border-gray-800">
                  <button
                    type="button"
                    className="flex w-full items-center justify-between px-4 py-2 text-left text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-800"
                    onClick={() =>
                      setTableExpandedById(prev => ({
                        ...prev,
                        [batch.uploadBatchId]: !(prev[batch.uploadBatchId] ?? true),
                      }))
                    }
                  >
                    <span>Table review</span>
                    <span className="text-xs font-normal text-gray-500">
                      {tableOpen ? 'Collapse' : 'Expand'}
                    </span>
                  </button>
                  {run.run_status === 'executing' ? (
                    <p className="border-t border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
                      VLM is processing files. Rows update as each file completes.
                    </p>
                  ) : null}
                  {tableOpen ? (
                    <BatchTableArtifact
                      mode={run.processing_mode}
                      tablePayload={batchPayload}
                      tablePreset={preset}
                      onTableChange={payload => onBatchTableChange(batch.uploadBatchId, payload)}
                    />
                  ) : null}
                </div>
              ) : null}
            </section>
          )
        })
      )}

      {showApproveBar ? (
        <section className="ow-card flex justify-end gap-2 p-3">
          <button type="button" className="btn-secondary" disabled={coaBusy} onClick={onSkipCoa}>
            Skip CoA
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!canApprove || coaBusy}
            onClick={onApprove}
          >
            {coaBusy ? 'Deploying…' : 'Approve'}
          </button>
        </section>
      ) : null}

      {coaState?.status === 'completed' || coaState?.status === 'running' || coaState?.status === 'skipped' ? (
        <section className="ow-card p-4">
          <h2 className="mb-2 text-sm font-semibold">Chart of accounts</h2>
          <p className="text-sm text-gray-600 dark:text-gray-400">
            Status: {coaState.status}
            {coaState.detail ? ` · ${JSON.stringify(coaState.detail)}` : ''}
          </p>
        </section>
      ) : null}

      {saveState?.status === 'completed' ? (
        <section className="ow-card border-green-200 p-4 dark:border-green-900">
          <p className="text-sm font-medium text-green-800 dark:text-green-300">Run saved successfully.</p>
        </section>
      ) : null}
    </div>
  )
}

function FileCard({
  file,
  moveTargets,
  onMoveToBatch,
  onRetry,
  onPreview,
  onForce,
  reVlmLocked = false,
}: {
  file: WorkflowRunFile
  moveTargets: { id: string; label: string }[]
  onMoveToBatch: (targetBatchId: string) => void
  onRetry: () => void
  onPreview?: () => void
  onForce: () => void
  reVlmLocked?: boolean
}) {
  const [moveTarget, setMoveTarget] = useState('')
  const tone =
    file.file_status === 'ok'
      ? 'text-green-700 dark:text-green-400'
      : file.file_status === 'failed'
        ? 'text-red-700 dark:text-red-400'
        : 'text-amber-700 dark:text-amber-400'

  const showRetry =
    file.file_status === 'ok' ||
    file.file_status === 'failed' ||
    file.file_status === 'warning' ||
    file.file_status === 'pending' ||
    file.file_status === 'running'
  const showForce = file.file_status === 'failed' || file.file_status === 'warning'
  const showMove = moveTargets.length > 0 && file.file_status === 'ok'
  const reVlmLockedTitle =
    'Approved and loaded into modules — Re-VLM is disabled to avoid conflicting updates.'

  return (
    <li className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 dark:border-gray-800 dark:bg-gray-900">
      <div className="min-w-0 flex items-start gap-2">
        <FileStatusIcon status={file.file_status} />
        <div className="min-w-0">
        <button
          type="button"
          className="truncate text-sm font-medium hover:underline"
          onClick={() => onPreview?.()}
          disabled={!onPreview}
        >
          {file.original_filename ?? file.task_file_id}
        </button>
        <div className={`text-xs ${tone}`}>
          {file.file_status}
          {file.gate_result ? ` · gate: ${file.gate_result}` : ''}
          {file.error_text ? ` · ${file.error_text}` : ''}
        </div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1">
        {showMove ? (
          <>
            <select
              className="rounded border border-gray-200 bg-white px-2 py-1 text-xs dark:border-gray-700 dark:bg-gray-900"
              value={moveTarget}
              onChange={e => setMoveTarget(e.target.value)}
            >
              <option value="">Move to…</option>
              {moveTargets.map(t => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn-secondary px-2 py-1 text-xs"
              disabled={!moveTarget}
              onClick={() => {
                if (!moveTarget) return
                onMoveToBatch(moveTarget)
                setMoveTarget('')
              }}
            >
              Move
            </button>
          </>
        ) : null}
        {showRetry ? (
          <button
            type="button"
            className="btn-secondary px-2 py-1 text-xs"
            onClick={onRetry}
            disabled={reVlmLocked}
            title={reVlmLocked ? reVlmLockedTitle : undefined}
          >
            Retry
          </button>
        ) : null}
        {showForce ? (
          <button
            type="button"
            className="btn-secondary px-2 py-1 text-xs"
            onClick={onForce}
            disabled={reVlmLocked}
            title={reVlmLocked ? reVlmLockedTitle : undefined}
          >
            Force
          </button>
        ) : null}
      </div>
    </li>
  )
}

function BatchTableArtifact({
  mode,
  tablePayload,
  tablePreset,
  onTableChange,
}: {
  mode: string
  tablePayload: Record<string, unknown>
  tablePreset: string
  onTableChange: (p: Record<string, unknown>) => void
}) {
  const m = mode.toUpperCase()
  const arap = (tablePayload.arapTransactions as ARAPTransaction[]) ?? []
  const bank = (tablePayload.bankTransactions as BankTransaction[]) ?? []
  const [coaOptions, setCoaOptions] = useState<string[]>([])

  useEffect(() => {
    let cancelled = false
    reconciliationApi
      .getChartOfAccounts()
      .then(res => {
        if (!cancelled) setCoaOptions((res.accounts || []).map(a => coaOptionLabel(a)).filter(Boolean))
      })
      .catch(() => {
        if (!cancelled) setCoaOptions([])
      })
    return () => {
      cancelled = true
    }
  }, [])

  const handleDeploy = useCallback(async () => {
    if (m === 'BANK') {
      const codeMap = await runDeployAccountCodes('BANK', undefined, bank)
      const nameByCode = await loadCoaNameByCode('BANK')
      onTableChange({
        ...tablePayload,
        bankTransactions: applyCodeMapToBank(bank, codeMap, nameByCode),
      })
    } else {
      const codeMap = await runDeployAccountCodes(m, arap)
      const nameByCode = await loadCoaNameByCode(m)
      onTableChange({
        ...tablePayload,
        arapTransactions: applyCodeMapToArap(arap, codeMap, nameByCode),
      })
    }
  }, [m, arap, bank, tablePayload, onTableChange])

  return (
    <div className="review-panel border-t border-gray-100 dark:border-gray-800">
      <div className="max-h-[50vh] overflow-auto p-4">
        {m === 'BANK' ? (
          <BankStatementReview
            transactions={bank}
            filename="workflow"
            coaOptions={coaOptions}
            onDeploy={() => void handleDeploy()}
            onDataChange={rows => onTableChange({ ...tablePayload, bankTransactions: rows })}
          />
        ) : (
          <ARAPReview
            transactions={arap}
            filename="workflow"
            useApTableSchema={tablePreset === 'ap_table'}
            coaOptionsByType={{ AR: coaOptions, AP: coaOptions }}
            onDeploy={() => void handleDeploy()}
            onDataChange={rows => onTableChange({ ...tablePayload, arapTransactions: rows })}
          />
        )}
      </div>
    </div>
  )
}
