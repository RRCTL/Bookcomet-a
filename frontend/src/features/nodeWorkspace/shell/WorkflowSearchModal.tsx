import { useEffect, useMemo, useRef, useState } from 'react'
import type { WorkflowFolder, WorkflowRunSummary } from '../workflowApi'
import { processingModeLabel } from '../../../components/ModeSelector'

type Props = {
  open: boolean
  onClose: () => void
  activeRuns: WorkflowRunSummary[]
  archivedRuns: WorkflowRunSummary[]
  folders: WorkflowFolder[]
  onSelectRun: (id: string) => void
  onNewRun: () => void
  onOpenTemplates: () => void
  onToggleWorkflow: () => void
}

export function WorkflowSearchModal({
  open,
  onClose,
  activeRuns,
  archivedRuns,
  folders,
  onSelectRun,
  onNewRun,
  onOpenTemplates,
  onToggleWorkflow,
}: Props) {
  const [query, setQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (open) {
      setQuery('')
      setTimeout(() => inputRef.current?.focus(), 0)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const q = query.trim().toLowerCase()

  const filteredRuns = useMemo(() => {
    const all = [...activeRuns, ...archivedRuns]
    if (!q) return all.slice(0, 20)
    return all
      .filter(
        r =>
          (r.title || '').toLowerCase().includes(q) ||
          r.processing_mode.toLowerCase().includes(q) ||
          r.run_status.toLowerCase().includes(q),
      )
      .slice(0, 20)
  }, [activeRuns, archivedRuns, q])

  const filteredFolders = useMemo(() => {
    if (!q) return folders.slice(0, 10)
    return folders.filter(f => f.name.toLowerCase().includes(q)).slice(0, 10)
  }, [folders, q])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-[70] flex items-start justify-center bg-black/40 p-4 pt-[15vh]" onClick={onClose}>
      <div
        className="ow-card w-full max-w-lg overflow-hidden"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Search workflows"
      >
        <div className="border-b border-gray-100 p-3 dark:border-gray-800">
          <input
            ref={inputRef}
            type="search"
            className="ow-input border-0 bg-transparent shadow-none focus:border-0"
            placeholder="Search runs, folders, actions…"
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
        </div>
        <div className="max-h-[50vh] overflow-y-auto p-2">
          <Section title="Actions">
            <SearchRow label="New run" hint="Create workflow" onClick={() => { onClose(); onNewRun() }} />
            <SearchRow label="Templates" hint="Manage graphs" onClick={() => { onClose(); onOpenTemplates() }} />
            <SearchRow label="Toggle workflow pane" hint="Graph, log, files" onClick={() => { onClose(); onToggleWorkflow() }} />
          </Section>
          {filteredFolders.length > 0 ? (
            <Section title="Folders">
              {filteredFolders.map(f => (
                <SearchRow key={f.id} label={f.name} hint="Folder" onClick={() => { /* scroll only */ onClose() }} />
              ))}
            </Section>
          ) : null}
          <Section title="Runs">
            {filteredRuns.length === 0 ? (
              <p className="px-3 py-2 text-sm text-gray-500">No matching runs</p>
            ) : (
              filteredRuns.map(r => (
                <SearchRow
                  key={r.id}
                  label={r.title || 'Untitled'}
                  hint={`${processingModeLabel(r.processing_mode)} · ${r.run_status}${r.archived_at ? ' · archived' : ''}`}
                  onClick={() => {
                    onClose()
                    onSelectRun(r.id)
                  }}
                />
              ))
            )}
          </Section>
        </div>
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-2">
      <div className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-gray-400">{title}</div>
      {children}
    </div>
  )
}

function SearchRow({ label, hint, onClick }: { label: string; hint: string; onClick: () => void }) {
  return (
    <button
      type="button"
      className="flex w-full flex-col rounded-lg px-3 py-2 text-left hover:bg-gray-50 dark:hover:bg-gray-850"
      onClick={onClick}
    >
      <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{label}</span>
      <span className="text-xs text-gray-500">{hint}</span>
    </button>
  )
}
