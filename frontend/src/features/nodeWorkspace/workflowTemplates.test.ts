import { describe, expect, it } from 'vitest'
import type { WorkflowGraph, WorkflowTemplate } from './workflowApi'
import {
  resolvePaletteTemplateSelection,
  sortPaletteTemplates,
  templateMatchingGraph,
} from './workflowTemplates'

function tpl(
  id: string,
  name: string,
  is_default: boolean,
  graph_json: WorkflowGraph,
): WorkflowTemplate {
  return { id, name, processing_mode: 'AP', is_default, graph_json }
}

const voteGraph: WorkflowGraph = {
  schemaVersion: 2,
  processingMode: 'AP',
  nodes: [{ id: 'vote', type: 'VLMProposer', position: { x: 0, y: 0 }, data: {} }],
  edges: [],
}

const defaultGraph: WorkflowGraph = {
  schemaVersion: 2,
  processingMode: 'AP',
  nodes: [{ id: 'vlm', type: 'VLM_API', position: { x: 0, y: 0 }, data: {} }],
  edges: [],
}

describe('sortPaletteTemplates', () => {
  it('puts default template first, then sorts by name', () => {
    const sorted = sortPaletteTemplates([
      tpl('a', '3 VLM Vote', false, voteGraph),
      tpl('b', 'AP Double Check', true, defaultGraph),
      tpl('c', 'Manager Review', false, defaultGraph),
    ])
    expect(sorted.map(t => t.name)).toEqual(['AP Double Check', '3 VLM Vote', 'Manager Review'])
  })
})

describe('resolvePaletteTemplateSelection', () => {
  const templates = sortPaletteTemplates([
    tpl('vote', '3 VLM Vote', false, voteGraph),
    tpl('default', 'AP Double Check', true, defaultGraph),
  ])

  it('prefers graph match over company default', () => {
    const picked = resolvePaletteTemplateSelection(templates, '', voteGraph)
    expect(picked?.name).toBe('3 VLM Vote')
  })

  it('falls back to default when graph is custom', () => {
    const customGraph: WorkflowGraph = {
      schemaVersion: 2,
      processingMode: 'AP',
      nodes: [{ id: 'x', type: 'Files', position: { x: 0, y: 0 }, data: {} }],
      edges: [],
    }
    const picked = resolvePaletteTemplateSelection(templates, '', customGraph)
    expect(picked?.name).toBe('AP Double Check')
  })

  it('keeps explicit selected id when valid', () => {
    const picked = resolvePaletteTemplateSelection(templates, 'vote', defaultGraph)
    expect(picked?.name).toBe('3 VLM Vote')
  })
})

describe('templateMatchingGraph', () => {
  it('matches normalized layout graphs', () => {
    const templates = [tpl('vote', '3 VLM Vote', false, voteGraph)]
    expect(templateMatchingGraph(templates, voteGraph)?.id).toBe('vote')
  })
})
