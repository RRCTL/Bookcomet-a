import { useEffect, useMemo, useRef, useState } from 'react'
import type { UserCompany } from '../../../contexts/AuthContext'
import type { WorkflowFolder, WorkflowRunSummary } from '../workflowApi'
import { ConfirmDialog } from './ConfirmDialog'
import { FileStatusIcon } from './FileStatusIcon'
import { FolderRowMenu } from './FolderRowMenu'
import { RunRowMenu } from './RunRowMenu'
import { formatTimeAgo } from './timeAgo'
import { processingModeLabel } from '../../../components/ModeSelector'

const RUN_DRAG = 'application/x-workflow-run-id'
const FOLDER_DRAG = 'application/x-workflow-folder-id'

type Props = {
  runs: WorkflowRunSummary[]
  folders: WorkflowFolder[]
  activeRunId: string | null
  companies: UserCompany[]
  activeCompanyId?: string
  companyName?: string
  userLabel?: string
  showArchived: boolean
  mobile?: boolean
  onToggleArchived: () => void
  onNewRun: () => void
  onSelectRun: (id: string) => void
  onArchiveRun: (id: string) => void
  onRenameRun: (id: string, title: string) => Promise<void>
  onDeleteRun: (id: string, title: string) => void
  onMoveRunToFolder: (runId: string, folderId: string | null) => void
  onCreateFolder: (name: string) => void
  onRenameFolder: (id: string, name: string) => void
  onDeleteFolder: (id: string) => void
  onReorderFolders: (orderedIds: string[]) => void
  onSwitchCompany: (companyId: string) => void
  onCreateWorkspace: (name: string) => Promise<void>
  onDeleteWorkspace: (workspace: UserCompany) => Promise<void>
  onLogout: () => void
}

