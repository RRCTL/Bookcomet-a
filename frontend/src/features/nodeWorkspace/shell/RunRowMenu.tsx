import { useEffect, useRef, useState } from 'react'
import type { WorkflowFolder } from '../workflowApi'

type Props = {
  folders: WorkflowFolder[]
  onStartRename: () => void
  onArchive: () => void
  onDelete: () => void
  onMove: (folderId: string | null) => void
}

export function RunRowMenu({ folders, onStartRename, onArchive, onDelete, onMove }: Props) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  return (
    <div className="relative shrink-0" ref={ref} onClick={e => e.stopPropagation()}>
      <button
        type="button"
        className="btn-ghost px-1.5 py-0.5 text-xs opacity-0 group-hover:opacity-100 focus:opacity-100"
        aria-label="Run actions"
        onClick={() => setOpen(v => !v)}
      >
        ...
      </button>
      {open ? (
        <div className="absolute right-0 top-full z-20 mt-1 min-w-[160px] rounded-xl border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-800 dark:bg-gray-850">
          <button
            type="button"
            className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-50 dark:hover:bg-gray-800"
            onMouseDown={e => {
              e.preventDefault()
              e.stopPropagation()
            }}
            onClick={() => {
              setOpen(false)
              onStartRename()
            }}
          >
            Rename
          </button>
          <div className="my-1 border-t border-gray-100 dark:border-gray-800" />
          {folders.length > 0 ? (
            <>
              <div className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                Move to folder
              </div>
              <button
                type="button"
                className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-50 dark:hover:bg-gray-800"
                onClick={() => {
                  setOpen(false)
                  onMove(null)
                }}
              >
                No folder
              </button>
              {folders.map(f => (
                <button
                  key={f.id}
                  type="button"
                  className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-50 dark:hover:bg-gray-800"
                  onClick={() => {
                    setOpen(false)
                    onMove(f.id)
                  }}
                >
                  {f.name}
                </button>
              ))}
              <div className="my-1 border-t border-gray-100 dark:border-gray-800" />
            </>
          ) : null}
          <button
            type="button"
            className="block w-full px-3 py-1.5 text-left text-sm text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-800"
            onClick={() => {
              setOpen(false)
              onArchive()
            }}
          >
            Archive
          </button>
          <div className="my-1 border-t border-gray-100 dark:border-gray-800" />
          <button
            type="button"
            className="block w-full px-3 py-1.5 text-left text-sm text-red-700 hover:bg-gray-50 dark:text-red-400 dark:hover:bg-gray-800"
            onClick={() => {
              setOpen(false)
              onDelete()
            }}
          >
            Delete
          </button>
        </div>
      ) : null}
    </div>
  )
}
