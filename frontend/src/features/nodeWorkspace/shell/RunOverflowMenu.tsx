import { useEffect, useRef, useState } from 'react'

type MenuItem = {
  id: string
  label: string
  onClick: () => void
  destructive?: boolean
}

type Props = {
  items: MenuItem[]
}

export function RunOverflowMenu({ items }: Props) {
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
    <div className="relative" ref={ref}>
      <button
        type="button"
        className="btn-ghost px-2"
        aria-label="Menu"
        aria-expanded={open}
        onClick={() => setOpen(v => !v)}
      >
        ...
      </button>
      {open ? (
        <div className="absolute right-0 top-full z-30 mt-1 min-w-[180px] rounded-xl border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-800 dark:bg-gray-850">
          {items.map(item => (
            <button
              key={item.id}
              type="button"
              className={`block w-full px-3 py-2 text-left text-sm hover:bg-gray-50 dark:hover:bg-gray-800 ${
                item.destructive ? 'text-red-700 dark:text-red-400' : 'text-gray-700 dark:text-gray-200'
              }`}
              onClick={() => {
                setOpen(false)
                item.onClick()
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
