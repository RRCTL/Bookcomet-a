import { useCallback, useEffect, useRef, useState } from 'react'
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
import { ERP_COA_DEPLOY_COMPLETE, useErpBackgroundJobs } from './erpBackgroundJobs'
import { coalesceBankAccountTypeRows } from '../../utils/bankAccountTypeCoalesce'
import {
  applyDebitCreditSide,
  defaultDrCr,
  hydrateDebitCredit,
} from './arapDebitCredit'
import { isModuleTxnLocked } from '../recon/moduleReconKeys'
import { syncModulesToRecon } from '../recon/syncModulesToRecon'

type Tx = Record<string, any>

/** One flattened, editable transaction with its owning run / batch attribution. */
export type FlatRow = {
  key: string
  runId: string
  batchId: string
  runTitle: string
  vlmAt: string | null
  runStatus: string
  taskId: string
  fileId: string | null
  filename: string
  tx: Tx
}

/** Title for runs created from Books Add Row / Import CSV when the module has no VLM batches yet. */
export const MANUAL_MODULE_RUN_TITLE = 'Manual / CSV entry'

export function isManualModuleRunTitle(title: string | null | undefined): boolean {
  return (title || '').trim() === MANUAL_MODULE_RUN_TITLE
}

/**
 * Runs whose rows belong in the destination module. Only approved runs appear:
 * awaiting_review (pre-approval) is excluded so unapproved rows stay in Processing
 * until the user approves. coa_running onward means approval already happened.
 *
 * Manual / CSV entry drafts are included so Add Row / Import work before any VLM run exists.
 */
const INCLUDED_STATUSES = new Set([
  'coa_running',
  'completed',
  'done',
  'saved',
])

function isModuleRunCandidate(run: {
  processing_removed_at?: string | null
  run_status: string
  title?: string | null
}): boolean {
  if (isManualModuleRunTitle(run.title)) return true
  if (run.processing_removed_at) return false
  return INCLUDED_STATUSES.has(run.run_status.toLowerCase())
}

/** VLM finished timestamp: the VLM/merge node `finished_at`, else the latest across nodes. */
export function vlmFinishedAt(run: WorkflowRun): string | null {
  const ns = run.node_states_json
  if (!ns || typeof ns !== 'object') return null
  const states = ns as Record<string, any>
  const direct = states.merge?.finished_at ?? states.vlm?.finished_at
  if (typeof direct === 'string') return direct
  let latest: string | null = null
  for (const v of Object.values(states)) {
    const f = v && typeof v === 'object' ? (v as any).finished_at : undefined
    if (typeof f === 'string' && (!latest || f > latest)) latest = f
  }
  return latest
}

function resolveFile(run: WorkflowRun, batchId: string, sourceFile: string): WorkflowRunFile | undefined {
  const batchFiles = run.files.filter(f => (f.upload_batch_id ?? f.task_file_id) === batchId)
  const sf = sourceFile.trim()
  if (sf) {
    const match = batchFiles.find(f => {
      const name = f.original_filename?.trim()
      if (!name) return false
      const stem = name.replace(/\.[^.]+$/, '')
      return sf === name || sf.includes(name) || (stem && sf.startsWith(stem))
    })
    if (match) return match
  }
  return batchFiles[0]
}

async function mapWithConcurrency<T, R>(items: T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const out: R[] = new Array(items.length)
  let cursor = 0
  const worker = async () => {
    while (cursor < items.length) {
      const i = cursor++
      out[i] = await fn(items[i]!)
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, () => worker()))
  return out
}

