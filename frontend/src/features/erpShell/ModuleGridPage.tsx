import { useCallback, useEffect, useMemo, useState } from 'react'
import { useAuth } from '../../contexts/AuthContext'
import { workflowApi, type WorkflowRunSummary } from '../nodeWorkspace/workflowApi'
import {
  buildBatchTablePayloadsFromRun,
  loadAllBatchTablePayloads,
  mergeBatchTablePayloads,
} from '../nodeWorkspace/batchTableSnapshots'
import { hasOcrDataOnRun, tablePayloadHasRows } from '../nodeWorkspace/tablePayloadMerge'
import { api } from '../../services/api'
import { FilePreviewModal } from '../../components/filePreview'
import type { ModuleDef } from './moduleRegistry'
import { DataGridShell, type Column } from './DataGridShell'
import { FilterBar } from './FilterBar'
import { GridFooter } from './GridFooter'
import { useRowFilePreview } from './useRowFilePreview'
import { BatchDrilldown } from './BatchDrilldown'
import { ModuleTransactionGrid } from './ModuleTransactionGrid'

type Props = { module: ModuleDef }

type RunRow = WorkflowRunSummary & { uploadTime: string | null }

/** Flat transaction modules use a transaction-level grid; A&L keeps the run grid. */
const FLAT_TX_MODES = new Set(['AP', 'AR', 'BANK'])

export function ModuleGridPage({ module }: Props) {
  if (module.mode && FLAT_TX_MODES.has(module.mode)) {
    return <ModuleTransactionGrid module={module} />
  }
  return <RunGridPage module={module} />
}

function uploadTimeOf(run: WorkflowRunSummary): string | null {
  const fromBatch = run.batches?.find(b => b.uploaded_at)?.uploaded_at
  return fromBatch ?? run.created_at ?? null
}

function fmtTime(iso: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '-' : d.toLocaleString()
}

function statusBadge(status: string): { cls: string; label: string } {
  const s = status.toLowerCase()
  if (s === 'completed' || s === 'done' || s === 'saved') return { cls: 'posted', label: 'Done' }
  if (s === 'failed' || s === 'error') return { cls: 'review', label: 'Failed' }
  if (s === 'executing' || s === 'coa_running') return { cls: 'open', label: 'Running' }
  if (s === 'awaiting_review') return { cls: 'open', label: 'Review' }
  return { cls: 'open', label: status || 'Draft' }
}

