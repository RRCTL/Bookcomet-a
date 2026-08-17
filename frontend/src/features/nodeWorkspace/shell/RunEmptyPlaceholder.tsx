type Props = {
  suggestedMode?: string
  onNewRun: () => void
}

const STEPS = [
  'Attach files in the composer (click or drag and drop)',
  'Set receipt and table style if required, then Run',
  'Review the table in the timeline and Approve',
  'Chart of accounts deploys after approval',
]

export function RunEmptyPlaceholder({ suggestedMode, onNewRun }: Props) {
  const mode = suggestedMode?.toUpperCase() ?? 'AR'

  return (
    <div className="flex h-full flex-col items-center justify-center px-6 py-12 text-center">
      <div className="w-full max-w-lg">
        <h2 className="mb-2 text-xl font-semibold text-gray-900 dark:text-gray-100">Start a workflow run</h2>
        <p className="mb-8 text-sm text-gray-500">
          Select a run from the sidebar or create a new {mode} batch.
        </p>
        <ol className="mb-8 space-y-3 text-left text-sm text-gray-600 dark:text-gray-400">
          {STEPS.map((step, i) => (
            <li key={i} className="flex gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-gray-100 text-xs font-semibold text-gray-700 dark:bg-gray-800 dark:text-gray-300">
                {i + 1}
              </span>
              <span className="pt-0.5">{step}</span>
            </li>
          ))}
        </ol>
        <button type="button" className="btn-primary" onClick={onNewRun}>
          New run
        </button>
      </div>
    </div>
  )
}
