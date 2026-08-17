import { useEffect, useRef, useState } from 'react'
import type { WorkflowRunSummaryFileStatus } from '../workflowApi'
import { FileStatusIcon } from './FileStatusIcon'

const ICON_PX = 14
const GAP_PX = 4
const MORE_PX = 14

type Props = {
  statuses: WorkflowRunSummaryFileStatus[]
}

function iconCountForWidth(containerWidth: number, total: number): number {
  if (total === 0 || containerWidth <= 0) return total
  const slot = ICON_PX + GAP_PX
  const maxIcons = Math.floor((containerWidth + GAP_PX) / slot)
  if (total <= maxIcons) return total
  const maxWithMore = Math.floor((containerWidth - MORE_PX + GAP_PX) / slot)
  return Math.max(1, maxWithMore - 1)
}

export function FileStatusIconRow({ statuses }: Props) {
  const ref = useRef<HTMLSpanElement>(null)
  const [iconCount, setIconCount] = useState(statuses.length)

  useEffect(() => {
    const el = ref.current
    if (!el) return

    const recompute = () => setIconCount(iconCountForWidth(el.clientWidth, statuses.length))

    const ro = new ResizeObserver(recompute)
    ro.observe(el)
    recompute()
    return () => ro.disconnect()
  }, [statuses.length])

  const showMore = iconCount < statuses.length
  const shown = statuses.slice(0, iconCount)
  const hidden = statuses.length - iconCount

  return (
    <span
      ref={ref}
      className="erp-proc-batch-icons"
      aria-label={
        showMore
          ? `${statuses.length} files, ${hidden} not shown`
          : `${statuses.length} file${statuses.length === 1 ? '' : 's'}`
      }
    >
      {shown.map(f => (
        <FileStatusIcon key={f.task_file_id} status={f.file_status} />
      ))}
      {showMore ? (
        <span
          className="task-more-icon"
          title={`${hidden} more file${hidden === 1 ? '' : 's'}`}
          aria-hidden="true"
        >
          ...
        </span>
      ) : null}
    </span>
  )
}
