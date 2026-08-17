import { useEffect, useMemo, useState } from 'react'
import type { WorkflowSkill } from '../workflowApi'

const FIELDS: Array<{ key: keyof WorkflowSkill['structured_json']; label: string }> = [
  { key: 'role', label: 'Role' },
  { key: 'rules', label: 'Rules' },
  { key: 'input_context', label: 'Input Context' },
  { key: 'output_format', label: 'Output Format' },
  { key: 'failure_handling', label: 'Failure Handling' },
  { key: 'retry_policy', label: 'Retry Policy' },
  { key: 'selection_reason', label: 'Selection Reason' },
]

type Props = {
  open: boolean
  mode: string
  skills: WorkflowSkill[]
  busy?: boolean
  onClose: () => void
  onSave: (skill: WorkflowSkill, structured: Record<string, string>) => void
  onReset: (skill: WorkflowSkill) => void
  onRollback: (skill: WorkflowSkill, version?: number) => void
}

export function WorkflowSkillsModal({
  open,
  mode,
  skills,
  busy,
  onClose,
  onSave,
  onReset,
  onRollback,
}: Props) {
  const [activeKey, setActiveKey] = useState<string>('')
  const active = useMemo(
    () => skills.find(skill => skill.skill_key === activeKey) ?? skills[0],
    [activeKey, skills],
  )
  const [draft, setDraft] = useState<Record<string, string>>({})

  useEffect(() => {
    if (!open) return
    setActiveKey(prev => prev || skills[0]?.skill_key || '')
  }, [open, skills])

  useEffect(() => {
    setDraft(active?.structured_json ?? {})
  }, [active?.id, active?.version])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="ow-card flex max-h-[90vh] w-full max-w-5xl flex-col overflow-hidden">
        <div className="flex items-start justify-between gap-4 border-b border-gray-200 p-4 dark:border-gray-800">
          <div>
            <h3 className="text-lg font-semibold">Workflow skills</h3>
            <p className="text-sm text-gray-500">
              Edit structured English runtime skills for {mode}. Nodes link here instead of editing prompts inline.
            </p>
          </div>
          <button type="button" className="btn-ghost" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden md:grid-cols-[220px_1fr]">
          <aside className="border-b border-gray-200 p-3 dark:border-gray-800 md:border-b-0 md:border-r">
            <div className="space-y-2">
              {skills.map(skill => (
                <button
                  key={skill.id}
                  type="button"
                  className={`w-full rounded-lg px-3 py-2 text-left text-sm ${
                    active?.id === skill.id
                      ? 'bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900'
                      : 'hover:bg-gray-100 dark:hover:bg-gray-800'
                  }`}
                  onClick={() => setActiveKey(skill.skill_key)}
                >
                  <div className="font-medium">{skill.skill_key.replaceAll('_', ' ')}</div>
                  <div className="text-xs opacity-75">Version {skill.version}</div>
                </button>
              ))}
            </div>
          </aside>

          <main className="min-h-0 overflow-y-auto p-4">
            {!active ? (
              <p className="text-sm text-gray-500">No workflow skills available.</p>
            ) : (
              <div className="grid gap-4 lg:grid-cols-2">
                <section className="space-y-3">
                  {FIELDS.map(field => (
                    <label key={field.key} className="block">
                      <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-gray-500">
                        {field.label}
                      </span>
                      <textarea
                        className="ow-input min-h-[80px] w-full"
                        value={draft[field.key] ?? ''}
                        onChange={event =>
                          setDraft(prev => ({ ...prev, [field.key]: event.target.value }))
                        }
                      />
                    </label>
                  ))}
                </section>

                <section className="space-y-3">
                  <div>
                    <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
                      Generated SKILL.md preview
                    </div>
                    <pre className="max-h-[520px] overflow-auto rounded-lg bg-gray-950 p-3 text-xs text-gray-100">
                      {active.generated_markdown}
                    </pre>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="btn-primary"
                      disabled={busy}
                      onClick={() => onSave(active, draft)}
                    >
                      Save skill
                    </button>
                    <button
                      type="button"
                      className="btn-secondary"
                      disabled={busy}
                      onClick={() => onReset(active)}
                    >
                      Reset default
                    </button>
                    <button
                      type="button"
                      className="btn-secondary"
                      disabled={busy || active.previous_versions.length === 0}
                      onClick={() => onRollback(active, active.previous_versions[0]?.version)}
                    >
                      Roll back
                    </button>
                  </div>
                </section>
              </div>
            )}
          </main>
        </div>
      </div>
    </div>
  )
}
