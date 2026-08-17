import { getModule } from './moduleRegistry'

type Props = {
  openTabs: string[]
  activeId: string
  onSelect: (id: string) => void
  onClose: (id: string) => void
}

export function ModuleTabBar({ openTabs, activeId, onSelect, onClose }: Props) {
  return (
    <div className="erp-tabstrip" role="tablist">
      {openTabs.map(id => {
        const mod = getModule(id)
        if (!mod) return null
        const closable = mod.id !== 'processing'
        return (
          <div
            key={id}
            role="tab"
            aria-selected={id === activeId}
            className={`erp-tab${id === activeId ? ' active' : ''}`}
            onClick={() => onSelect(id)}
          >
            {mod.label}
            {closable && (
              <button
                type="button"
                className="erp-x"
                aria-label={`Close ${mod.label}`}
                onClick={e => {
                  e.stopPropagation()
                  onClose(id)
                }}
              >
                &times;
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
}
