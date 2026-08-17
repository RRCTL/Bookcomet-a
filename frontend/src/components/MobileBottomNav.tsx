import './MobileBottomNav.css'
import type { ReactElement } from 'react'

export type BottomTab = 'tasks' | 'chat' | 'workspace'

interface Props {
  activeTab: BottomTab
  onTabChange: (tab: BottomTab) => void
  taskCount?: number
}

const TABS: { id: BottomTab; label: string; icon: ReactElement }[] = [
  {
    id: 'tasks',
    label: 'Tasks',
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
        <rect x="3" y="3" width="14" height="14" rx="2" stroke="currentColor" strokeWidth="1.5"/>
        <path d="M7 7H13M7 10H13M7 13H10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      </svg>
    ),
  },
  {
    id: 'chat',
    label: 'Chat',
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
        <path d="M4 4H16V13H8L4 16V4Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
      </svg>
    ),
  },
  {
    id: 'workspace',
    label: 'Workspace',
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
        <rect x="3" y="3" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.5"/>
        <rect x="11" y="3" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.5"/>
        <rect x="3" y="11" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.5"/>
        <rect x="11" y="11" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.5"/>
      </svg>
    ),
  },
]

export function MobileBottomNav({ activeTab, onTabChange, taskCount }: Props) {
  return (
    <nav className="mobile-bottom-nav">
      {TABS.map(tab => (
        <button
          key={tab.id}
          className={`bottom-nav-tab ${activeTab === tab.id ? 'bottom-nav-tab--active' : ''}`}
          onClick={() => onTabChange(tab.id)}
        >
          <span className="bottom-nav-icon">
            {tab.icon}
            {tab.id === 'tasks' && taskCount != null && taskCount > 0 && (
              <span className="bottom-nav-badge">{taskCount}</span>
            )}
          </span>
          <span className="bottom-nav-label">{tab.label}</span>
        </button>
      ))}
    </nav>
  )
}
