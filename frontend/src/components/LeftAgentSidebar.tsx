import { useState, useRef, useEffect } from 'react'
import './LeftAgentSidebar.css'
import type { ProcessingMode } from './ModeSelector'
import type { UserCompany } from '../contexts/AuthContext'

export interface SidebarTask {
  id: string
  title: string
  createdAt: string
  status: 'idle' | 'queued' | 'processing' | 'completed' | 'failed'
  processingMode: string
  fileCount: number
  hasSpreadsheet: boolean
  hasFinancialReport: boolean
  /** Idle row: show in folder when composer is on this task or user already sent chat. */
  idleVisible?: boolean
}

interface FolderDef {
  mode: ProcessingMode
  label: string
  badgeLabel: string
  color: string
}

const FOLDERS: FolderDef[] = [
  { mode: 'AR',        label: 'AR',                   badgeLabel: 'AR',   color: '#22c55e' },
  { mode: 'AP',        label: 'AP',                   badgeLabel: 'AP',   color: '#eab308' },
  { mode: 'BANK',      label: 'BANK',                 badgeLabel: 'BANK', color: '#9333ea' },
  { mode: 'OTHER', label: 'Other',                badgeLabel: 'Other', color: '#10b981' },
]

interface Props {
  tasks: SidebarTask[]
  activeTaskId: string | null
  activeMode: ProcessingMode
  isDrawer?: boolean
  onClose?: () => void
  onSelectTask: (taskId: string) => void
  onRenameTask: (taskId: string, newTitle: string) => void
  onDeleteTask: (taskId: string) => void
  onUpload: () => void
  onStartReport: () => void
  onModeChange: (mode: ProcessingMode) => void
  deployingTaskIds: Set<string>
  aiThinkingTaskIds: Set<string>
  /** Multi-tenant company scope — always shown at top of sidebar */
  companies: UserCompany[]
  activeCompany: UserCompany | null
  onSwitchCompany: (companyId: string) => void
  collapsed?: boolean
  onToggleCollapse?: () => void
  onCreateWorkspace?: (name: string) => Promise<void>
}

