import type { ReactNode } from 'react'
import { ControlsPane, type ControlsTab } from './ControlsPane'

type Props = {
  open: boolean
  onClose: () => void
  tab: ControlsTab
  onTabChange: (tab: ControlsTab) => void
  workflowPanel: ReactNode
  logPanel: ReactNode
  filesPanel: ReactNode
}

export function ControlsBottomSheet({
  open,
  onClose,
  tab,
  onTabChange,
  workflowPanel,
  logPanel,
  filesPanel,
}: Props) {
  if (!open) return null

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/40 md:hidden" onClick={onClose} aria-hidden />
      <div
        className="fixed inset-x-0 bottom-0 z-50 flex max-h-[85dvh] flex-col rounded-t-2xl border border-gray-200 bg-gray-50 shadow-xl dark:border-gray-800 dark:bg-gray-950 md:hidden"
        role="dialog"
        aria-modal="true"
        aria-label="Workflow controls"
      >
        <div className="flex shrink-0 items-center justify-between border-b border-gray-200 px-4 py-2 dark:border-gray-800">
          <span className="text-sm font-semibold">Workflow</span>
          <button type="button" className="btn-ghost px-2 text-sm" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <ControlsPane
            tab={tab}
            onTabChange={onTabChange}
            workflowPanel={workflowPanel}
            logPanel={logPanel}
            filesPanel={filesPanel}
          />
        </div>
      </div>
    </>
  )
}
