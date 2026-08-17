import { layoutGraphVertical } from './defaultGraphs'
import type { WorkflowGraph, WorkflowTemplate } from './workflowApi'

export function sortPaletteTemplates(templates: WorkflowTemplate[]): WorkflowTemplate[] {
  return [...templates].sort((a, b) => {
    if (a.is_default !== b.is_default) return a.is_default ? -1 : 1
    return a.name.localeCompare(b.name)
  })
}

export function templateMatchingGraph(
  templates: WorkflowTemplate[],
  graph: WorkflowGraph,
): WorkflowTemplate | undefined {
  let normalized = ''
  try {
    normalized = JSON.stringify(layoutGraphVertical(graph))
  } catch {
    return undefined
  }
  return templates.find(template => {
    try {
      return JSON.stringify(layoutGraphVertical(template.graph_json)) === normalized
    } catch {
      return false
    }
  })
}

export function resolvePaletteTemplateSelection(
  templates: WorkflowTemplate[],
  selectedId: string,
  graph: WorkflowGraph,
): WorkflowTemplate | undefined {
  if (!templates.length) return undefined
  if (selectedId) {
    const picked = templates.find(t => t.id === selectedId)
    if (picked) return picked
  }
  return (
    templateMatchingGraph(templates, graph) ??
    templates.find(t => t.is_default) ??
    templates[0]
  )
}
