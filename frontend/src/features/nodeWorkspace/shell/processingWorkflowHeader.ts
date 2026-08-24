import { layoutGraphVertical } from '../defaultGraphs'
import type { WorkflowGraph, WorkflowNodeCatalogEntry, WorkflowTemplate } from '../workflowApi'
import type { ApVlmReceiptSignal, ApVlmTablePreset } from '../../workspace/apComposerOptions'
import { receiptSettingsFromGraph } from './graphReceiptSettings'
import { resolvePaletteTemplateSelection } from '../workflowTemplates'
import type { ReVlmWorkflowSettings } from '../reVlmReasonChips'

const VLM_PROVIDER_NODE_TYPES = new Set([
  'VLM_API',
  'VLMProposer',
  'VLMJudge',
  'ManagerReview',
  'VLMDoubleCheck',
])

/** Fallback when node-catalog has not loaded / has no provider options. */
export const DEFAULT_WORKFLOW_PROVIDER_OPTIONS = ['Qwen'] as const

/** Provider labels from `/api/workflows/node-catalog` (WORKFLOW_PROVIDERS on API). */
export function providerOptionsFromCatalog(
  catalog: Array<Pick<WorkflowNodeCatalogEntry, 'params'>>,
): string[] {
  for (const entry of catalog) {
    const raw = entry.params?.provider?.options
    if (Array.isArray(raw) && raw.length > 0) {
      return raw.map(String)
    }
  }
  return [...DEFAULT_WORKFLOW_PROVIDER_OPTIONS]
}

/** Keep select value valid when legacy graph labels (e.g. DeepSeek) are not in API options. */
export function resolveProviderSelection(current: string | null | undefined, options: string[]): string {
  const label = (current ?? '').trim()
  if (label && options.includes(label)) return label
  return options[0] ?? DEFAULT_WORKFLOW_PROVIDER_OPTIONS[0]
}

export function providerFromGraph(graph: WorkflowGraph): string {
  const vlm = graph.nodes.find(n => n.type === 'VLM_API')
  if (vlm) return String(vlm.data?.provider ?? 'Qwen')
  const proposer = graph.nodes.find(n => n.type === 'VLMProposer')
  if (proposer) return String(proposer.data?.provider ?? 'Qwen')
  return 'Qwen'
}

export function hasVlmProviderControl(graph: WorkflowGraph): boolean {
  return graph.nodes.some(n => VLM_PROVIDER_NODE_TYPES.has(n.type))
}

export function patchProviderInGraph(graph: WorkflowGraph, provider: string): WorkflowGraph {
  return {
    ...graph,
    nodes: graph.nodes.map(n =>
      VLM_PROVIDER_NODE_TYPES.has(n.type) ? { ...n, data: { ...n.data, provider } } : n,
    ),
  }
}

export function workflowSettingsFromGraph(
  graph: WorkflowGraph,
  templates: WorkflowTemplate[],
  processingMode: string,
): ReVlmWorkflowSettings {
  const modeTemplates = templates.filter(t => t.processing_mode === processingMode)
  const resolved = resolvePaletteTemplateSelection(modeTemplates, '', graph)
  const receipt = receiptSettingsFromGraph(graph)
  return {
    templateId: resolved?.id ?? '',
    provider: providerFromGraph(graph),
    receiptSignal: receipt.receiptSignal ?? undefined,
    tablePreset: receipt.tablePreset ?? undefined,
  }
}

export function applyWorkflowSettingsToGraph(
  baseGraph: WorkflowGraph,
  templates: WorkflowTemplate[],
  settings: ReVlmWorkflowSettings,
): WorkflowGraph {
  let graph = baseGraph
  if (settings.templateId) {
    const tpl = templates.find(t => t.id === settings.templateId)
    if (tpl) {
      graph = layoutGraphVertical(JSON.parse(JSON.stringify(tpl.graph_json)) as WorkflowGraph)
    }
  }
  if (settings.provider) {
    graph = patchProviderInGraph(graph, settings.provider)
  }
  const receiptNode = graph.nodes.find(n => n.type === 'ReceiptStyle')
  if (receiptNode && (settings.receiptSignal != null || settings.tablePreset != null)) {
    graph = {
      ...graph,
      nodes: graph.nodes.map(n =>
        n.id === receiptNode.id
          ? {
              ...n,
              data: {
                ...n.data,
                ...(settings.receiptSignal != null ? { receiptSignal: settings.receiptSignal } : {}),
                ...(settings.tablePreset != null ? { tablePreset: settings.tablePreset } : {}),
              },
            }
          : n,
      ),
    }
  }
  return graph
}

export function workflowSettingsChanged(
  graph: WorkflowGraph,
  templates: WorkflowTemplate[],
  processingMode: string,
  next: ReVlmWorkflowSettings,
): boolean {
  const current = workflowSettingsFromGraph(graph, templates, processingMode)
  return (
    current.templateId !== next.templateId ||
    current.provider !== next.provider ||
    (next.receiptSignal != null && current.receiptSignal !== next.receiptSignal) ||
    (next.tablePreset != null && current.tablePreset !== next.tablePreset)
  )
}
