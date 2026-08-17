import { useEffect, useMemo, useState } from 'react'
import {
  workflowApi,
  type WorkflowRun,
  type WorkflowRunFile,
} from '../nodeWorkspace/workflowApi'
import {
  loadAllBatchTablePayloads,
  buildBatchTablePayloadsFromRun,
  persistBatchTableSnapshot,
  frozenPresetForBatch,
} from '../nodeWorkspace/batchTableSnapshots'
import { ARAPReview, type ARAPTransaction } from '../../components/ARAPReview'
import { BankStatementReview, type BankTransaction } from '../../components/BankStatementReview'
import {
  runDeployAccountCodes,
  applyCodeMapToArap,
  applyCodeMapToBank,
  loadCoaNameByCode,
} from '../workspace/deployCodes'
import { OtherTable } from '../../components/OtherTable'
import type { OtherRow } from '../../types/other'
import { FileStatusIcon } from '../nodeWorkspace/shell/FileStatusIcon'
import { tablePayloadHasRows } from '../nodeWorkspace/tablePayloadMerge'
import { api } from '../../services/api'
import { reconciliationApi } from '../../services/reconciliation'
import { assetSourceLabel, filesByIdFromRun } from '../../utils/rowSourceLabel'
import { coaOptionLabel } from '../../utils/coaDisplay'

type Props = {
  runId: string
  mode: string
  companyId: string
}

type UploadBatch = {
  uploadBatchId: string
  files: WorkflowRunFile[]
}

function groupUploadBatches(files: WorkflowRunFile[]): UploadBatch[] {
  const byBatch = new Map<string, WorkflowRunFile[]>()
  for (const f of files) {
    const key = f.upload_batch_id ?? f.task_file_id
    const list = byBatch.get(key) ?? []
    list.push(f)
    byBatch.set(key, list)
  }
  return Array.from(byBatch.entries()).map(([uploadBatchId, batchFiles]) => ({
    uploadBatchId,
    files: batchFiles.sort((a, b) =>
      (a.uploaded_at ?? '').localeCompare(b.uploaded_at ?? '') || a.task_file_id.localeCompare(b.task_file_id),
    ),
  }))
}

