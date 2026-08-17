import type { WorkflowTemplate } from '../workflowApi'

type Props = {
  templates: WorkflowTemplate[]
  activeMode: string
  onClose: () => void
  onSaveCurrent: (name: string, asDefault: boolean) => void
  onDelete: (id: string) => void
  onSetDefault: (id: string) => void
}

export function TemplatesManagerModal({
  templates,
  activeMode,
  onClose,
  onSaveCurrent,
  onDelete,
  onSetDefault,
}: Props) {
  const modeTemplates = templates.filter(t => t.processing_mode === activeMode)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="ow-card max-h-[80vh] w-full max-w-lg overflow-y-auto p-6">
        <h2 className="mb-2 text-lg font-semibold">Workflow templates</h2>
        <p className="mb-4 text-sm text-gray-500">Mode: {activeMode}</p>
        {modeTemplates.length === 0 ? (
          <p className="mb-4 text-sm text-gray-500">No templates for this mode yet.</p>
        ) : (
          <ul className="mb-4 space-y-2">
            {modeTemplates.map(t => (
              <li
                key={t.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-gray-200 p-3 dark:border-gray-800"
              >
                <div>
                  <div className="font-medium">{t.name}</div>
                  {t.is_default ? (
                    <span className="text-xs text-green-700 dark:text-green-400">Default</span>
                  ) : null}
                </div>
                <div className="flex gap-2">
                  {!t.is_default ? (
                    <button type="button" className="btn-secondary px-2 py-1 text-xs" onClick={() => onSetDefault(t.id)}>
                      Set default
                    </button>
                  ) : null}
                  <button type="button" className="btn-destructive px-2 py-1 text-xs" onClick={() => onDelete(t.id)}>
                    Delete
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap gap-2 border-t border-gray-100 pt-4 dark:border-gray-800">
          <button
            type="button"
            className="btn-primary"
            onClick={() => {
              const name = `Template ${new Date().toISOString().slice(0, 10)}`
              onSaveCurrent(name, false)
            }}
          >
            Save current graph
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              const name = `Default ${activeMode}`
              onSaveCurrent(name, true)
            }}
          >
            Save as default
          </button>
          <button type="button" className="btn-ghost" onClick={onClose}>
            Close
          </button>
        </div>
        <p className="mt-3 text-xs text-gray-400">
          Use the workflow palette to deploy a template. New runs load the company default template for their mode,
          then fall back to the built-in graph.
        </p>
      </div>
    </div>
  )
}
