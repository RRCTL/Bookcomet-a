const NOTICE_KEY = 'bookcomet_node_workspace_v1_notice_seen'

export function shouldShowCutoverNotice(): boolean {
  try {
    return localStorage.getItem(NOTICE_KEY) !== '1'
  } catch {
    return false
  }
}

export function dismissCutoverNotice(): void {
  try {
    localStorage.removeItem('bookcomet_ap_composer_')
    const keys: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (k?.startsWith('bookcomet_ap_composer_')) keys.push(k)
      if (k?.startsWith('bookcomet_task_')) keys.push(k)
    }
    keys.forEach(k => localStorage.removeItem(k))
    localStorage.setItem(NOTICE_KEY, '1')
  } catch {
    localStorage.setItem(NOTICE_KEY, '1')
  }
}

type Props = { onDismiss: () => void }

export function CutoverNotice({ onDismiss }: Props) {
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true">
      <div className="ow-card w-full max-w-md p-6">
        <h2 className="mb-2 text-lg font-semibold">Workspace upgraded</h2>
        <p className="mb-4 text-sm text-gray-600 dark:text-gray-400">
          Bookcomet now uses an Open WebUI-style workflow shell with a sidebar, timeline, and workflow
          controls pane. Local task cache was cleared for this one-time migration. Start a new run from
          the sidebar.
        </p>
        <button type="button" className="btn-primary" onClick={onDismiss}>
          Continue
        </button>
      </div>
    </div>
  )
}
