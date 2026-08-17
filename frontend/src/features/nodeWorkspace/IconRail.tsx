import type { ReactNode } from 'react'

export type DrawerTab = 'queue' | 'templates' | null

type Props = {
  activeDrawer: DrawerTab
  queueCount: number
  onToggle: (tab: DrawerTab) => void
}

function RailIcon({ children }: { label: string; children: ReactNode }) {
  return <span className="icon-rail__glyph" aria-hidden>{children}</span>
}

export function IconRail({ activeDrawer, queueCount, onToggle }: Props) {
  const toggle = (tab: 'queue' | 'templates') => {
    onToggle(activeDrawer === tab ? null : tab)
  }

  return (
    <nav className="icon-rail" aria-label="Workspace tools">
      <button
        type="button"
        className={`icon-rail__btn${activeDrawer === 'queue' ? ' icon-rail__btn--active' : ''}`}
        title="Queue"
        onClick={() => toggle('queue')}
      >
        <RailIcon label="Queue">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M4 6h16M4 12h16M4 18h10" />
          </svg>
        </RailIcon>
        {queueCount > 0 ? <span className="icon-rail__badge">{queueCount}</span> : null}
      </button>
      <button
        type="button"
        className={`icon-rail__btn${activeDrawer === 'templates' ? ' icon-rail__btn--active' : ''}`}
        title="Templates"
        onClick={() => toggle('templates')}
      >
        <RailIcon label="Templates">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="3" width="7" height="7" />
            <rect x="14" y="3" width="7" height="7" />
            <rect x="3" y="14" width="7" height="7" />
            <rect x="14" y="14" width="7" height="7" />
          </svg>
        </RailIcon>
      </button>
    </nav>
  )
}
