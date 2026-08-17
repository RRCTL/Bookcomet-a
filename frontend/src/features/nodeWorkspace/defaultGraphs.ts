import type { WorkflowGraph } from './workflowApi'

export const GRAPH_SCHEMA_VERSION = 2

export const REQUIRED_RUN_NODE_TYPES = new Set([
  'Files',
  'ModeConfig',
  'TableReview',
  'SaveResult',
])

export const NODE_IO: Record<string, { inputs: Record<string, string>; outputs: Record<string, string> }> = {
  Files: { inputs: {}, outputs: { out: 'FILE_BATCH' } },
  ModeConfig: { inputs: { in: 'FILE_BATCH' }, outputs: { out: 'OCR_CONTEXT' } },
  ReceiptStyle: { inputs: { in: 'OCR_CONTEXT' }, outputs: { out: 'OCR_CONTEXT' } },
  VLM_API: { inputs: { in: 'OCR_CONTEXT' }, outputs: { out: 'OCR_RESULT' } },
  If: { inputs: { in: 'FILE_ITEM' }, outputs: { true: 'FILE_ITEM', false: 'FILE_ITEM' } },
  Switch: { inputs: { in: 'FILE_ITEM' }, outputs: { out0: 'FILE_ITEM', out1: 'FILE_ITEM', default: 'FILE_ITEM' } },
  VLMDoubleCheck: { inputs: { in: 'OCR_RESULT' }, outputs: { out: 'VERIFIED_OCR_RESULT' } },
  VLMProposer: { inputs: { in: 'OCR_CONTEXT' }, outputs: { out: 'OCR_PROPOSAL' } },
  ProposalPoolJoin: { inputs: { in: 'OCR_PROPOSAL' }, outputs: { out: 'OCR_PROPOSAL_POOL' } },
  VLMJudge: { inputs: { in: 'OCR_PROPOSAL_POOL' }, outputs: { out: 'JUDGE_REPORT' } },
  VoteSelector: { inputs: { in: 'JUDGE_REPORT' }, outputs: { out: 'SELECTED_OCR_RESULT' } },
  ManagerReview: { inputs: { in: 'SELECTED_OCR_RESULT' }, outputs: { out: 'VERIFICATION_REPORT' } },
  ConditionRouter: {
    inputs: { in: 'VERIFICATION_REPORT' },
    outputs: { pass: 'VERIFICATION_REPORT', retry: 'VERIFICATION_REPORT', fail: 'VERIFICATION_REPORT' },
  },
  ExternalApiCall: { inputs: { in: 'ANY' }, outputs: { out: 'ANY' } },
  MergeResult: { inputs: { in: 'VERIFICATION_REPORT' }, outputs: { out: 'OCR_RESULT' } },
  TableReview: { inputs: { in: 'OCR_RESULT' }, outputs: { out: 'APPROVED_TABLE' } },
  SaveResult: { inputs: { in: 'APPROVED_TABLE' }, outputs: { out: 'SAVED_PACKAGE' } },
}

const EDGE_COMPAT: Record<string, Set<string>> = {
  FILE_BATCH: new Set(['FILE_BATCH', 'OCR_CONTEXT']),
  FILE_ITEM: new Set(['FILE_ITEM', 'OCR_RESULT']),
  OCR_CONTEXT: new Set(['OCR_CONTEXT']),
  OCR_RESULT: new Set(['OCR_RESULT', 'VERIFIED_OCR_RESULT', 'APPROVED_TABLE']),
  OCR_PROPOSAL: new Set(['OCR_PROPOSAL', 'OCR_PROPOSAL_POOL']),
  OCR_PROPOSAL_POOL: new Set(['OCR_PROPOSAL_POOL', 'JUDGE_REPORT']),
  JUDGE_REPORT: new Set(['JUDGE_REPORT', 'SELECTED_OCR_RESULT']),
  SELECTED_OCR_RESULT: new Set(['SELECTED_OCR_RESULT', 'VERIFICATION_REPORT', 'OCR_RESULT']),
  VERIFICATION_REPORT: new Set(['VERIFICATION_REPORT', 'OCR_RESULT']),
  VERIFIED_OCR_RESULT: new Set(['VERIFIED_OCR_RESULT', 'OCR_RESULT', 'APPROVED_TABLE']),
  APPROVED_TABLE: new Set(['APPROVED_TABLE', 'SAVED_PACKAGE']),
  SAVED_PACKAGE: new Set(['SAVED_PACKAGE']),
}