export function useModuleTransactions(mode: string, companyId: string) {
  const upper = (mode || '').toUpperCase()
  const isBank = upper === 'BANK'
  const { startCoaDeploy, isCoaDeploying } = useErpBackgroundJobs()

  const [rows, setRows] = useState<FlatRow[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [preparing, setPreparing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dirty, setDirty] = useState<Set<string>>(new Set())

  // Saving / deploy context kept in refs so callbacks stay stable.
  const runsRef = useRef<Map<string, WorkflowRun>>(new Map())
  const basePayloadsRef = useRef<Map<string, Tx>>(new Map())
  const batchInfoRef = useRef<Map<string, { runId: string; batchId: string }>>(new Map())
  const seqRef = useRef(0)
  /** In-session manual batch created when the module had no existing VLM/approved rows. */
  const pendingManualRef = useRef<{ runId: string; batchId: string; run: WorkflowRun } | null>(null)
  const ensuringManualRef = useRef<Promise<{ runId: string; batchId: string; run: WorkflowRun }> | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const all = await workflowApi.listRuns(companyId)
      const candidates = all.filter(
        r => (r.processing_mode || '').toUpperCase() === upper && isModuleRunCandidate(r),
      )
      pendingManualRef.current = null
      ensuringManualRef.current = null
      const runsMap = new Map<string, WorkflowRun>()
      const base = new Map<string, Tx>()
      const info = new Map<string, { runId: string; batchId: string }>()
      const next: FlatRow[] = []

      await mapWithConcurrency(candidates, 4, async summary => {
        const full = await workflowApi.getRun(companyId, summary.id)
        runsMap.set(full.id, full)
        let loaded = await loadAllBatchTablePayloads(full, companyId)
        if (Object.keys(loaded).length === 0) loaded = buildBatchTablePayloadsFromRun(full)
        const vlmAt = vlmFinishedAt(full)
        const title = full.title || 'Untitled'
        for (const [batchId, payload] of Object.entries(loaded)) {
          const key = `${full.id}::${batchId}`
          base.set(key, payload)
          info.set(key, { runId: full.id, batchId })
          let arr = (isBank ? payload.bankTransactions : payload.arapTransactions) as Tx[] | undefined
          if (isBank && arr?.length) {
            arr = coalesceBankAccountTypeRows(arr.map(t => ({ ...t }))) as Tx[]
          }
          ;(arr ?? []).forEach(rawTx => {
            const tx = isBank
              ? rawTx
              : { ...rawTx, ...hydrateDebitCredit(rawTx, upper) }
            const file = resolveFile(full, batchId, String(tx.source_file ?? ''))
            next.push({
              key: `${full.id}::${batchId}::${seqRef.current++}`,
              runId: full.id,
              batchId,
              runTitle: title,
              vlmAt,
              runStatus: full.run_status,
              taskId: full.task_id,
              fileId: file?.task_file_id ?? null,
              filename: file?.original_filename?.trim() || '',
              tx,
            })
          })
        }
        // Empty manual containers still need a batch key so Add/Import can attach without creating another run.
        if (isManualModuleRunTitle(title) && Object.keys(loaded).length === 0) {
          const batchId = `manual-${full.id}`
          const key = `${full.id}::${batchId}`
          base.set(key, isBank ? { bankTransactions: [] } : { arapTransactions: [] })
          info.set(key, { runId: full.id, batchId })
        }
      })

      runsRef.current = runsMap
      basePayloadsRef.current = base
      batchInfoRef.current = info
      // Prefer an existing manual container for the next empty-module Add/Import.
      for (const meta of info.values()) {
        const run = runsMap.get(meta.runId)
        if (run && isManualModuleRunTitle(run.title)) {
          pendingManualRef.current = { runId: meta.runId, batchId: meta.batchId, run }
          break
        }
      }
      setRows(next)
      setDirty(new Set())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load transactions.')
    } finally {
      setLoading(false)
    }
  }, [companyId, upper, isBank])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    const onComplete = (event: Event) => {
      const detail = (event as CustomEvent<{ mode?: string; companyId?: string; failed?: string }>).detail
      if ((detail.mode || '').toUpperCase() !== upper) return
      if (detail.companyId !== companyId) return
      if (detail.failed) setError(detail.failed)
      void reload()
    }
    window.addEventListener(ERP_COA_DEPLOY_COMPLETE, onComplete)
    return () => window.removeEventListener(ERP_COA_DEPLOY_COMPLETE, onComplete)
  }, [upper, companyId, reload])

  const markDirty = useCallback((runId: string, batchId: string) => {
    setDirty(prev => new Set(prev).add(`${runId}::${batchId}`))
  }, [])

  const updateCell = useCallback(
    (key: string, field: string, value: unknown) => {
      setRows(prev =>
        prev.map(r => {
          if (r.key !== key) return r
          if (isModuleTxnLocked(r.tx)) return r
          markDirty(r.runId, r.batchId)
          return { ...r, tx: { ...r.tx, [field]: value } }
        }),
      )
    },
    [markDirty],
  )

  /** Set Account / GL code and keep Category (CoA name) in sync. */
  const updateAccountCode = useCallback(
    (key: string, code: string, categoryName: string) => {
      setRows(prev =>
        prev.map(r => {
          if (r.key !== key) return r
          if (isModuleTxnLocked(r.tx)) return r
          markDirty(r.runId, r.batchId)
          return { ...r, tx: { ...r.tx, account_code: code, category: categoryName } }
        }),
      )
    },
    [markDirty],
  )

  const updateDebitCredit = useCallback(
    (key: string, side: 'debit' | 'credit', value: number | null) => {
      const synced = applyDebitCreditSide(side, value)
      setRows(prev =>
        prev.map(r => {
          if (r.key !== key) return r
          if (isModuleTxnLocked(r.tx)) return r
          markDirty(r.runId, r.batchId)
          return { ...r, tx: { ...r.tx, ...synced } }
        }),
      )
    },
    [markDirty],
  )

  const updateBankAccountType = useCallback(
    (key: string, value: string) => {
      const v = String(value)
      setRows(prev =>
        prev.map(r => {
          if (r.key !== key) return r
          if (isModuleTxnLocked(r.tx)) return r
          // OCR / imported bank rows keep a fixed account; only Add Row (manual_entry) can change it.
          if (r.tx?.manual_entry !== true) return r
          markDirty(r.runId, r.batchId)
          return {
            ...r,
            tx: {
              ...r.tx,
              account_type: v,
              賬戶類型: v,
              帳戶類型: v,
              账户类型: v,
            },
          }
        }),
      )
    },
    [markDirty],
  )

  const resolveAnchor = useCallback((prev: FlatRow[], selectedKeys: Set<string>): FlatRow | null => {
    if (prev.length === 0) return null
    return (
      (selectedKeys.size > 0 && prev.find(r => selectedKeys.has(r.key))) ||
      [...prev].sort((a, b) => (b.vlmAt ?? '').localeCompare(a.vlmAt ?? ''))[0] ||
      null
    )
  }, [])

  /** Create (or reuse) a manual run+batch when the module has no existing rows to attach to. */
  const ensureManualBatch = useCallback(async (): Promise<{ runId: string; batchId: string; run: WorkflowRun }> => {
    if (pendingManualRef.current) return pendingManualRef.current

    for (const meta of batchInfoRef.current.values()) {
      const run = runsRef.current.get(meta.runId)
      if (run && isManualModuleRunTitle(run.title)) {
        const info = { runId: meta.runId, batchId: meta.batchId, run }
        pendingManualRef.current = info
        return info
      }
    }

    if (ensuringManualRef.current) return ensuringManualRef.current

    const pending = (async () => {
      const created = await workflowApi.createRun(companyId, upper)
      const run = await workflowApi.patchRunMeta(companyId, created.id, {
        title: MANUAL_MODULE_RUN_TITLE,
        // Keep Processing clean — Books owns this container until VLM batches exist.
        remove_from_processing: true,
      })
      const batchId = `manual-${crypto.randomUUID()}`
      const key = `${run.id}::${batchId}`
      runsRef.current.set(run.id, run)
      batchInfoRef.current.set(key, { runId: run.id, batchId })
      basePayloadsRef.current.set(key, isBank ? { bankTransactions: [] } : { arapTransactions: [] })
      const info = { runId: run.id, batchId, run }
      pendingManualRef.current = info
      return info
    })()

    ensuringManualRef.current = pending
    try {
      return await pending
    } finally {
      if (ensuringManualRef.current === pending) ensuringManualRef.current = null
    }
  }, [companyId, upper, isBank])

  const blankTx = useCallback(
    (batchId: string): Tx =>
      isBank
        ? {
            id_number: '',
            date: new Date().toISOString().slice(0, 10),
            account_type: '',
            particulars: '',
            deposit: null,
            withdrawal: null,
            balance: undefined,
            currency: 'HKD',
            account_code: '',
            category: '',
            source_file: '',
            manual_entry: true,
            upload_batch_id: batchId,
          }
        : {
            id_number: '',
            date: new Date().toISOString().slice(0, 10),
            transaction_type: upper === 'AP' ? 'AP' : 'AR',
            amount: null,
            debit: null,
            credit: null,
            dr_cr: defaultDrCr(upper),
            currency: 'HKD',
            payer: '',
            payee: '',
            bank: '',
            account_code: '',
            category: '',
            memo: '',
            source_file: '',
            manual_entry: true,
            upload_batch_id: batchId,
          },
    [isBank, upper],
  )

  const addRow = useCallback(
    async (selectedKeys: Set<string>) => {
      setError(null)
      try {
        const meta = resolveAnchor(rows, selectedKeys)
        let runId = meta?.runId
        let batchId = meta?.batchId
        let runTitle = meta?.runTitle
        let vlmAt = meta?.vlmAt ?? null
        let runStatus = meta?.runStatus
        let taskId = meta?.taskId

        if (!runId || !batchId) {
          setPreparing(true)
          const manual = await ensureManualBatch()
          runId = manual.runId
          batchId = manual.batchId
          runTitle = manual.run.title || MANUAL_MODULE_RUN_TITLE
          vlmAt = null
          runStatus = manual.run.run_status
          taskId = manual.run.task_id
        }

        const anchorRunId = runId
        const anchorBatchId = batchId
        const anchorTitle = runTitle || MANUAL_MODULE_RUN_TITLE
        const anchorVlmAt = vlmAt
        const anchorStatus = runStatus || 'draft'
        const anchorTaskId = taskId || ''

        setRows(prev => {
          const live = resolveAnchor(prev, selectedKeys)
          const useRunId = live?.runId ?? anchorRunId
          const useBatchId = live?.batchId ?? anchorBatchId
          const newRow: FlatRow = {
            key: `${useRunId}::${useBatchId}::${seqRef.current++}`,
            runId: useRunId,
            batchId: useBatchId,
            runTitle: live?.runTitle ?? anchorTitle,
            vlmAt: live?.vlmAt ?? anchorVlmAt,
            runStatus: live?.runStatus ?? anchorStatus,
            taskId: live?.taskId ?? anchorTaskId,
            fileId: null,
            filename: '',
            tx: blankTx(useBatchId),
          }
          markDirty(useRunId, useBatchId)
          if (live) {
            const idx = prev.findIndex(r => r.key === live.key)
            const next = [...prev]
            next.splice(idx + 1, 0, newRow)
            return next
          }
          return [...prev, newRow]
        })
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not add a row.')
      } finally {
        setPreparing(false)
      }
    },
    [rows, resolveAnchor, ensureManualBatch, blankTx, markDirty],
  )

  /** Append CSV-imported manual rows onto the selected (or latest) batch — Save to persist. */
  const importRows = useCallback(
    async (txs: Tx[], selectedKeys: Set<string>): Promise<number> => {
      if (txs.length === 0) return 0
      setError(null)
      try {
        const meta = resolveAnchor(rows, selectedKeys)
        let runId = meta?.runId
        let batchId = meta?.batchId
        let runTitle = meta?.runTitle
        let vlmAt = meta?.vlmAt ?? null
        let runStatus = meta?.runStatus
        let taskId = meta?.taskId

        if (!runId || !batchId) {
          setPreparing(true)
          const manual = await ensureManualBatch()
          runId = manual.runId
          batchId = manual.batchId
          runTitle = manual.run.title || MANUAL_MODULE_RUN_TITLE
          vlmAt = null
          runStatus = manual.run.run_status
          taskId = manual.run.task_id
        }

        const anchorRunId = runId
        const anchorBatchId = batchId
        const anchorTitle = runTitle || MANUAL_MODULE_RUN_TITLE
        const anchorVlmAt = vlmAt
        const anchorStatus = runStatus || 'draft'
        const anchorTaskId = taskId || ''

        markDirty(anchorRunId, anchorBatchId)
        setRows(prev => {
          const live = resolveAnchor(prev, selectedKeys)
          const useRunId = live?.runId ?? anchorRunId
          const useBatchId = live?.batchId ?? anchorBatchId
          const newRows: FlatRow[] = txs.map(raw => ({
            key: `${useRunId}::${useBatchId}::${seqRef.current++}`,
            runId: useRunId,
            batchId: useBatchId,
            runTitle: live?.runTitle ?? anchorTitle,
            vlmAt: live?.vlmAt ?? anchorVlmAt,
            runStatus: live?.runStatus ?? anchorStatus,
            taskId: live?.taskId ?? anchorTaskId,
            fileId: null,
            filename: '',
            tx: {
              ...raw,
              manual_entry: true,
              upload_batch_id: useBatchId,
              source_file: raw.source_file ?? '',
            },
          }))
          if (live) {
            const idx = prev.findIndex(r => r.key === live.key)
            const next = [...prev]
            next.splice(idx < 0 ? prev.length : idx + 1, 0, ...newRows)
            return next
          }
          return [...prev, ...newRows]
        })
        return txs.length
      } catch (err) {
        setError(err instanceof Error ? err.message : 'CSV import failed.')
        return 0
      } finally {
        setPreparing(false)
      }
    },
    [rows, resolveAnchor, ensureManualBatch, markDirty],
  )

  const deleteRows = useCallback(
    (keys: Set<string>) => {
      if (keys.size === 0) return
      setRows(prev => {
        const removable = new Set(
          prev.filter(r => keys.has(r.key) && !isModuleTxnLocked(r.tx)).map(r => r.key),
        )
        if (removable.size === 0) return prev
        prev.forEach(r => {
          if (removable.has(r.key)) markDirty(r.runId, r.batchId)
        })
        return prev.filter(r => !removable.has(r.key))
      })
    },
    [markDirty],
  )

  const deployCodes = useCallback(
    async (keys?: Set<string>) => {
      setError(null)
      try {
        const target = keys && keys.size > 0 ? rows.filter(r => keys.has(r.key)) : rows
        const groups = new Map<string, FlatRow[]>()
        for (const r of target) {
          const k = `${r.runId}::${r.batchId}`
          const list = groups.get(k) ?? []
          list.push(r)
          groups.set(k, list)
        }
        const batches = []
        for (const [batchKey, list] of groups) {
          const meta = batchInfoRef.current.get(batchKey)
          const run = meta ? runsRef.current.get(meta.runId) : undefined
          if (!meta || !run || !run.task_id) continue
          const baseP = basePayloadsRef.current.get(batchKey) ?? {}
          batches.push({
            task_id: run.task_id,
            batch_id: meta.batchId,
            run_id: meta.runId,
            transactions: list.map(r => r.tx),
            base_payload: baseP,
            table_preset: frozenPresetForBatch(run, meta.batchId),
          })
        }
        if (batches.length === 0) return
        await startCoaDeploy({ mode: upper, companyId, batches })
        setDirty(prev => {
          const next = new Set(prev)
          for (const k of groups.keys()) next.delete(k)
          return next
        })
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Deploy Codes failed.')
      }
    },
    [rows, upper, companyId, startCoaDeploy],
  )

  const saveAll = useCallback(async () => {
    if (dirty.size === 0) return
    setSaving(true)
    setError(null)
    try {
      const byBatch = new Map<string, Tx[]>()
      for (const r of rows) {
        const k = `${r.runId}::${r.batchId}`
        if (!dirty.has(k)) continue
        const list = byBatch.get(k) ?? []
        // Stamp batch ownership so multi-batch slicing can keep manual Add Rows.
        const tx =
          r.tx?.manual_entry === true && !r.tx.upload_batch_id
            ? { ...r.tx, upload_batch_id: r.batchId }
            : r.tx
        list.push(tx)
        byBatch.set(k, list)
      }
      // Batches whose rows were all deleted still need an (empty) save.
      for (const k of dirty) if (!byBatch.has(k)) byBatch.set(k, [])

      for (const [k, txs] of byBatch) {
        const meta = batchInfoRef.current.get(k)
        const run = meta ? runsRef.current.get(meta.runId) : undefined
        if (!meta || !run) continue
        const baseP = basePayloadsRef.current.get(k) ?? {}
        const payload = isBank
          ? { ...baseP, bankTransactions: txs }
          : { ...baseP, arapTransactions: txs }
        basePayloadsRef.current.set(k, payload)
        await persistBatchTableSnapshot(
          run,
          meta.batchId,
          payload,
          frozenPresetForBatch(run, meta.batchId),
          companyId,
          { fromModule: true },
        )
      }
      setDirty(new Set())
      // Keep Reconciliation DB aligned with Books (add / edit / delete).
      void syncModulesToRecon(companyId).catch(err =>
        console.warn('[Books] recon sync after save failed:', err),
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed.')
    } finally {
      setSaving(false)
    }
  }, [dirty, rows, isBank, companyId])

  return {
    rows,
    loading,
    saving,
    preparing,
    deploying: isCoaDeploying(upper),
    error,
    dirty,
    reload,
    updateCell,
    updateAccountCode,
    updateDebitCredit,
    updateBankAccountType,
    addRow,
    importRows,
    deleteRows,
    deployCodes,
    saveAll,
  }
}