export function RunSidebar({
  runs,
  folders,
  activeRunId,
  companies,
  activeCompanyId,
  companyName,
  userLabel,
  showArchived,
  mobile = false,
  onToggleArchived,
  onNewRun,
  onSelectRun,
  onArchiveRun,
  onRenameRun,
  onDeleteRun,
  onMoveRunToFolder,
  onCreateFolder,
  onRenameFolder,
  onDeleteFolder,
  onReorderFolders,
  onSwitchCompany,
  onCreateWorkspace,
  onDeleteWorkspace,
  onLogout,
}: Props) {
  const [query, setQuery] = useState('')
  const [newFolderName, setNewFolderName] = useState('')
  const [newWorkspaceName, setNewWorkspaceName] = useState('')
  const [workspaceMenuOpen, setWorkspaceMenuOpen] = useState(false)
  const [addingWorkspace, setAddingWorkspace] = useState(false)
  const [workspaceBusy, setWorkspaceBusy] = useState(false)
  const [deleteWorkspace, setDeleteWorkspace] = useState<UserCompany | null>(null)
  const [deleteFolderId, setDeleteFolderId] = useState<string | null>(null)
  const [dragOverFolderId, setDragOverFolderId] = useState<string | null>(null)

  const sortedFolders = useMemo(
    () => [...folders].sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.name.localeCompare(b.name)),
    [folders],
  )

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return runs.filter(r => {
      if (!showArchived && r.archived_at) return false
      if (showArchived && !r.archived_at) return false
      if (!q) return true
      return (r.title || '').toLowerCase().includes(q) || r.processing_mode.toLowerCase().includes(q)
    })
  }, [runs, query, showArchived])

  const runsByFolder = useMemo(() => {
    const unfiled: WorkflowRunSummary[] = []
    const byFolder = new Map<string, WorkflowRunSummary[]>()
    for (const f of sortedFolders) byFolder.set(f.id, [])
    for (const r of filtered) {
      if (r.folder_id && byFolder.has(r.folder_id)) {
        byFolder.get(r.folder_id)!.push(r)
      } else {
        unfiled.push(r)
      }
    }
    return { unfiled, byFolder }
  }, [filtered, sortedFolders])

  const moveFolder = (fromId: string, toId: string) => {
    if (fromId === toId) return
    const ids = sortedFolders.map(f => f.id)
    const fromIdx = ids.indexOf(fromId)
    const toIdx = ids.indexOf(toId)
    if (fromIdx < 0 || toIdx < 0) return
    ids.splice(fromIdx, 1)
    ids.splice(toIdx, 0, fromId)
    onReorderFolders(ids)
  }

  const deleteFolder = sortedFolders.find(f => f.id === deleteFolderId)
  const activeWorkspace = companies.find(company => company.id === activeCompanyId)
  const workspaceLabel = activeWorkspace?.name ?? companyName ?? 'Select workspace'
  const addWorkspace = async () => {
    const name = newWorkspaceName.trim()
    if (!name || workspaceBusy) return
    setWorkspaceBusy(true)
    try {
      await onCreateWorkspace(name)
      setNewWorkspaceName('')
      setAddingWorkspace(false)
      setWorkspaceMenuOpen(false)
    } finally {
      setWorkspaceBusy(false)
    }
  }

  return (
    <>
      <aside
        className={`flex h-full shrink-0 flex-col border-r border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-gray-950 ${
          mobile ? 'w-full max-w-[var(--sidebar-width)]' : ''
        }`}
        style={mobile ? undefined : { width: 'var(--sidebar-width)' }}
      >
        <div className="flex items-center gap-2 border-b border-gray-200 p-3 dark:border-gray-800">
          <button type="button" className="btn-primary flex-1" onClick={onNewRun}>
            New run
          </button>
        </div>
        <div className="border-b border-gray-200 p-3 dark:border-gray-800">
          <input
            type="search"
            className="ow-input"
            placeholder="Search runs…"
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
          <button type="button" className="btn-ghost mt-2 w-full text-xs" onClick={onToggleArchived}>
            {showArchived ? 'Show active runs' : 'Show archived'}
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {sortedFolders.length > 0 ? (
            <div className="mb-3">
              <div className="px-2 py-1 text-xs font-semibold uppercase tracking-wide text-gray-500">Folders</div>
              {sortedFolders.map(folder => (
                <div
                  key={folder.id}
                  className={`mb-2 rounded-lg ${dragOverFolderId === folder.id ? 'ring-2 ring-gray-400 dark:ring-gray-500' : ''}`}
                  onDragOver={e => {
                    if (e.dataTransfer.types.includes(RUN_DRAG)) {
                      e.preventDefault()
                      setDragOverFolderId(folder.id)
                    }
                  }}
                  onDragLeave={() => setDragOverFolderId(null)}
                  onDrop={e => {
                    e.preventDefault()
                    setDragOverFolderId(null)
                    const runId = e.dataTransfer.getData(RUN_DRAG)
                    if (runId) onMoveRunToFolder(runId, folder.id)
                  }}
                >
                  <div
                    className="group flex cursor-grab items-center justify-between px-2 py-1 text-sm font-medium text-gray-700 dark:text-gray-300"
                    draggable
                    onDragStart={e => {
                      e.dataTransfer.setData(FOLDER_DRAG, folder.id)
                      e.dataTransfer.effectAllowed = 'move'
                    }}
                    onDragOver={e => {
                      if (e.dataTransfer.types.includes(FOLDER_DRAG)) e.preventDefault()
                    }}
                    onDrop={e => {
                      e.preventDefault()
                      e.stopPropagation()
                      const fromId = e.dataTransfer.getData(FOLDER_DRAG)
                      if (fromId) moveFolder(fromId, folder.id)
                    }}
                  >
                    <span className="truncate">{folder.name}</span>
                    <FolderRowMenu
                      folderName={folder.name}
                      onRename={name => onRenameFolder(folder.id, name)}
                      onDelete={() => setDeleteFolderId(folder.id)}
                    />
                  </div>
                  <ul className="space-y-0.5">
                    {(runsByFolder.byFolder.get(folder.id) ?? []).map(run => (
                      <RunRow
                        key={run.id}
                        run={run}
                        active={run.id === activeRunId}
                        folders={sortedFolders}
                        onSelect={() => onSelectRun(run.id)}
                        onArchive={() => onArchiveRun(run.id)}
                        onRename={title => onRenameRun(run.id, title)}
                        onDelete={() => onDeleteRun(run.id, run.title || 'Untitled')}
                        onMove={folderId => onMoveRunToFolder(run.id, folderId)}
                      />
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          ) : null}
          <div className="px-2 py-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
            {showArchived ? 'Archived' : 'Runs'}
          </div>
          <ul className="space-y-0.5">
            {runsByFolder.unfiled.map(run => (
              <RunRow
                key={run.id}
                run={run}
                active={run.id === activeRunId}
                folders={sortedFolders}
                onSelect={() => onSelectRun(run.id)}
                onArchive={() => onArchiveRun(run.id)}
                onRename={title => onRenameRun(run.id, title)}
                onDelete={() => onDeleteRun(run.id, run.title || 'Untitled')}
                onMove={folderId => onMoveRunToFolder(run.id, folderId)}
              />
            ))}
          </ul>
          {filtered.length === 0 ? (
            <p className="px-2 py-4 text-sm text-gray-500">No runs yet. Start with New run.</p>
          ) : null}
        </div>
        <div className="border-t border-gray-200 p-3 dark:border-gray-800">
          <div className="flex gap-2">
            <input
              type="text"
              className="ow-input flex-1"
              placeholder="New folder"
              value={newFolderName}
              onChange={e => setNewFolderName(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && newFolderName.trim()) {
                  onCreateFolder(newFolderName.trim())
                  setNewFolderName('')
                }
              }}
            />
            <button
              type="button"
              className="btn-secondary shrink-0"
              disabled={!newFolderName.trim()}
              onClick={() => {
                if (!newFolderName.trim()) return
                onCreateFolder(newFolderName.trim())
                setNewFolderName('')
              }}
            >
              Add
            </button>
          </div>
          <div className="relative mt-3 space-y-2 text-xs text-gray-500">
            <div className="mb-1 font-medium text-gray-700 dark:text-gray-300">Workspace</div>
            <button
              type="button"
              className="ow-input flex w-full items-center justify-between text-left"
              onClick={() => setWorkspaceMenuOpen(open => !open)}
              aria-expanded={workspaceMenuOpen}
            >
              <span className="truncate">{workspaceLabel}</span>
              <span className="ml-2 text-gray-400">v</span>
            </button>
            {workspaceMenuOpen ? (
              <div className="absolute bottom-full left-0 right-0 z-20 mb-2 rounded-xl border border-gray-200 bg-white p-2 shadow-lg dark:border-gray-700 dark:bg-gray-900">
                <div className="max-h-48 overflow-y-auto">
                  {companies.map(company => (
                    <div
                      key={company.id}
                      className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-800"
                    >
                      <button
                        type="button"
                        className="min-w-0 flex-1 text-left"
                        onClick={() => {
                          onSwitchCompany(company.id)
                          setWorkspaceMenuOpen(false)
                        }}
                      >
                        <span className="block truncate text-sm font-medium text-gray-800 dark:text-gray-100">
                          {company.name}
                        </span>
                        <span className="block truncate text-xs text-gray-500">
                          {company.id === activeCompanyId ? 'Active' : company.roleLabel}
                        </span>
                      </button>
                      <button
                        type="button"
                        className="btn-ghost px-2 py-1 text-xs text-red-600 hover:bg-red-50 dark:text-red-300 dark:hover:bg-red-950/40"
                        disabled={workspaceBusy}
                        onClick={() => setDeleteWorkspace(company)}
                      >
                        Delete
                      </button>
                    </div>
                  ))}
                </div>
                <div className="my-2 border-t border-gray-200 dark:border-gray-700" />
                {addingWorkspace ? (
                  <div className="flex gap-2">
                    <input
                      type="text"
                      className="ow-input flex-1"
                      autoFocus
                      placeholder="Workspace name"
                      value={newWorkspaceName}
                      onChange={e => setNewWorkspaceName(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === 'Enter') void addWorkspace()
                        if (e.key === 'Escape') setAddingWorkspace(false)
                      }}
                    />
                    <button
                      type="button"
                      className="btn-secondary shrink-0"
                      disabled={!newWorkspaceName.trim() || workspaceBusy}
                      onClick={() => void addWorkspace()}
                    >
                      {workspaceBusy ? 'Adding' : 'Add'}
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="btn-ghost w-full justify-start text-sm"
                    onClick={() => setAddingWorkspace(true)}
                  >
                    + Add workspace
                  </button>
                )}
              </div>
            ) : null}
            {userLabel ? <div className="truncate">{userLabel}</div> : null}
          </div>
          <button type="button" className="btn-ghost mt-2 w-full justify-start text-sm" onClick={onLogout}>
            Log out
          </button>
        </div>
      </aside>

      <ConfirmDialog
        open={deleteFolderId != null}
        title="Delete folder?"
        message={
          deleteFolder
            ? `Delete "${deleteFolder.name}"? Runs in this folder will be moved to unfiled.`
            : 'Delete this folder?'
        }
        confirmLabel="Delete"
        destructive
        onCancel={() => setDeleteFolderId(null)}
        onConfirm={() => {
          if (deleteFolderId) onDeleteFolder(deleteFolderId)
          setDeleteFolderId(null)
        }}
      />
      <ConfirmDialog
        open={deleteWorkspace != null}
        title="Delete workspace?"
        message={
          deleteWorkspace
            ? `Delete "${deleteWorkspace.name}"? This removes the workspace and its data for this company.`
            : 'Delete this workspace?'
        }
        confirmLabel="Delete"
        destructive
        onCancel={() => setDeleteWorkspace(null)}
        onConfirm={() => {
          if (!deleteWorkspace) return
          void onDeleteWorkspace(deleteWorkspace).finally(() => {
            setDeleteWorkspace(null)
            setWorkspaceMenuOpen(false)
          })
        }}
      />
    </>
  )
}

function RunRow({
  run,
  active,
  folders,
  onSelect,
  onArchive,
  onRename,
  onDelete,
  onMove,
}: {
  run: WorkflowRunSummary
  active: boolean
  folders: WorkflowFolder[]
  onSelect: () => void
  onArchive: () => void
  onRename: (title: string) => Promise<void>
  onDelete: () => void
  onMove: (folderId: string | null) => void
}) {
  const displayTitle = run.title || 'Untitled'
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(displayTitle)
  const [saving, setSaving] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const skipBlurUntilRef = useRef(0)

  useEffect(() => {
    if (!editing) setDraft(displayTitle)
  }, [displayTitle, editing])

  useEffect(() => {
    if (!editing) return
    skipBlurUntilRef.current = Date.now() + 200
    const frame = requestAnimationFrame(() => {
      inputRef.current?.focus()
      inputRef.current?.select()
    })
    return () => cancelAnimationFrame(frame)
  }, [editing])

  const cancelRename = () => {
    if (saving) return
    setDraft(displayTitle)
    setEditing(false)
  }

  const commitRename = () => {
    if (saving || Date.now() < skipBlurUntilRef.current) return
    const next = draft.trim()
    if (!next || next === displayTitle) {
      setEditing(false)
      return
    }
    setSaving(true)
    void onRename(next)
      .then(() => setEditing(false))
      .catch(() => {
        skipBlurUntilRef.current = Date.now() + 200
        requestAnimationFrame(() => {
          inputRef.current?.focus()
          inputRef.current?.select()
        })
      })
      .finally(() => setSaving(false))
  }

  const statusLine = (
    <span className="block truncate text-xs text-gray-500">
      {processingModeLabel(run.processing_mode)} · {run.run_status}
      {run.updated_at ? ` · ${formatTimeAgo(run.updated_at)}` : ''}
    </span>
  )
  const batches = run.batches ?? []
  const batchStatusIcons =
    batches.length > 0 ? (
      <span className="mt-1 flex flex-wrap items-center gap-1" aria-label="Batch statuses">
        {batches.map(batch => (
          <FileStatusIcon key={batch.upload_batch_id} status={batch.status} />
        ))}
      </span>
    ) : null

  return (
    <li className="group">
      <div
        className={`flex items-start gap-1 rounded-lg px-2 py-2 transition ${
          active ? 'bg-gray-200 dark:bg-gray-800' : 'hover:bg-gray-100 dark:hover:bg-gray-850'
        }`}
      >
        {editing ? (
          <div className="min-w-0 flex-1" onClick={e => e.stopPropagation()}>
            <input
              ref={inputRef}
              className="ow-input mb-0.5 w-full py-0.5 text-sm font-medium"
              value={draft}
              disabled={saving}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  commitRename()
                }
                if (e.key === 'Escape') cancelRename()
              }}
              onBlur={commitRename}
            />
            {statusLine}
            {batchStatusIcons}
          </div>
        ) : (
          <button
            type="button"
            className="min-w-0 flex-1 text-left text-sm"
            draggable
            onDragStart={e => {
              e.dataTransfer.setData(RUN_DRAG, run.id)
              e.dataTransfer.effectAllowed = 'move'
            }}
            onClick={onSelect}
          >
            <span className="block truncate font-medium">{displayTitle}</span>
            {statusLine}
            {batchStatusIcons}
          </button>
        )}
        <RunRowMenu
          folders={folders}
          onStartRename={() => setEditing(true)}
          onArchive={onArchive}
          onDelete={onDelete}
          onMove={onMove}
        />
      </div>
    </li>
  )
}

export { RUN_DRAG }