function edgeType(nodeType: string, handle: string, dir: 'in' | 'out'): string {
  const spec = NODE_IO[nodeType]
  if (!spec) return 'ANY'
  const map = dir === 'in' ? spec.inputs : spec.outputs
  if (dir === 'in' && Object.keys(map).length === 0) return 'NONE'
  return map[handle] ?? map.out ?? map.in ?? 'ANY'
}

export function edgesCompatible(
  sourceType: string,
  targetType: string,
  sourceHandle?: string | null,
  targetHandle?: string | null,
): boolean {
  const outT = edgeType(sourceType, sourceHandle || 'out', 'out')
  const inT = edgeType(targetType, targetHandle || 'in', 'in')
  if (inT === 'NONE') return false
  if (outT === 'ANY' || inT === 'ANY') return true
  if (outT === 'VERIFIED_OCR_RESULT' && inT === 'OCR_RESULT') return true
  const allowed = EDGE_COMPAT[outT]
  return allowed ? allowed.has(inT) : true
}

const NODE_X = 50
const NODE_SPACING = 40
const BRANCH_SPACING_X = 260

const ESTIMATED_NODE_HEIGHT: Record<string, number> = {
  Files: 180,
  ModeConfig: 90,
  ReceiptStyle: 210,
  VLM_API: 200,
  VLMDoubleCheck: 160,
  If: 100,
  Switch: 120,
  VLMProposer: 190,
  ProposalPoolJoin: 120,
  VLMJudge: 150,
  VoteSelector: 140,
  ManagerReview: 160,
  ConditionRouter: 140,
  ExternalApiCall: 170,
  MergeResult: 120,
  TableReview: 130,
  SaveResult: 90,
}

function estimatedNodeHeight(type: string | undefined): number {
  return ESTIMATED_NODE_HEIGHT[type ?? ''] ?? 110
}

function orderedNodeIds(graph: WorkflowGraph): string[] {
  const targets = new Set(graph.edges.map(e => e.target))
  const head = graph.nodes.find(n => !targets.has(n.id))
  if (!head) return graph.nodes.map(n => n.id)

  const nextBySource = new Map(graph.edges.map(e => [e.source, e.target]))
  const order: string[] = []
  let current: string | undefined = head.id
  const seen = new Set<string>()
  while (current && !seen.has(current)) {
    seen.add(current)
    order.push(current)
    current = nextBySource.get(current)
  }
  for (const n of graph.nodes) {
    if (!seen.has(n.id)) order.push(n.id)
  }
  return order
}

