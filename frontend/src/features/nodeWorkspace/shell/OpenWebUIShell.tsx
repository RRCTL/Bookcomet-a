import type { CSSProperties, PointerEvent as ReactPointerEvent, ReactNode } from 'react'

type Props = {
  sidebar: ReactNode
  navbar: ReactNode
  timeline: ReactNode
  composer: ReactNode
  controls?: ReactNode
  controlsOpen: boolean
  sidebarOpen: boolean
  isMobile: boolean
  sidebarWidth: number
  controlsWidth: number
  onSidebarWidthChange: (width: number) => void
  onControlsWidthChange: (width: number) => void
  mobileControlsSheet?: ReactNode
  onSidebarBackdropClick?: () => void
}

function clampWidth(width: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, Math.round(width)))
}

function maxControlsWidth(): number {
  return Math.max(300, Math.floor(window.innerWidth * 0.5))
}

export function OpenWebUIShell({
  sidebar,
  navbar,
  timeline,
  composer,
  controls,
  controlsOpen,
  sidebarOpen,
  isMobile,
  sidebarWidth,
  controlsWidth,
  onSidebarWidthChange,
  onControlsWidthChange,
  mobileControlsSheet,
  onSidebarBackdropClick,
}: Props) {
  const startSidebarResize = (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault()
    const startX = event.clientX
    const startWidth = sidebarWidth
    const onMove = (moveEvent: PointerEvent) => {
      onSidebarWidthChange(clampWidth(startWidth + moveEvent.clientX - startX, 220, 420))
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  const startControlsResize = (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault()
    const startX = event.clientX
    const startWidth = controlsWidth
    const onMove = (moveEvent: PointerEvent) => {
      onControlsWidthChange(clampWidth(startWidth - (moveEvent.clientX - startX), 300, maxControlsWidth()))
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }

  const sidebarVars = { '--sidebar-width': `${sidebarWidth}px` } as CSSProperties

  return (
    <div className="relative flex h-[100dvh] w-full overflow-hidden bg-white text-gray-900 dark:bg-gray-900 dark:text-gray-100">
      {isMobile && sidebarOpen ? (
        <div
          className="fixed inset-0 z-30 bg-black/40 md:hidden"
          onClick={onSidebarBackdropClick}
          aria-hidden
        />
      ) : null}

      <div
        className={`shrink-0 transition-[transform] duration-200 ${
          isMobile
            ? sidebarOpen
              ? 'fixed inset-y-0 left-0 z-40 shadow-xl'
              : 'fixed inset-y-0 left-0 z-40 -translate-x-full'
            : sidebarOpen
              ? ''
              : 'hidden'
        }`}
        style={sidebarVars}
      >
        {sidebar}
      </div>
      {!isMobile && sidebarOpen ? (
        <button
          type="button"
          aria-label="Resize sidebar"
          className="z-10 hidden w-1 shrink-0 cursor-col-resize bg-transparent hover:bg-gray-300 md:block dark:hover:bg-gray-700"
          onPointerDown={startSidebarResize}
        />
      ) : null}

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {navbar}
        <div className="flex min-h-0 flex-1">
          <div className="flex min-h-0 min-w-0 flex-1 flex-col border-r border-gray-200 dark:border-gray-800">
            <div className="min-h-0 flex-1 overflow-y-auto">{timeline}</div>
            <div className="shrink-0 border-t border-gray-200 bg-white p-3 dark:border-gray-800 dark:bg-gray-900 md:p-4">
              {composer}
            </div>
          </div>
          {controlsOpen && controls && !isMobile ? (
            <>
              <button
                type="button"
                aria-label="Resize workflow controls"
                className="z-10 hidden w-1 shrink-0 cursor-col-resize bg-transparent hover:bg-gray-300 md:block dark:hover:bg-gray-700"
                onPointerDown={startControlsResize}
              />
              <aside
                className="hidden min-h-0 shrink-0 flex-col border-l border-gray-200 bg-gray-50 md:flex dark:border-gray-800 dark:bg-gray-950"
                style={{ width: controlsWidth }}
              >
                {controls}
              </aside>
            </>
          ) : null}
        </div>
      </div>

      {mobileControlsSheet}
    </div>
  )
}
