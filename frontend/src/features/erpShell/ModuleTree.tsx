import { useState } from 'react'
import { enabledModules } from './moduleRegistry'

type Props = {
  activeId: string
  open: boolean
  onSelect: (id: string) => void
}

export function ModuleTree({ activeId, open, onSelect }: Props) {
  const [filter, setFilter] = useState('')
  const modules = enabledModules().filter(m =>
    m.label.toLowerCase().includes(filter.trim().toLowerCase()),
  )

  return (
    <nav className={`erp-tree${open ? ' open' : ''}`} aria-label="Modules">
      <div className="erp-filter">
        <input
          type="text"
          placeholder="Filter modules..."
          value={filter}
          onChange={e => setFilter(e.target.value)}
        />
      </div>
      <ul>
        {modules.map(m => (
          <li
            key={m.id}
            className={m.id === activeId ? 'active' : ''}
            onClick={() => onSelect(m.id)}
          >
            {m.label}
          </li>
        ))}
      </ul>
    </nav>
  )
}