function proposalBranchLayout(graph: WorkflowGraph): Map<string, { x: number; y: number }> | null {
  const sourceToTargets = new Map<string, string[]>()
  for (const edge of graph.edges) {
    const targets = sourceToTargets.get(edge.source) ?? []
    targets.push(edge.target)
    sourceToTargets.set(edge.source, targets)
  }
  const nodeById = new Map(graph.nodes.map(node => [node.id, node]))
  const proposalIds = graph.nodes.filter(node => node.type === 'VLMProposer').map(node => node.id)
  if (proposalIds.length < 2) return null
  const pool = graph.nodes.find(node => node.type === 'ProposalPoolJoin')
  if (!pool) return null
  const sourceId = [...sourceToTargets.entries()].find(([, targets]) =>
    proposalIds.every(id => targets.includes(id)),
  )?.[0]
  if (!sourceId) return null

  const order: string[] = []
  let current: string | undefined = graph.nodes.find(node => !graph.edges.some(edge => edge.target === node.id))?.id
  const mainSeen = new Set(proposalIds)
  while (current && !mainSeen.has(current)) {
    mainSeen.add(current)
    order.push(current)
    current = (sourceToTargets.get(current) ?? []).find(id => !proposalIds.includes(id))
  }
  const hasManagerReview = graph.nodes.some(node => node.type === 'ManagerReview')
  const afterPoolOrder = hasManagerReview
    ? ['ProposalPoolJoin', 'VLMJudge', 'VoteSelector', 'ManagerReview', 'MergeResult', 'TableReview', 'SaveResult']
    : ['ProposalPoolJoin', 'VLMJudge', 'VoteSelector', 'MergeResult', 'TableReview', 'SaveResult']
    .map(type => graph.nodes.find(node => node.type === type)?.id)
    .filter((id): id is string => Boolean(id))
  const posById = new Map<string, { x: number; y: number }>()
  let y = 0
  for (const id of order) {
    posById.set(id, { x: NODE_X, y })
    y += estimatedNodeHeight(nodeById.get(id)?.type) + NODE_SPACING
  }
  const branchY = y
  const centerOffset = ((proposalIds.length - 1) * BRANCH_SPACING_X) / 2
  proposalIds.forEach((id, index) => {
    posById.set(id, { x: NODE_X + index * BRANCH_SPACING_X - centerOffset, y: branchY })
  })
  y += Math.max(...proposalIds.map(id => estimatedNodeHeight(nodeById.get(id)?.type))) + NODE_SPACING
  for (const id of afterPoolOrder) {
    posById.set(id, { x: NODE_X, y })
    y += estimatedNodeHeight(nodeById.get(id)?.type) + NODE_SPACING
  }
  for (const node of graph.nodes) {
    if (!posById.has(node.id)) {
      posById.set(node.id, { x: NODE_X, y })
      y += estimatedNodeHeight(node.type) + NODE_SPACING
    }
  }
  return posById
}

export function isHorizontalLayout(graph: WorkflowGraph): boolean {
  if (graph.nodes.length < 2) return false
  const xs = graph.nodes.map(n => n.position.x)
  const ys = graph.nodes.map(n => n.position.y)
  return Math.max(...xs) - Math.min(...xs) > Math.max(...ys) - Math.min(...ys)
}

export function needsVerticalRelayout(graph: WorkflowGraph): boolean {
  const order = orderedNodeIds(graph)
  const nodeById = new Map(graph.nodes.map(n => [n.id, n]))
  for (let i = 1; i < order.length; i++) {
    const prev = nodeById.get(order[i - 1]!)
    const curr = nodeById.get(order[i]!)
    if (!prev || !curr) continue
    const minNextY = prev.position.y + estimatedNodeHeight(prev.type) + NODE_SPACING
    if (curr.position.y < minNextY - 1) return true
  }
  return false
}

export function layoutGraphVertical(
  graph: WorkflowGraph,
  heights?: Map<string, number>,
): WorkflowGraph {
  const branchPositions = proposalBranchLayout(graph)
  const order = branchPositions ? [] : orderedNodeIds(graph)
  const nodeById = new Map(graph.nodes.map(n => [n.id, n]))
  const posById = branchPositions ?? new Map<string, { x: number; y: number }>()
  let y = 0
  for (const id of order) {
    posById.set(id, { x: NODE_X, y })
    const node = nodeById.get(id)
    const h = heights?.get(id) ?? estimatedNodeHeight(node?.type)
    y += h + NODE_SPACING
  }
  return {
    ...graph,
    schemaVersion: GRAPH_SCHEMA_VERSION,
    nodes: graph.nodes.map(n => ({
      ...n,
      position: posById.get(n.id) ?? n.position,
    })),
  }
}

function typedEdge(source: string, target: string, sourceHandle = 'out', targetHandle = 'in') {
  return {
    id: `${source}-${target}`,
    source,
    target,
    sourceHandle,
    targetHandle,
  }
}

