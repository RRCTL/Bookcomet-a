import { describe, expect, it } from 'vitest'
import {
  defaultGraphForMode,
  edgesCompatible,
  graphWithDoubleCheckDisabled,
  graphWithDoubleCheckEnabled,
  isHorizontalLayout,
  layoutGraphVertical,
} from './defaultGraphs'

const ESTIMATED_HEIGHT: Record<string, number> = {
  Files: 180,
  ModeConfig: 90,
  ReceiptStyle: 210,
  VLM_API: 160,
  TableReview: 130,
  SaveResult: 90,
}

function assertVerticalStack(graph: ReturnType<typeof defaultGraphForMode>) {
  const ys = graph.nodes.map(n => n.position.y)
  const xs = graph.nodes.map(n => n.position.x)
  for (let i = 1; i < ys.length; i++) {
    expect(ys[i]).toBeGreaterThan(ys[i - 1]!)
  }
  expect(Math.max(...xs) - Math.min(...xs)).toBe(0)
}

describe('defaultGraphForMode', () => {
  it('lays out AP graph vertically with receipt node', () => {
    const graph = defaultGraphForMode('AP')
    expect(graph.nodes).toHaveLength(6)
    assertVerticalStack(graph)
    expect(graph.edges.map(e => e.id)).toEqual([
      'files-mode',
      'mode-receipt',
      'receipt-vlm',
      'vlm-table',
      'table-save',
    ])
  })

  it('lays out AR graph vertically with receipt node', () => {
    const graph = defaultGraphForMode('AR')
    expect(graph.nodes).toHaveLength(6)
    assertVerticalStack(graph)
  })

  it('lays out BANK graph vertically without receipt node', () => {
    const graph = defaultGraphForMode('BANK')
    expect(graph.nodes).toHaveLength(5)
    assertVerticalStack(graph)
    expect(graph.nodes.some(n => n.type === 'ReceiptStyle')).toBe(false)
    expect(graph.edges.map(e => e.id)).toEqual([
      'files-mode',
      'mode-vlm',
      'vlm-table',
      'table-save',
    ])
  })

  it('spaces tall nodes without overlap in AP graph', () => {
    const graph = defaultGraphForMode('AP')
    const order = ['files', 'mode', 'receipt', 'vlm', 'table', 'save']
    for (let i = 1; i < order.length; i++) {
      const prev = graph.nodes.find(n => n.id === order[i - 1])!
      const curr = graph.nodes.find(n => n.id === order[i])!
      const prevBottom = prev.position.y + (ESTIMATED_HEIGHT[prev.type ?? ''] ?? 110)
      expect(curr.position.y).toBeGreaterThanOrEqual(prevBottom + 40)
    }
  })
})

describe('layoutGraphVertical', () => {
  it('reorders horizontal saved positions to vertical', () => {
    const graph = defaultGraphForMode('BANK')
    const horizontal = {
      ...graph,
      nodes: graph.nodes.map((n, i) => ({ ...n, position: { x: i * 300, y: 120 } })),
    }
    expect(isHorizontalLayout(horizontal)).toBe(true)
    const vertical = layoutGraphVertical(horizontal)
    assertVerticalStack(vertical)
  })

  it('places VLM proposal branches side by side', () => {
    const graph = defaultGraphForMode('AP')
    const proposalNodes = ['proposal_a', 'proposal_b', 'proposal_c'].map((id, index) => ({
      id,
      type: 'VLMProposer',
      position: { x: 0, y: 0 },
      data: { label: `Proposal ${index + 1}`, nodeType: 'VLMProposer' },
    }))
    const branch = layoutGraphVertical({
      ...graph,
      nodes: [
        ...graph.nodes.filter(node => node.type !== 'VLM_API'),
        ...proposalNodes,
        { id: 'proposal_pool', type: 'ProposalPoolJoin', position: { x: 0, y: 0 }, data: {} },
        { id: 'vlm_judge', type: 'VLMJudge', position: { x: 0, y: 0 }, data: {} },
        { id: 'vote', type: 'VoteSelector', position: { x: 0, y: 0 }, data: {} },
        { id: 'merge', type: 'MergeResult', position: { x: 0, y: 0 }, data: {} },
      ],
      edges: [
        ...graph.edges.filter(edge => edge.source !== 'vlm' && edge.target !== 'vlm'),
        { id: 'receipt-proposal-a', source: 'receipt', target: 'proposal_a' },
        { id: 'receipt-proposal-b', source: 'receipt', target: 'proposal_b' },
        { id: 'receipt-proposal-c', source: 'receipt', target: 'proposal_c' },
        { id: 'proposal-a-pool', source: 'proposal_a', target: 'proposal_pool' },
        { id: 'proposal-b-pool', source: 'proposal_b', target: 'proposal_pool' },
        { id: 'proposal-c-pool', source: 'proposal_c', target: 'proposal_pool' },
        { id: 'pool-judge', source: 'proposal_pool', target: 'vlm_judge' },
        { id: 'judge-vote', source: 'vlm_judge', target: 'vote' },
        { id: 'vote-merge', source: 'vote', target: 'merge' },
        { id: 'merge-table', source: 'merge', target: 'table' },
      ],
    })
    const proposals = proposalNodes.map(node => branch.nodes.find(n => n.id === node.id)!)
    expect(new Set(proposals.map(node => node.position.x)).size).toBe(3)
    expect(new Set(proposals.map(node => node.position.y)).size).toBe(1)
    expect(branch.nodes.find(node => node.id === 'proposal_pool')!.position.y).toBeGreaterThan(proposals[0]!.position.y)
  })
})

describe('graph schema v2 helpers', () => {
  it('inserts VLMDoubleCheck when enabled', () => {
    const graph = defaultGraphForMode('AP')
    const next = graphWithDoubleCheckEnabled(graph)
    expect(next.nodes.some(n => n.type === 'VLMDoubleCheck')).toBe(true)
    expect(next.edges.some(e => e.source === 'vlm_double_check' && e.target === 'table')).toBe(true)
  })

  it('removes VLMDoubleCheck when disabled', () => {
    const graph = graphWithDoubleCheckEnabled(defaultGraphForMode('AP'))
    const next = graphWithDoubleCheckDisabled(graph)
    expect(next.nodes.some(n => n.type === 'VLMDoubleCheck')).toBe(false)
    expect(next.edges.some(e => e.source === 'vlm' && e.target === 'table')).toBe(true)
  })

  it('rejects incompatible edges to Files', () => {
    expect(edgesCompatible('SaveResult', 'Files', 'out', 'in')).toBe(false)
    expect(edgesCompatible('VLM_API', 'TableReview', 'out', 'in')).toBe(true)
  })

  it('allows proposal pool plugin edges', () => {
    expect(edgesCompatible('VLMProposer', 'ProposalPoolJoin', 'out', 'in')).toBe(true)
    expect(edgesCompatible('ProposalPoolJoin', 'VLMJudge', 'out', 'in')).toBe(true)
    expect(edgesCompatible('VLMJudge', 'VoteSelector', 'out', 'in')).toBe(true)
    expect(edgesCompatible('VoteSelector', 'ManagerReview', 'out', 'in')).toBe(true)
    expect(edgesCompatible('VoteSelector', 'MergeResult', 'out', 'in')).toBe(true)
    expect(edgesCompatible('ManagerReview', 'MergeResult', 'out', 'in')).toBe(true)
    expect(edgesCompatible('MergeResult', 'TableReview', 'out', 'in')).toBe(true)
  })
})
