import type { ReactNode } from 'react'

type Props = {
  children: ReactNode
  /** Right-aligned actions (e.g. Search button). */
  actions?: ReactNode
}

export function FilterBar({ children, actions }: Props) {
  return (
    <div className="erp-filterbar">
      {children}
      <div className="erp-grow" />
      {actions}
    </div>
  )
}