export function defaultGraphForMode(mode: string): WorkflowGraph {
  const m = (mode || 'AR').toUpperCase()
  const nodes: WorkflowGraph['nodes'] = [
    { id: 'files', type: 'Files', position: { x: 0, y: 0 }, data: { label: 'Files', nodeType: 'Files' } },
    {
      id: 'mode',
      type: 'ModeConfig',
      position: { x: 0, y: 0 },
      data: { label: 'Mode', nodeType: 'ModeConfig', processingMode: m },
    },
    {
      id: 'vlm',
      type: 'VLM_API',
      position: { x: 0, y: 0 },
      data: {
        label: 'VLM',
        nodeType: 'VLM_API',
        provider: 'Qwen',
        model: null,
        crossVlm: false,
        promptPreset: 'default',
      },
    },
    {
      id: 'table',
      type: 'TableReview',
      position: { x: 0, y: 0 },
      data: { label: 'Table Review', nodeType: 'TableReview' },
    },
    { id: 'save', type: 'SaveResult', position: { x: 0, y: 0 }, data: { label: 'Save', nodeType: 'SaveResult' } },
  ]
  let edges: WorkflowGraph['edges'] = [
    typedEdge('files', 'mode'),
    typedEdge('mode', 'vlm'),
    typedEdge('vlm', 'table'),
    typedEdge('table', 'save'),
  ]
  if (m === 'AR' || m === 'AP') {
    nodes.splice(2, 0, {
      id: 'receipt',
      type: 'ReceiptStyle',
      position: { x: 0, y: 0 },
      data: {
        label: 'Receipt Style',
        nodeType: 'ReceiptStyle',
        receiptSignal: 'guess',
        tablePreset: 'default',
      },
    })
    edges = [
      typedEdge('files', 'mode'),
      typedEdge('mode', 'receipt'),
      typedEdge('receipt', 'vlm'),
      typedEdge('vlm', 'table'),
      typedEdge('table', 'save'),
    ]
  }
  return layoutGraphVertical({ schemaVersion: GRAPH_SCHEMA_VERSION, nodes, edges, processingMode: m })
}

/** Insert VLMDoubleCheck node when user toggles crossVlm on VLM_API. */
export function graphWithDoubleCheckEnabled(graph: WorkflowGraph): WorkflowGraph {
  if (graph.nodes.some(n => n.type === 'VLMDoubleCheck')) return graph
  const vlm = graph.nodes.find(n => n.id === 'vlm' || n.type === 'VLM_API')
  const table = graph.nodes.find(n => n.id === 'table' || n.type === 'TableReview')
  if (!vlm || !table) return graph
  const dcId = 'vlm_double_check'
  const nodes = [
    ...graph.nodes.map(n =>
      n.id === vlm.id ? { ...n, data: { ...n.data, crossVlm: false } } : n,
    ),
    {
      id: dcId,
      type: 'VLMDoubleCheck',
      position: { x: 0, y: 0 },
      data: {
        label: 'VLM Double Check',
        nodeType: 'VLMDoubleCheck',
        provider: 'Qwen',
        model: null,
        mergePolicy: 'cross_vlm',
        enabled: true,
      },
    },
  ]
  const edges = [
    ...graph.edges.filter(e => !(e.source === vlm.id && e.target === table.id)),
    typedEdge(vlm.id, dcId),
    typedEdge(dcId, table.id),
  ]
  return layoutGraphVertical({ ...graph, schemaVersion: GRAPH_SCHEMA_VERSION, nodes, edges })
}

/** Remove VLMDoubleCheck node and reconnect VLM_API to TableReview. */
export function graphWithDoubleCheckDisabled(graph: WorkflowGraph): WorkflowGraph {
  const dc = graph.nodes.find(n => n.type === 'VLMDoubleCheck')
  if (!dc) return graph
  const vlm = graph.nodes.find(n => n.type === 'VLM_API')
  const table = graph.nodes.find(n => n.id === 'table' || n.type === 'TableReview')
  if (!vlm || !table) return graph
  const nodes = graph.nodes.filter(n => n.id !== dc.id)
  const edges = [
    ...graph.edges.filter(e => e.source !== dc.id && e.target !== dc.id),
    typedEdge(vlm.id, table.id),
  ]
  return layoutGraphVertical({ ...graph, schemaVersion: GRAPH_SCHEMA_VERSION, nodes, edges })
}

export function apReceiptReady(graph: WorkflowGraph): boolean {
  const m = (graph.processingMode || '').toUpperCase()
  if (m !== 'AR' && m !== 'AP') return true
  const n = graph.nodes.find(x => x.type === 'ReceiptStyle')
  const rs = n?.data?.receiptSignal
  const tp = n?.data?.tablePreset
  return Boolean(rs && tp)
}
