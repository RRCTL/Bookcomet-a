import { useCallback, useEffect, useRef, type MouseEvent as ReactMouseEvent } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  applyNodeChanges,
  applyEdgeChanges,
  useReactFlow,
  ReactFlowProvider,
  ConnectionLineType,
  MarkerType,
  type Node,
  type Edge,
  type OnNodesChange,
  type OnEdgesChange,
  type Connection,
  addEdge,
  Panel,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { edgesCompatible } from './defaultGraphs'
import { workflowNodeTypes, type WorkflowNodeData } from './nodes/workflowNodeTypes'
import type { WorkflowGraph } from './workflowApi'
import { CANVAS_DOT_COLOR, type WorkflowTheme } from './workflowTheme'

type Props = {
  graph: WorkflowGraph
  nodes: Node<WorkflowNodeData>[]
  theme: WorkflowTheme
  layoutKey?: string
  onGraphChange: (graph: WorkflowGraph) => void
  onNodesUpdate: (nodes: Node<WorkflowNodeData>[]) => void
  onNodeClick?: (node: Node<WorkflowNodeData>, event: ReactMouseEvent) => void
}

function graphFromNodes(
  graph: WorkflowGraph,
  next: Node<WorkflowNodeData>[],
): WorkflowGraph {
  const nodeIds = new Set(next.map(n => n.id))
  return {
    ...graph,
    nodes: next.map(n => ({
      id: n.id,
      type: n.type ?? 'Files',
      position: n.position,
      data: n.data as Record<string, unknown>,
    })),
    edges: graph.edges.filter(e => nodeIds.has(e.source) && nodeIds.has(e.target)),
  }
}

function shouldPersistNodeChanges(
  changes: Parameters<OnNodesChange<Node<WorkflowNodeData>>>[0],
): boolean {
  return changes.some(ch => {
    if (ch.type === 'dimensions' || ch.type === 'select') return false
    if (ch.type === 'position') return 'dragging' in ch && ch.dragging === false
    return true
  })
}

function WorkflowCanvasInner({ graph, nodes, theme, layoutKey, onGraphChange, onNodesUpdate, onNodeClick }: Props) {
  const { fitView } = useReactFlow()
  const didFitRef = useRef(false)
  const lastNodeCountRef = useRef(0)
  const lastLayoutKeyRef = useRef<string | undefined>(undefined)

  useEffect(() => {
    if (layoutKey !== lastLayoutKeyRef.current) {
      lastLayoutKeyRef.current = layoutKey
      didFitRef.current = false
      lastNodeCountRef.current = 0
    }
  }, [layoutKey])

  useEffect(() => {
    if (nodes.length === 0) return
    const nodeWasAdded = nodes.length > lastNodeCountRef.current
    lastNodeCountRef.current = nodes.length
    if (didFitRef.current && !nodeWasAdded) return
    didFitRef.current = true
    void fitView({ padding: 0.15, duration: 200 })
  }, [nodes.length, layoutKey, fitView])

  const edges: Edge[] = graph.edges.map(e => ({
    id: e.id,
    source: e.source,
    target: e.target,
    type: 'straight',
  }))

  const onNodesChange: OnNodesChange<Node<WorkflowNodeData>> = useCallback(
    changes => {
      const next = applyNodeChanges(changes, nodes)
      onNodesUpdate(next)
      if (shouldPersistNodeChanges(changes)) {
        onGraphChange(graphFromNodes(graph, next))
      }
    },
    [nodes, graph, onGraphChange, onNodesUpdate],
  )

  const onEdgesChange: OnEdgesChange = useCallback(
    changes => {
      const nextEdges = applyEdgeChanges(changes, edges)
      onGraphChange({
        ...graph,
        edges: nextEdges.map(e => ({
          id: e.id,
          source: e.source,
          target: e.target,
          sourceHandle: e.sourceHandle ?? undefined,
          targetHandle: e.targetHandle ?? undefined,
        })),
      })
    },
    [edges, graph, onGraphChange],
  )

  const onConnect = useCallback(
    (conn: Connection) => {
      const src = nodes.find(n => n.id === conn.source)
      const tgt = nodes.find(n => n.id === conn.target)
      if (src?.type && tgt?.type) {
        if (
          !edgesCompatible(
            src.type,
            tgt.type,
            conn.sourceHandle,
            conn.targetHandle,
          )
        ) {
          return
        }
      }
      const nextEdges = addEdge({ ...conn, type: 'straight' }, edges)
      onGraphChange({
        ...graph,
        edges: nextEdges.map(e => ({
          id: e.id,
          source: e.source,
          target: e.target,
          sourceHandle: e.sourceHandle ?? undefined,
          targetHandle: e.targetHandle ?? undefined,
        })),
      })
    },
    [edges, graph, nodes, onGraphChange],
  )

  return (
    <div className="h-full w-full min-h-[280px]">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={(event, node) => onNodeClick?.(node, event)}
        nodeTypes={workflowNodeTypes}
        noDragClassName="nodrag"
        noWheelClassName="nowheel"
        defaultEdgeOptions={{
          type: 'straight',
          animated: false,
          markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12 },
        }}
        connectionLineType={ConnectionLineType.Straight}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} size={1} color={CANVAS_DOT_COLOR[theme]} />
        <Panel position="bottom-right">
          <div className="react-flow__controls-stack">
            <MiniMap
              pannable
              zoomable
              style={{ marginBottom: 4, width: 120, height: 80 }}
            />
            <Controls showInteractive={false} />
          </div>
        </Panel>
      </ReactFlow>
    </div>
  )
}

export function WorkflowCanvas(props: Props) {
  return (
    <ReactFlowProvider>
      <WorkflowCanvasInner {...props} />
    </ReactFlowProvider>
  )
}
