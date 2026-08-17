import { useEffect, useRef, useState } from 'react'

type Props = {
  folderName: string
  onRename: (name: string) => void
  onDelete: () => void
}

export function FolderRowMenu({ folderName, onRename, onDelete }: Props) {
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(folderName)
  const ref = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setName(folderName)
  }, [folderName])

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  useEffect(() => {
    if (editing) inputRef.current?.focus()
  }, [editing])

  if (editing) {
    return (
      <input
        ref={inputRef}
        className="ow-input max-w-[120px] py-0.5 text-xs"
        value={name}
        onChange={e => setName(e.target.value)}
        onClick={e => e.stopPropagation()}
        onKeyDown={e => {
          if (e.key === 'Enter' && name.trim()) {
            onRename(name.trim())
            setEditing(false)
          }
          if (e.key === 'Escape') setEditing(false)
        }}
        onBlur={() => {
          if (name.trim() && name.trim() !== folderName) onRename(name.trim())
          setEditing(false)
        }}
      />
    )
  }

  return (
    <div className="relative shrink-0" ref={ref} onClick={e => e.stopPropagation()}>
      <button
        type="button"
        className="btn-ghost px-1.5 py-0.5 text-xs opacity-0 group-hover:opacity-100 focus:opacity-100"
        aria-label="Folder actions"
        onClick={() => setOpen(v => !v)}
      >
        ...
      </button>
      {open ? (
        <div className="absolute right-0 top-full z-20 mt-1 min-w-[140px] rounded-xl border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-800 dark:bg-gray-850">
          <button
            type="button"
            className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-50 dark:hover:bg-gray-800"
            onClick={() => {
              setOpen(false)
              setEditing(true)
            }}
          >
            Rename
          </button>
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
