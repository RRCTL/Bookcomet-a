import { useEffect, useState } from 'react'
import type { WorkflowGraph, WorkflowNodeCatalogEntry } from '../workflowApi'

type GraphNode = WorkflowGraph['nodes'][number]

type Props = {
  node?: GraphNode
  selectedCount?: number
  catalogEntry?: WorkflowNodeCatalogEntry
  onPatch: (nodeId: string, patch: Record<string, unknown>) => void
  onDelete: (nodeId: string) => void
  onOpenSkill: (skillKey?: string | null) => void
}

function paramLabel(key: string): string {
  return key
    .replace(/([A-Z])/g, ' $1')
    .replace(/_/g, ' ')
    .replace(/^./, char => char.toUpperCase())
}

function valueForInput(value: unknown): string {
  if (value == null) return ''
  return String(value)
}

export function NodeSettingsPanel({ node, selectedCount = 0, catalogEntry, onPatch, onDelete, onOpenSkill }: Props) {
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    setExpanded(false)
  }, [node?.id])

  if (selectedCount > 1) {
    return (
      <div className="border-b border-gray-200 p-3 dark:border-gray-800">
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Node settings</div>
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">{selectedCount} nodes selected</p>
          <button type="button" className="btn-secondary text-xs" onClick={() => onDelete('')}>
            Delete
          </button>
        </div>
        <p className="mt-2 text-xs text-gray-500">Shift + click nodes on the canvas to change this selection.</p>
      </div>
    )
  }

  if (!node) {
    return (
      <div className="border-b border-gray-200 p-3 text-sm text-gray-500 dark:border-gray-800">
        Select a node to edit its settings.
      </div>
    )
  }

  const params = catalogEntry?.params ?? {}
  const skillKey = typeof node.data.skillKey === 'string' ? node.data.skillKey : null

  return (
    <div className="border-b border-gray-200 p-3 dark:border-gray-800">
      <div className="mb-3 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">Node settings</div>
          <div className="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">
            {String(node.data.label ?? catalogEntry?.label ?? node.type)}
          </div>
          <div className="text-xs text-gray-500">{node.type}</div>
        </div>
        <button
          type="button"
          className="btn-secondary text-xs"
          title="Delete node"
          onClick={() => onDelete(node.id)}
        >
          Delete
        </button>
      </div>

      {!expanded ? (
        <button type="button" className="btn-secondary w-full text-xs" onClick={() => setExpanded(true)}>
          Expand settings
        </button>
      ) : (
        <button type="button" className="btn-ghost mb-3 w-full text-xs" onClick={() => setExpanded(false)}>
          Collapse settings
        </button>
      )}

      {!expanded ? (
        <p className="text-xs text-gray-500">Settings are hidden until you need to edit this node.</p>
      ) : null}

      {expanded ? (
        <>

      <label className="mb-2 block">
        <span className="mb-1 block text-xs font-medium text-gray-500">Node name</span>
        <input
          className="ow-input"
          value={String(node.data.label ?? '')}
          onChange={event => onPatch(node.id, { label: event.target.value })}
        />
      </label>

      <div className="space-y-2">
        {Object.entries(params).map(([key, spec]) => {
          const type = String(spec.type ?? 'string')
          const rawValue = node.data[key] ?? spec.default
          const options = Array.isArray(spec.options) ? spec.options.map(String) : []
          if (options.length > 0) {
            return (
              <label key={key} className="block">
                <span className="mb-1 block text-xs font-medium text-gray-500">{paramLabel(key)}</span>
                <select
                  className="ow-input"
                  value={valueForInput(rawValue)}
                  onChange={event => onPatch(node.id, { [key]: event.target.value || null })}
                >
                  {options.map(option => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </label>
            )
          }
          if (type === 'boolean') {
            return (
              <label key={key} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={Boolean(rawValue)}
                  onChange={event => onPatch(node.id, { [key]: event.target.checked })}
                />
                {paramLabel(key)}
              </label>
            )
          }
          if (type === 'number') {
            return (
              <label key={key} className="block">
                <span className="mb-1 block text-xs font-medium text-gray-500">{paramLabel(key)}</span>
                <input
                  className="ow-input"
                  type="number"
                  value={valueForInput(rawValue)}
                  onChange={event => {
                    const next = event.target.value
                    onPatch(node.id, { [key]: next === '' ? null : Number(next) })
                  }}
                />
              </label>
            )
          }
          return (
            <label key={key} className="block">
              <span className="mb-1 block text-xs font-medium text-gray-500">{paramLabel(key)}</span>
              <input
                className="ow-input"
                value={valueForInput(rawValue)}
                onChange={event => onPatch(node.id, { [key]: event.target.value || null })}
              />
            </label>
          )
        })}
      </div>

      {node.type === 'ExternalApiCall' ? (
        <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-100">
          This node may send document images, OCR text, accounting data, and extracted fields to an external API.
        </div>
      ) : null}

      {catalogEntry?.skillAttachable ? (
        <button type="button" className="btn-secondary mt-3 w-full text-xs" onClick={() => onOpenSkill(skillKey)}>
          Edit attached skill
        </button>
      ) : null}
        </>
      ) : null}
    </div>
  )
}
