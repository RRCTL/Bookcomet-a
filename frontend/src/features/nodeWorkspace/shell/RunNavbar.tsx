import { RunOverflowMenu } from './RunOverflowMenu'

type Props = {
  title: string
  mode?: string
  runStatus?: string
  sidebarOpen: boolean
  controlsOpen: boolean
  themeLabel: string
  onToggleSidebar: () => void
  onToggleControls: () => void
  onOpenManager: () => void
  onOpenSkills: () => void
  onThemeToggle: () => void
  onOpenSearch: () => void
  onExportAudit?: () => void
  onDeleteRun?: () => void
}

export function RunNavbar({
  title,
  mode,
  runStatus,
  sidebarOpen,
  controlsOpen,
  themeLabel,
  onToggleSidebar,
  onToggleControls,
  onOpenManager,
  onOpenSkills,
  onThemeToggle,
  onOpenSearch,
  onExportAudit,
  onDeleteRun,
}: Props) {
  const menuItems: Array<{
    id: string
    label: string
    onClick: () => void
    destructive?: boolean
  }> = [
    {
      id: 'workflow',
      label: controlsOpen ? 'Hide workflow pane' : 'Show workflow pane',
      onClick: onToggleControls,
    },
    { id: 'templates', label: 'Templates', onClick: onOpenManager },
    { id: 'skills', label: 'Workflow skills', onClick: onOpenSkills },
    { id: 'theme', label: `Theme: ${themeLabel}`, onClick: onThemeToggle },
    { id: 'search', label: 'Search (Ctrl+K)', onClick: onOpenSearch },
  ]
  if (onExportAudit) {
    menuItems.push({ id: 'audit-json', label: 'Export audit JSON', onClick: onExportAudit })
  }
  if (onDeleteRun) {
    menuItems.push({ id: 'delete', label: 'Delete run', onClick: onDeleteRun, destructive: true })
  }

  return (
    <header className="sticky top-0 z-20 flex shrink-0 items-center gap-2 border-b border-gray-200 bg-white/90 px-3 py-2 backdrop-blur dark:border-gray-800 dark:bg-gray-900/90 md:px-4">
      <button
        type="button"
        className="btn-ghost shrink-0 px-2"
        aria-label={sidebarOpen ? 'Close sidebar' : 'Open sidebar'}
        onClick={onToggleSidebar}
      >
        {sidebarOpen ? '||' : '='}
      </button>
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-base font-semibold text-gray-900 dark:text-gray-100">{title}</h1>
        <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
          {mode ? (
            <span className="rounded-md bg-gray-100 px-2 py-0.5 font-medium dark:bg-gray-850">{mode}</span>
          ) : null}
          {runStatus ? <span>{runStatus}</span> : null}
        </div>
      </div>
      <button type="button" className="btn-ghost hidden shrink-0 text-xs sm:inline-flex" onClick={onOpenSearch}>
        Search
      </button>
      <RunOverflowMenu items={menuItems} />
    </header>
  )
}