function RunGridPage({ module }: Props) {
  const { activeCompany } = useAuth()
  const companyId = activeCompany?.id ?? 'default'
  const presetKey = `erp.filter.${module.id}`
  const savedPreset = (() => {
    try {
      return JSON.parse(localStorage.getItem(presetKey) || '{}') as { search?: string; status?: string }
    } catch {
      return {}
    }
  })()
  const [runs, setRuns] = useState<WorkflowRunSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState(savedPreset.search ?? '')
  const [statusFilter, setStatusFilter] = useState(savedPreset.status ?? 'all')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [deleting, setDeleting] = useState(false)
  const [previewingId, setPreviewingId] = useState<string | null>(null)
  // Lazy "has extracted data" probe result per run id (undefined = not probed yet).
  const [hasData, setHasData] = useState<Record<string, boolean>>({})
  const preview = useRowFilePreview(companyId)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const all = await workflowApi.listRuns(companyId)
      setRuns(module.mode ? all.filter(r => r.processing_mode === module.mode) : all)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load records.')
    } finally {
      setLoading(false)
    }
  }, [companyId, module.mode])

  useEffect(() => {
    setSelectedIds(new Set())
    setHasData({})
    void load()
  }, [load])

  // Lazily probe each run (limited concurrency) so the expand chevron only
  // appears for batches that actually have extracted rows / records.
  const isAssetModule = module.mode === 'OTHER'
  useEffect(() => {
    const candidates = runs.filter(r => r.file_count > 0)
    if (candidates.length === 0) return
    let cancelled = false
    const queue = [...candidates]

    const probe = async (run: WorkflowRunSummary): Promise<boolean> => {
      try {
        if (isAssetModule) {
          const { records } = await api.getOtherRecords(run.task_id, companyId)
          return records.length > 0
        }
        const full = await workflowApi.getRun(companyId, run.id)
        const loaded = await loadAllBatchTablePayloads(full, companyId)
        const payloads = hasOcrDataOnRun(full)
          ? mergeBatchTablePayloads(full, loaded, buildBatchTablePayloadsFromRun(full))
          : loaded
        return Object.values(payloads).some(p => tablePayloadHasRows(p, full.processing_mode))
      } catch {
        return false
      }
    }

    const worker = async () => {
      while (queue.length > 0 && !cancelled) {
        const run = queue.shift()!
        const result = await probe(run)
        if (cancelled) return
        setHasData(prev => ({ ...prev, [run.id]: result }))
      }
    }

    const workers = Array.from({ length: Math.min(4, queue.length) }, () => worker())
    void Promise.all(workers)
    return () => {
      cancelled = true
    }
  }, [runs, companyId, isAssetModule])

  // Persist the filter as a saved preset per module.
  useEffect(() => {
    try {
      localStorage.setItem(presetKey, JSON.stringify({ search, status: statusFilter }))
    } catch {
      /* storage may be unavailable */
    }
  }, [presetKey, search, statusFilter])

  const openPreview = useCallback(
    async (run: WorkflowRunSummary) => {
      if (run.file_count === 0) return
      setPreviewingId(run.id)
      try {
        const full = await workflowApi.getRun(companyId, run.id)
        const file = full.files?.find(f => f.task_file_id)
        if (file) {
          await preview.open(full.task_id, file.task_file_id, file.original_filename || 'document')
        }
      } catch {
        /* preview is best-effort */
      } finally {
        setPreviewingId(null)
      }
    },
    [companyId, preview],
  )

  const rows: RunRow[] = useMemo(() => {
    const q = search.trim().toLowerCase()
    return runs
      .filter(r => (statusFilter === 'all' ? true : statusBadge(r.run_status).label.toLowerCase() === statusFilter))
      .filter(r => (q ? (r.title || '').toLowerCase().includes(q) : true))
      .map(r => ({ ...r, uploadTime: uploadTimeOf(r) }))
  }, [runs, search, statusFilter])

  const columns: Column<RunRow>[] = useMemo(
    () => [
      {
        key: 'title',
        header: 'Run / Batch Name',
        value: r => r.title || 'Untitled',
        render: r => r.title || 'Untitled',
      },
      {
        key: 'status',
        header: 'Status',
        value: r => statusBadge(r.run_status).label,
        render: r => {
          const b = statusBadge(r.run_status)
          return <span className={`erp-badge ${b.cls}`}>{b.label}</span>
        },
      },
      { key: 'files', header: 'Files', numeric: true, value: r => r.file_count, render: r => r.file_count },
      { key: 'uploadTime', header: 'Upload Time', value: r => r.uploadTime, render: r => fmtTime(r.uploadTime) },
      {
        key: 'preview',
        header: 'Preview',
        render: r => (
          <button
            type="button"
            className="erp-preview-btn"
            disabled={r.file_count === 0 || previewingId === r.id}
            onClick={() => void openPreview(r)}
          >
            {previewingId === r.id ? 'Loading...' : r.file_count === 0 ? 'Manual' : 'Preview'}
          </button>
        ),
      },
    ],
    [previewingId, openPreview],
  )

  const toggleSelect = (id: string) =>
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const toggleAll = (ids: string[], select: boolean) =>
    setSelectedIds(prev => {
      const next = new Set(prev)
      ids.forEach(id => (select ? next.add(id) : next.delete(id)))
      return next
    })

  const deleteSelected = useCallback(async () => {
    const ids = Array.from(selectedIds)
    if (ids.length === 0) return
    const ok = window.confirm(
      `Delete ${ids.length} selected ${ids.length === 1 ? 'batch' : 'batches'}? ` +
        'This permanently removes the run, uploaded files, and review data. This cannot be undone.',
    )
    if (!ok) return
    setDeleting(true)
    try {
      await Promise.all(ids.map(id => workflowApi.deleteRun(companyId, id)))
      setSelectedIds(new Set())
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete the selected batches.')
    } finally {
      setDeleting(false)
    }
  }, [selectedIds, companyId, load])

  const totalFiles = rows.reduce((s, r) => s + (r.file_count || 0), 0)

  return (
    <>
      <FilterBar
        actions={
          <>
            <button
              type="button"
              className="erp-btn danger"
              disabled={selectedIds.size === 0 || deleting}
              onClick={() => void deleteSelected()}
            >
              {deleting ? 'Deleting...' : `Delete${selectedIds.size ? ` (${selectedIds.size})` : ''}`}
            </button>
            <button type="button" className="erp-btn primary" onClick={() => void load()}>
              Search
            </button>
          </>
        }
      >
        <div className="erp-field">
          Run / Batch
          <input type="text" value={search} onChange={e => setSearch(e.target.value)} />
        </div>
        <div className="erp-field">
          Status
          <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
            <option value="all">All</option>
            <option value="done">Done</option>
            <option value="running">Running</option>
            <option value="review">Review</option>
            <option value="failed">Failed</option>
            <option value="draft">Draft</option>
          </select>
        </div>
      </FilterBar>

      <DataGridShell
        columns={columns}
        rows={rows}
        getRowId={r => r.id}
        rowFlag={r => statusBadge(r.run_status).cls === 'review'}
        selectedIds={selectedIds}
        onToggleSelect={toggleSelect}
        onToggleAll={toggleAll}
        loading={loading}
        error={error}
        emptyText={`No ${module.label} batches yet. Create one in Processing.`}
        canExpand={r => hasData[r.id] === true}
        renderExpanded={r => (
          <BatchDrilldown runId={r.id} mode={module.mode ?? r.processing_mode} companyId={companyId} />
        )}
      />

      <GridFooter
        selectedCount={selectedIds.size}
        stats={[
          { label: 'Batches', value: String(rows.length) },
          { label: 'Files', value: String(totalFiles) },
        ]}
      />

      <FilePreviewModal
        open={preview.state.open}
        onClose={preview.close}
        filename={preview.state.filename}
        mimeType={preview.state.mimeType}
        previewUrl={preview.state.previewUrl}
        loading={preview.state.loading}
        error={preview.state.error}
        onDownload={preview.download}
      />
    </>
  )
}
