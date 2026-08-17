import type { ReactNode } from 'react'
import { FileStatusIcon } from './FileStatusIcon'

export type ControlsTab = 'workflow' | 'log' | 'files'

type Props = {
  tab: ControlsTab
  onTabChange: (tab: ControlsTab) => void
  workflowPanel: ReactNode
  logPanel: ReactNode
  filesPanel: ReactNode
}

const TABS: { id: ControlsTab; label: string }[] = [
  { id: 'workflow', label: 'Workflow' },
  { id: 'log', label: 'Log' },
  { id: 'files', label: 'Files' },
]

export function ControlsPane({ tab, onTabChange, workflowPanel, logPanel, filesPanel }: Props) {
  return (
    <>
      <div className="flex shrink-0 border-b border-gray-200 dark:border-gray-800">
        {TABS.map(t => (
          <button
            key={t.id}
            type="button"
            className={`flex-1 px-3 py-2 text-xs font-semibold uppercase tracking-wide transition ${
              tab === t.id
                ? 'border-b-2 border-gray-900 bg-gray-50 text-gray-900 dark:border-gray-100 dark:bg-gray-850 dark:text-gray-100'
                : 'text-gray-500 hover:bg-gray-50 hover:text-gray-700 dark:hover:bg-gray-900 dark:hover:text-gray-300'
            }`}
            onClick={() => onTabChange(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {tab === 'workflow' ? (
          <div className="min-h-0 flex-1 overflow-y-auto">{workflowPanel}</div>
        ) : null}
        {tab === 'log' ? logPanel : null}
        {tab === 'files' ? filesPanel : null}
      </div>
    </>
  )
}

type ConsoleLine = { ts?: string; level: string; message: string }

export function ControlsLogPanel({ lines }: { lines: ConsoleLine[] }) {
  return (
    <div className="h-full min-h-[200px] overflow-y-auto p-3 font-mono text-xs">
      {lines.length === 0 ? (
        <p className="text-gray-500">No log output yet.</p>
      ) : (
        lines.map((line, i) => (
          <div key={i} className="mb-1 text-gray-700 dark:text-gray-300">
            <span className="text-gray-400">[{line.level}]</span> {line.message}
          </div>
        ))
      )}
    </div>
  )
}

export function ControlsFilesPanel({
  files,
  onPreviewFile,
}: {
  files: Array<{ original_filename?: string | null; file_status: string; task_file_id: string }>
  onPreviewFile?: (taskFileId: string) => void
}) {
  return (
    <div className="h-full min-h-[200px] overflow-y-auto p-3">
      {files.length === 0 ? (
        <p className="text-sm text-gray-500">No files attached.</p>
      ) : (
        <ul className="space-y-2 text-sm">
          {files.map(f => (
            <li key={f.task_file_id} className="rounded-lg border border-gray-200 p-2 dark:border-gray-800">
              <div className="flex items-start justify-between gap-2">
                <FileStatusIcon status={f.file_status} />
                <button
                  type="button"
                  className="min-w-0 flex-1 text-left font-medium hover:underline"
                  onClick={() => onPreviewFile?.(f.task_file_id)}
                  disabled={!onPreviewFile}
                >
                  {f.original_filename ?? f.task_file_id}
                </button>
                {onPreviewFile ? (
                  <button
                    type="button"
                    className="shrink-0 text-xs text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
                    onClick={() => onPreviewFile(f.task_file_id)}
                  >
                    Preview
                  </button>
                ) : null}
              </div>
              <div className="text-xs text-gray-500">{f.file_status}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
