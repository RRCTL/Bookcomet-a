import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { useAuth } from '../../contexts/AuthContext'
import { api, BG_JOB_STORAGE_PREFIX, type BackgroundJobRecord } from '../../services/api'

export const ERP_COA_DEPLOY_COMPLETE = 'bookcomet:erp-coa-deploy-complete'

type CoaDeployStorageMeta = {
  kind: 'coa_deploy'
  mode: string
  companyId: string
  batchKeys: string[]
}

export type CoaDeployBatchInput = {
  task_id: string
  batch_id: string
  run_id: string
  transactions: Record<string, unknown>[]
  base_payload?: Record<string, unknown>
  table_preset?: string
}

type ErpBackgroundJobsContextValue = {
  activeJobs: BackgroundJobRecord[]
  isCoaDeploying: (mode: string) => boolean
  startCoaDeploy: (args: {
    mode: string
    companyId: string
    batches: CoaDeployBatchInput[]
  }) => Promise<string>
}

const ErpBackgroundJobsContext = createContext<ErpBackgroundJobsContextValue | null>(null)

export function useErpBackgroundJobs(): ErpBackgroundJobsContextValue {
  const ctx = useContext(ErpBackgroundJobsContext)
  if (!ctx) {
    throw new Error('useErpBackgroundJobs must be used within ErpBackgroundJobsProvider')
  }
  return ctx
}

export function ErpBackgroundJobsProvider({ children }: { children: ReactNode }) {
  const { activeCompany } = useAuth()
  const companyId = activeCompany?.id ?? 'default'
  const [activeJobs, setActiveJobs] = useState<BackgroundJobRecord[]>([])
  const [deployingModes, setDeployingModes] = useState<Set<string>>(() => new Set())
  const pollingRef = useRef(new Set<string>())

  const markDeploying = useCallback((mode: string) => {
    const upper = mode.toUpperCase()
    setDeployingModes(prev => {
      if (prev.has(upper)) return prev
      const next = new Set(prev)
      next.add(upper)
      return next
    })
  }, [])

  const unmarkDeploying = useCallback((mode: string) => {
    const upper = mode.toUpperCase()
    setDeployingModes(prev => {
      if (!prev.has(upper)) return prev
      const next = new Set(prev)
      next.delete(upper)
      return next
    })
  }, [])

  const refreshActiveJobs = useCallback(async () => {
    const jobs = await api.listActiveBackgroundJobs()
    setActiveJobs(jobs)
    return jobs
  }, [])

  const finishCoaDeploy = useCallback(
    (
      jobId: string,
      meta: CoaDeployStorageMeta,
      failed?: string,
      result?: Record<string, unknown> | null,
    ) => {
      localStorage.removeItem(BG_JOB_STORAGE_PREFIX + jobId)
      pollingRef.current.delete(jobId)
      unmarkDeploying(meta.mode)
      void refreshActiveJobs()
      if (!failed && result) {
        const blocked = Number(result.blocked_posted_count || 0)
        if (blocked > 0) {
          window.alert(
            `${blocked} row(s) are posted to the GL and were skipped during Deploy Codes.\n\n` +
              'Unpost the journal in RECON (back to draft), then Deploy Codes again for those rows.',
          )
        }
      }
      window.dispatchEvent(
        new CustomEvent(ERP_COA_DEPLOY_COMPLETE, {
          detail: { ...meta, failed, result },
        }),
      )
    },
    [refreshActiveJobs, unmarkDeploying],
  )

  const pollJob = useCallback(
    async (jobId: string, meta: CoaDeployStorageMeta) => {
      if (pollingRef.current.has(jobId)) return
      pollingRef.current.add(jobId)
      try {
        const result = await api.waitForBackgroundJob(jobId, {
          companyId: meta.companyId,
          onProgress: () => {
            void refreshActiveJobs()
          },
        })
        finishCoaDeploy(jobId, meta, undefined, result as Record<string, unknown>)
      } catch (e) {
        const msg = e instanceof Error ? e.message : 'Deploy failed'
        finishCoaDeploy(jobId, meta, msg)
      }
    },
    [finishCoaDeploy, refreshActiveJobs],
  )

  const startCoaDeploy = useCallback(
    async (args: { mode: string; companyId: string; batches: CoaDeployBatchInput[] }) => {
      const mode = args.mode.toUpperCase()
      markDeploying(mode)
      const { job_id } = await api.createCoaDeployBackgroundJob(
        { mode, batches: args.batches },
        args.companyId,
      )
      const meta: CoaDeployStorageMeta = {
        kind: 'coa_deploy',
        mode,
        companyId: args.companyId,
        batchKeys: args.batches.map(b => `${b.run_id}::${b.batch_id}`),
      }
      localStorage.setItem(BG_JOB_STORAGE_PREFIX + job_id, JSON.stringify(meta))
      void pollJob(job_id, meta)
      void refreshActiveJobs()
      return job_id
    },
    [markDeploying, pollJob, refreshActiveJobs],
  )

  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      if (cancelled) return
      await refreshActiveJobs()
    }
    void tick()
    const id = window.setInterval(tick, 4000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [companyId, refreshActiveJobs])

  useEffect(() => {
    let cancelled = false
    const keys: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (k?.startsWith(BG_JOB_STORAGE_PREFIX)) keys.push(k)
    }
    for (const key of keys) {
      const jobId = key.slice(BG_JOB_STORAGE_PREFIX.length)
      const raw = localStorage.getItem(key)
      if (!raw) continue
      try {
        const meta = JSON.parse(raw) as CoaDeployStorageMeta
        if (meta.kind !== 'coa_deploy' || meta.companyId !== companyId) continue
        markDeploying(meta.mode)
        void api
          .getBackgroundJob(jobId, companyId)
          .then(st => {
            if (cancelled) return
            if (st.status === 'completed') {
              finishCoaDeploy(jobId, meta, undefined, st.result_json ?? null)
              return
            }
            if (st.status === 'failed' || st.status === 'cancelled') {
              finishCoaDeploy(jobId, meta, st.error_text || 'Deploy failed')
              return
            }
            void pollJob(jobId, meta)
          })
          .catch(() => {})
      } catch {
        /* ignore stale bg metadata */
      }
    }
    return () => {
      cancelled = true
    }
  }, [companyId, finishCoaDeploy, markDeploying, pollJob])

  const value = useMemo(
    (): ErpBackgroundJobsContextValue => ({
      activeJobs,
      isCoaDeploying: (mode: string) => deployingModes.has(mode.toUpperCase()),
      startCoaDeploy,
    }),
    [activeJobs, deployingModes, startCoaDeploy],
  )

  return <ErpBackgroundJobsContext.Provider value={value}>{children}</ErpBackgroundJobsContext.Provider>
}
