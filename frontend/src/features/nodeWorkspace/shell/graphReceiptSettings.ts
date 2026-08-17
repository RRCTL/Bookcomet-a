import type { WorkflowGraph } from '../workflowApi'
import type { ApVlmReceiptSignal, ApVlmTablePreset } from '../../workspace/apComposerOptions'

export function receiptSettingsFromGraph(graph: WorkflowGraph): {
  receiptSignal: ApVlmReceiptSignal | null
  tablePreset: ApVlmTablePreset | null
} {
  const node = graph.nodes.find(n => n.type === 'ReceiptStyle')
  const rs = node?.data?.receiptSignal
  const tp = node?.data?.tablePreset
  return {
    receiptSignal: typeof rs === 'string' ? (rs as ApVlmReceiptSignal) : null,
    tablePreset: typeof tp === 'string' ? (tp as ApVlmTablePreset) : null,
  }
}