export function LeftAgentSidebar({
  tasks,
  activeTaskId,
  activeMode,
  isDrawer,
  onClose,
  onSelectTask,
  onRenameTask,
  onDeleteTask,
  onUpload,
  onStartReport: _onStartReport,
  onModeChange,
  deployingTaskIds,
  aiThinkingTaskIds,
  companies,
  activeCompany,
  onSwitchCompany,
  collapsed = false,
  onToggleCollapse: _onToggleCollapse,
  onCreateWorkspace,
}: Props) {
  const [openFolders, setOpenFolders] = useState<Set<string>>(
    () => new Set(['AR', 'AP', 'BANK'])
  )
  const [editingId, setEditingId] = useState<string | null>(null)
  const [menuId, setMenuId] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [companyMenuOpen, setCompanyMenuOpen] = useState(false)
  const [createModalOpen, setCreateModalOpen] = useState(false)
  const [newWorkspaceName, setNewWorkspaceName] = useState('')
  const [createSubmitting, setCreateSubmitting] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const companyMenuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (companyMenuRef.current && !companyMenuRef.current.contains(e.target as Node)) {
        setCompanyMenuOpen(false)
      }
    }
    if (companyMenuOpen) document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [companyMenuOpen])

  const toggleFolder = (mode: string) => {
    setOpenFolders(prev => {
      const next = new Set(prev)
      if (next.has(mode)) next.delete(mode)
      else next.add(mode)
      return next
    })
  }

  const hasRealContent = (t: SidebarTask) =>
    t.fileCount > 0 ||
    t.hasSpreadsheet ||
    t.status !== 'idle' ||
    Boolean(t.idleVisible)

  const getTasksForFolder = (folder: FolderDef): SidebarTask[] => {
    const q = searchQuery.trim().toLowerCase()
    return [...tasks]
      .filter(t => t.processingMode === folder.mode)
      .filter(hasRealContent)
      .filter(t => !q || t.title.toLowerCase().includes(q))
      .reverse()
  }

  const getStatusClass = (task: SidebarTask): string => {
    if (deployingTaskIds.has(task.id) || aiThinkingTaskIds.has(task.id)) return 'task-spinner'
    switch (task.status) {
      case 'processing': return 'task-spinner'
      case 'queued':     return 'task-queued-icon'
      case 'failed':     return 'task-failed-icon'
      case 'idle':       return 'task-idle-icon'
      default:           return 'task-done-icon'
    }
  }

  const handleFolderClick = (folder: FolderDef) => {
    toggleFolder(folder.mode)
    onModeChange(folder.mode)
  }

  const multiCompany = companies.length > 1
  const companyMenuInteractive = multiCompany || Boolean(onCreateWorkspace)

  const resetCreateModal = () => {
    setCreateModalOpen(false)
    setNewWorkspaceName('')
    setCreateError(null)
    setCreateSubmitting(false)
  }

  const handleCreateWorkspaceSubmit = async () => {
    if (!onCreateWorkspace) return
    const trimmed = newWorkspaceName.trim()
    if (!trimmed) {
      setCreateError('Enter a workspace name')
      return
    }
    setCreateSubmitting(true)
    setCreateError(null)
    try {
      await onCreateWorkspace(trimmed)
      resetCreateModal()
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : String(e))
      setCreateSubmitting(false)
    }
  }

  return (
    <aside className={`agent-sidebar${collapsed ? ' agent-sidebar--collapsed' : ''}`}>
      {/* Company scope — always visible at top */}
      <div
        className={`sidebar-company-bar${companyMenuInteractive ? ' sidebar-company-bar--interactive' : ''}${companyMenuOpen ? ' sidebar-company-bar--open' : ''}`}
        ref={companyMenuRef}
        onClick={companyMenuInteractive ? () => setCompanyMenuOpen(o => !o) : undefined}
        role={companyMenuInteractive ? 'button' : undefined}
        tabIndex={companyMenuInteractive ? 0 : undefined}
        onKeyDown={companyMenuInteractive ? (e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setCompanyMenuOpen(o => !o)
          }
        } : undefined}
      >
        <div className="sidebar-company-bar-inner">
          <div className="sidebar-company-text">
            <div className="sidebar-company-name" title={activeCompany?.name ?? ''}>
              {activeCompany?.name ?? '…'}
            </div>
            {activeCompany && (
              <div className="sidebar-company-role">{activeCompany.roleLabel}</div>
            )}
          </div>
          {companyMenuInteractive && (
            <svg className="sidebar-company-chevron" width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden>
              <path d="M2 3.5L5 6.5L8 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          )}
        </div>

        {companyMenuInteractive && companyMenuOpen && (
          <div className="sidebar-company-menu" onClick={e => e.stopPropagation()}>
            {multiCompany &&
              companies.map(c => (
                <div
                  key={c.id}
                  className={`sidebar-company-item${c.id === activeCompany?.id ? ' active' : ''}`}
                  onClick={() => {
                    onSwitchCompany(c.id)
                    setCompanyMenuOpen(false)
                  }}
                >
                  <span className="sidebar-company-item-name">{c.name}</span>
                  <span className="sidebar-company-item-role">{c.roleLabel}</span>
                  {c.id === activeCompany?.id && (
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" className="sidebar-company-check">
                      <path d="M13.5 4L6 11.5L2.5 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                  )}
                </div>
              ))}
            {multiCompany && onCreateWorkspace && <div className="sidebar-company-menu-divider" />}
            {onCreateWorkspace && (
              <button
                type="button"
                className="sidebar-company-create"
                onClick={(e) => {
                  e.stopPropagation()
                  setCompanyMenuOpen(false)
                  setCreateModalOpen(true)
                  setNewWorkspaceName('')
                  setCreateError(null)
                }}
              >
                <span className="sidebar-company-create-icon" aria-hidden>+</span>
                Create workspace
              </button>
            )}
          </div>
        )}
      </div>

      {/* Header — close button (drawer) + upload + search */}
      <div className="agent-sidebar-header">
        {isDrawer && (
          <div className="sidebar-drawer-toprow">
            <span className="sidebar-drawer-title">Tasks</span>
            <button className="sidebar-upload-btn" onClick={onUpload}>
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M14 10V12.5C14 13.163 13.7366 13.7989 13.2678 14.2678C12.7989 14.7366 12.163 15 11.5 15H4.5C3.83696 15 3.20107 14.7366 2.73223 14.2678C2.26339 13.7989 2 13.163 2 12.5V10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/><path d="M11 5L8 2L5 5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/><path d="M8 2V10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
              Upload
            </button>
            <button className="sidebar-close-btn" onClick={onClose} title="Close">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 4L12 12M12 4L4 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>
            </button>
          </div>
        )}
        <div className="sidebar-search">
          <svg className="sidebar-search-icon" width="13" height="13" viewBox="0 0 16 16" fill="none">
            <circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.5"/>
            <path d="M11 11L14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          <input
            className="sidebar-search-input"
            type="text"
            placeholder="Search tasks…"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
          />
          {searchQuery && (
            <button className="sidebar-search-clear" onClick={() => setSearchQuery('')} title="Clear">×</button>
          )}
        </div>
      </div>

      {/* Agent Folder List */}
      <div className="agent-folders">
        {FOLDERS.map(folder => {
          const folderTasks = getTasksForFolder(folder)
          const isOpen = openFolders.has(folder.mode)
          const isActiveFolder = activeMode === folder.mode

          return (
            <div key={folder.mode} className="agent-folder">
              {/* Folder Header */}
              <button
                className={`agent-folder-header ${isActiveFolder ? 'active' : ''}`}
                onClick={() => handleFolderClick(folder)}
              >
                <svg
                  className={`folder-chevron ${isOpen ? 'open' : ''}`}
                  width="12" height="12" viewBox="0 0 12 12" fill="none"
                >
                  <path d="M4 2L8 6L4 10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
                <span
                  className="folder-color-dot"
                  style={{ background: folder.color }}
                />
                <span className="folder-label">{folder.label}</span>
                {folderTasks.length > 0 && (
                  <span className="folder-task-count">{folderTasks.length}</span>
                )}
              </button>

              {/* Task Records inside folder */}
              {isOpen && (
                <div className="agent-folder-records">
                  {folderTasks.length === 0 ? (
                    <div className="folder-empty">No records</div>
                  ) : (
                    folderTasks.map(task => (
                      <div
                        key={task.id}
                        className="record-item-wrapper"
                        style={{ position: 'relative', zIndex: menuId === task.id ? 9999 : 1 }}
                      >
                        {editingId === task.id ? (
                          <input
                            className="record-title-edit"
                            defaultValue={task.title}
                            autoFocus
                            onBlur={(e) => { onRenameTask(task.id, e.target.value); setEditingId(null) }}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') { onRenameTask(task.id, e.currentTarget.value); setEditingId(null) }
                              else if (e.key === 'Escape') setEditingId(null)
                            }}
                          />
                        ) : (
                          <>
                            <button
                              className={`record-item ${activeTaskId === task.id ? 'active' : ''}`}
                              onClick={() => onSelectTask(task.id)}
                            >
                              <span className={getStatusClass(task)} />
                              <div className="record-info">
                                <div
                                  className="record-title"
                                  onDoubleClick={(e) => { e.stopPropagation(); setEditingId(task.id) }}
                                >
                                  {task.title}
                                </div>
                                <div className="record-meta">
                                  {task.fileCount > 0 ? `${task.fileCount} files · ` : ''}
                                  {new Date(task.createdAt).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                                </div>
                              </div>
                            </button>

                            <div className="record-menu">
                              <button
                                className="menu-button"
                                onClick={(e) => { e.stopPropagation(); setMenuId(menuId === task.id ? null : task.id) }}
                              >
                                ⋮
                              </button>
                              {menuId === task.id && (
                                <div className="menu-dropdown">
                                  <button onClick={() => { setEditingId(task.id); setMenuId(null) }}>Rename</button>
                                  <button className="delete-btn" onClick={() => { onDeleteTask(task.id); setMenuId(null) }}>Delete</button>
                                </div>
                              )}
                            </div>
                          </>
                        )}
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {createModalOpen && onCreateWorkspace && (
        <div
          className="sidebar-create-workspace-backdrop"
          onClick={() => { if (!createSubmitting) resetCreateModal() }}
          role="presentation"
        >
          <div
            className="sidebar-create-workspace-modal"
            onClick={e => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby="sidebar-create-workspace-heading"
          >
            <h2 id="sidebar-create-workspace-heading" className="sidebar-create-workspace-title">
              Create workspace
            </h2>
            <p className="sidebar-create-workspace-hint">
              A new company workspace will be created and selected when you continue.
            </p>
            <label className="sidebar-create-workspace-label">
              Name
              <input
                className="sidebar-create-workspace-input"
                value={newWorkspaceName}
                onChange={e => setNewWorkspaceName(e.target.value)}
                disabled={createSubmitting}
                autoFocus
              />
            </label>
            {createError && <p className="sidebar-create-workspace-error">{createError}</p>}
            <div className="sidebar-create-workspace-actions">
              <button
                type="button"
                className="sidebar-create-workspace-btn sidebar-create-workspace-btn--ghost"
                onClick={() => { if (!createSubmitting) resetCreateModal() }}
                disabled={createSubmitting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="sidebar-create-workspace-btn sidebar-create-workspace-btn--primary"
                onClick={() => { void handleCreateWorkspaceSubmit() }}
                disabled={createSubmitting}
              >
                Create
              </button>
            </div>
          </div>
        </div>
      )}
    </aside>
  )
}