export function BatchDrilldown({ runId, mode, companyId }: Props) {
  const upper = (mode || '').toUpperCase()
  const isAsset = upper === 'OTHER'
  const isBank = upper === 'BANK'

  const [run, setRun] = useState<WorkflowRun | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [payloads, setPayloads] = useState<Record<string, Record<string, unknown>>>({})
  const [assetRecords, setAssetRecords] = useState<OtherRow[]>([])
  const [saving, setSaving] = useState(false)
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
  }, [companyId])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    workflowApi
      .getRun(companyId, runId)
      .then(async r => {
        if (cancelled) return
        setRun(r)
        if (isAsset) {
          const { records } = await api.getOtherRecords(r.task_id, companyId)
          if (!cancelled) {
            const filesById = filesByIdFromRun(r.files)
            setAssetRecords(
              records.map(rec => ({
                id: rec.id,
                record_type: rec.record_type as 'loan' | 'fixed_asset',
                source_file_id: rec.source_file_id ?? undefined,
                source_file_label: assetSourceLabel(
                  { ...rec.payload_json, source_file_id: rec.source_file_id },
                  filesById,
                ),
                ...rec.payload_json,
              })),
            )
          }
        } else {
          let loaded = await loadAllBatchTablePayloads(r, companyId)
          if (Object.keys(loaded).length === 0) loaded = buildBatchTablePayloadsFromRun(r)
          if (!cancelled) setPayloads(loaded)
        }
      })
      .catch(e => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [runId, companyId, isAsset])

  const batches = useMemo(() => (run ? groupUploadBatches(run.files) : []), [run])

  async function saveBatch(batchId: string, payload: Record<string, unknown>) {
    if (!run) return
    setSaving(true)
    try {
      const next = { ...payloads, [batchId]: payload }
      setPayloads(next)
      await persistBatchTableSnapshot(
        run,
        batchId,
        payload,
        frozenPresetForBatch(run, batchId),
        companyId,
        { fromModule: true },
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  // Manual Chart of Accounts deploy lives in the destination module (not Processing).
  async function deployBatch(batchId: string, payload: Record<string, unknown>) {
    if (!run) return
    setSaving(true)
    try {
      if (isBank) {
        const rows = (payload.bankTransactions as BankTransaction[] | undefined) ?? []
        const codeMap = await runDeployAccountCodes('BANK', undefined, rows)
        const nameByCode = await loadCoaNameByCode('BANK')
        await saveBatch(batchId, {
          ...payload,
          bankTransactions: applyCodeMapToBank(rows, codeMap, nameByCode),
        })
      } else {
        const rows = (payload.arapTransactions as ARAPTransaction[] | undefined) ?? []
        const codeMap = await runDeployAccountCodes(upper, rows)
        const nameByCode = await loadCoaNameByCode(upper)
        await saveBatch(batchId, { ...payload, arapTransactions: applyCodeMapToArap(rows, codeMap, nameByCode) })
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="erp-empty">Loading transactions...</div>
  if (error) return <div className="erp-empty">Failed to load: {error}</div>
  if (!run) return null

  if (isAsset) {
    if (assetRecords.length === 0) return <div className="erp-empty">No asset / liability records.</div>
    return (
      <OtherTable
        records={assetRecords}
        onRecordChange={async (recordId, updated) => {
          try {
            await api.updateOtherRecord(recordId, updated as Record<string, unknown>)
            setAssetRecords(prev => prev.map(r => (r.id === recordId ? updated : r)))
          } catch (e) {
            setError(e instanceof Error ? e.message : String(e))
          }
        }}
      />
    )
  }

  if (batches.length === 0) return <div className="erp-empty">No uploaded files.</div>

  return (
    <div>
      {batches.map(batch => {
        const payload = payloads[batch.uploadBatchId] ?? {}
        const hasRows = tablePayloadHasRows(payload, run.processing_mode)
        const arap = (payload.arapTransactions as ARAPTransaction[] | undefined) ?? []
        const bank = (payload.bankTransactions as BankTransaction[] | undefined) ?? []
        return (
          <div className="erp-batch-block" key={batch.uploadBatchId}>
            <ul className="erp-batch-files">
              {batch.files.map(f => (
                <li key={f.task_file_id} className="erp-batch-file">
                  <FileStatusIcon status={f.file_status} />
                  <span className="fname">{f.original_filename?.trim() || f.task_file_id}</span>
                  <span className="fstat">{f.file_status}</span>
                </li>
              ))}
            </ul>
            {hasRows ? (
              <div className="erp-file-table">
                {isBank ? (
                  <BankStatementReview
                    transactions={bank}
                    filename="workflow"
                    coaOptions={coaOptions}
                    onDeploy={() => void deployBatch(batch.uploadBatchId, payload)}
                    onDataChange={rows => void saveBatch(batch.uploadBatchId, { ...payload, bankTransactions: rows })}
                  />
                ) : (
                  <ARAPReview
                    transactions={arap}
                    filename="workflow"
                    useApTableSchema={upper === 'AP'}
                    coaOptionsByType={{ AR: coaOptions, AP: coaOptions }}
                    onDeploy={() => void deployBatch(batch.uploadBatchId, payload)}
                    onDataChange={rows => void saveBatch(batch.uploadBatchId, { ...payload, arapTransactions: rows })}
                  />
                )}
              </div>
            ) : (
              <div className="erp-empty erp-batch-empty">No extracted rows for this batch yet.</div>
            )}
          </div>
        )
      })}
      {saving && <div className="erp-empty">Saving...</div>}
    </div>
  )
}
