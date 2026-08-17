import type { DrawerTab } from './IconRail'
import type { WorkflowRunSummary, WorkflowTemplate } from './workflowApi'

type Props = {
  tab: DrawerTab
  runs: WorkflowRunSummary[]
  templates: WorkflowTemplate[]
  activeRunId: string | null
  onSelectRun: (id: string) => void
}

export function LeftDrawer({ tab, runs, templates, activeRunId, onSelectRun }: Props) {
  if (!tab) return null

  if (tab === 'queue') {
    const active = runs.filter(r => ['executing', 'coa_running'].includes(r.run_status))
    return (
      <aside className="left-drawer">
        <div className="left-drawer__title">Queue</div>
        {active.length === 0 ? (
          <p className="left-drawer__empty">No runs in progress</p>
        ) : (
          <ul className="left-drawer__list">
            {active.map(r => (
              <li key={r.id}>
                <button
                  type="button"
                  className={`left-drawer__item${r.id === activeRunId ? ' left-drawer__item--active' : ''}`}
                  onClick={() => onSelectRun(r.id)}
                >
                  <span>{r.title || 'Untitled'}</span>
                  <span className="left-drawer__status">{r.run_status}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>
    )
  }

  return (
    <aside className="left-drawer">
      <div className="left-drawer__title">Templates</div>
      {templates.length === 0 ? (
        <p className="left-drawer__empty">No templates saved</p>
      ) : (
        <ul className="left-drawer__list">
          {templates.map(t => (
            <li key={t.id} className="left-drawer__item-static">
              {t.name} <span className="left-drawer__status">({t.processing_mode})</span>
            </li>
          ))}
        </ul>
      )}
    </aside>
  )
}
