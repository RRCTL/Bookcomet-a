import type { ReactNode } from 'react'
import type { Node, NodeProps } from '@xyflow/react'
import type { WorkflowGraph } from '../workflowApi'
import { graphWithDoubleCheckDisabled, graphWithDoubleCheckEnabled } from '../defaultGraphs'
import { Handle, Position } from '@xyflow/react'
import {
  AP_RECEIPT_OPTIONS_ORDER,
  AP_TABLE_OPTIONS_ORDER,
  type ApVlmReceiptSignal,
  type ApVlmTablePreset,
} from '../../workspace/apComposerOptions'
import type { ARAPTransaction } from '../../../components/ARAPReview'
import type { BankTransaction } from '../../../components/BankStatementReview'
import type { WorkflowRun, WorkflowRunFile } from '../workflowApi'
import { formatFilePageCount } from '../filePageLabel'

export type WorkflowNodeData = {
  label?: string
  nodeType?: string
  processingMode?: string
  receiptSignal?: ApVlmReceiptSignal
  tablePreset?: ApVlmTablePreset
  provider?: string
  providerOptions?: string[]
  model?: string | null
  crossVlm?: boolean
  promptPreset?: string
  mergePolicy?: string
  enabled?: boolean
  condition?: string
  switchOn?: string
  policy?: string
  skillKey?: string | null
  proposalName?: string
  retryOnFail?: boolean
  endpointEnvKey?: string | null
  method?: string
  dangerAcknowledged?: boolean
  description?: string
  files?: WorkflowRunFile[]
  queueFiles?: WorkflowRunFile[]
  runStatus?: string
  onUpload?: () => void
  onGraphDataChange?: (nodeId: string, patch: Record<string, unknown>) => void
  onGraphStructureChange?: (graph: WorkflowGraph) => void
  currentGraph?: WorkflowGraph
  tablePayload?: Record<string, unknown>
  onTableChange?: (payload: Record<string, unknown>) => void
  onOpenReview?: () => void
  onApprove?: () => void
  onSkipCoa?: () => void
  coaBusy?: boolean
  nodeState?: { status?: string; detail?: unknown }
}

const DEFAULT_PROVIDER_OPTIONS = ['Qwen']

function VlmProviderSelect({
  id,
  data,
  fallback = 'Qwen',
}: {
  id: string
  data: WorkflowNodeData
  fallback?: string
}) {
  const options = data.providerOptions?.length ? data.providerOptions : DEFAULT_PROVIDER_OPTIONS
  const defaultProvider = options[0] ?? fallback
  return (
    <label className="mb-2 block">
      Provider
      <select
        className="ow-input nodrag nowheel mt-1"
        value={data.provider ?? defaultProvider}
        onChange={e => data.onGraphDataChange?.(id, { provider: e.target.value })}
      >
        {options.map(p => (
          <option key={p} value={p}>
            {p}
          </option>
        ))}
      </select>
    </label>
  )
}

function nodeStatusLabel(status?: string, detail?: unknown): string {
  if (!status) return ''
  if (status === 'skipped' && detail && typeof detail === 'object' && 'reason' in detail) {
    const reason = String((detail as { reason?: string }).reason ?? '')
    if (reason === 'no_files_on_branch') return 'Skipped: no files on branch'
  }
  const detailText =
    detail && typeof detail === 'object'
      ? Object.entries(detail as Record<string, unknown>)
          .slice(0, 2)
          .map(([k, v]) => `${k}: ${v}`)
          .join(', ')
      : detail != null
        ? String(detail)
        : ''
  return detailText ? `${status} (${detailText})` : status
}

function NodeShell({
  title,
  children,
  nodeState,
  sourceHandles = ['out'],
  targetHandles = ['in'],
}: {
  title: string
  children: ReactNode
  nodeState?: { status?: string; detail?: unknown }
  sourceHandles?: string[]
  targetHandles?: string[]
}) {
  const footer = nodeStatusLabel(nodeState?.status, nodeState?.detail)
  return (
    <div className="wf-node min-w-[200px] rounded-xl border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-850">
      <div className="rounded-t-xl border-b border-gray-200 bg-gray-50 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-gray-700 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200">
        {title}
      </div>
      <div className="p-3 text-sm">{children}</div>
      {footer ? (
        <div className="border-t border-gray-200 px-3 py-1.5 text-[10px] uppercase tracking-wide text-gray-500 dark:border-gray-700 dark:text-gray-400">
          {footer}
        </div>
      ) : null}
      {targetHandles.map(h => (
        <Handle
          key={`t-${h}`}
          type="target"
          position={Position.Top}
          id={h}
          className="wf-handle wf-handle--target"
        />
      ))}
      {sourceHandles.map((h, i) => (
        <Handle
          key={`s-${h}`}
          type="source"
          position={Position.Bottom}
          id={h}
          style={sourceHandles.length > 1 ? { left: `${((i + 1) / (sourceHandles.length + 1)) * 100}%` } : undefined}
          className="wf-handle wf-handle--source"
        />
      ))}
    </div>
  )
}

function fileChipClass(status: string, gate?: string | null): string {
  if (status === 'failed' || status === 'error') return 'ow-chip ow-chip--fail'
  if (gate === 'warning' || gate === 'warn') return 'ow-chip ow-chip--warn'
  if (status === 'completed' || status === 'ok') return 'ow-chip ow-chip--ok'
  return 'ow-chip bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'
}

function canvasQueueFiles(data: WorkflowNodeData): WorkflowRunFile[] {
  return data.queueFiles ?? data.files ?? []
}

export function FilesNode({ data }: NodeProps<Node<WorkflowNodeData>>) {
  const files = canvasQueueFiles(data)
  return (
    <NodeShell title="Files" nodeState={data.nodeState}>
      <button type="button" className="btn-primary mb-2 w-full" onClick={data.onUpload}>
        Add files
      </button>
      <ul className="max-h-[120px] list-disc overflow-auto pl-4">
        {files.length === 0 && <li className="text-gray-500">No files yet</li>}
        {files.map(f => {
          const pageLabel = formatFilePageCount(f.page_count)
          return (
          <li key={f.task_file_id} className="mb-1">
            {f.original_filename ?? f.task_file_id}
            {pageLabel ? ` (${pageLabel})` : ''}
          </li>
          )
        })}
      </ul>
    </NodeShell>
  )
}

export function ModeConfigNode({ data }: NodeProps<Node<WorkflowNodeData>>) {
  return (
    <NodeShell title="Mode" nodeState={data.nodeState}>
      <div>{data.processingMode ?? data.label}</div>
    </NodeShell>
  )
}

export function ReceiptStyleNode({ id, data }: NodeProps<Node<WorkflowNodeData>>) {
  return (
    <NodeShell title="Receipt style" nodeState={data.nodeState}>
      <label className="mb-2 block">
        Layout
        <select
          className="ow-input nodrag nowheel mt-1"
          value={data.receiptSignal ?? ''}
          onChange={e => {
            data.onGraphDataChange?.(id, { receiptSignal: e.target.value as ApVlmReceiptSignal })
          }}
        >
          <option value="">Select...</option>
          {AP_RECEIPT_OPTIONS_ORDER.map(o => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        Table
        <select
          className="ow-input nodrag nowheel mt-1"
          value={data.tablePreset ?? ''}
          onChange={e => {
            data.onGraphDataChange?.(id, { tablePreset: e.target.value as ApVlmTablePreset })
          }}
        >
          <option value="">Select...</option>
          {AP_TABLE_OPTIONS_ORDER.map(o => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </label>
    </NodeShell>
  )
}

export function VlmApiNode({ id, data }: NodeProps<Node<WorkflowNodeData>>) {
  const files = canvasQueueFiles(data)
  return (
    <NodeShell title="VLM API" nodeState={data.nodeState}>
      <VlmProviderSelect id={id} data={data} fallback="Qwen" />
      <label className="mb-2 flex items-center gap-2">
        <input
          type="checkbox"
          className="nodrag"
          checked={Boolean(data.crossVlm) || Boolean(data.currentGraph?.nodes.some(n => n.type === 'VLMDoubleCheck'))}
          onChange={e => {
            const checked = e.target.checked
            if (data.onGraphStructureChange && data.currentGraph) {
              const next = checked
                ? graphWithDoubleCheckEnabled(data.currentGraph)
                : graphWithDoubleCheckDisabled(data.currentGraph)
              data.onGraphStructureChange(next)
              return
            }
            data.onGraphDataChange?.(id, { crossVlm: checked })
          }}
        />
        Enable double check node
      </label>
      <div className="mb-2">
        {files.map(f => (
          <span key={f.task_file_id} className={fileChipClass(f.file_status, f.gate_result)}>
            {(f.original_filename ?? 'file').slice(0, 12)}
            {f.gate_result ? ` ${f.gate_result}` : ''}
          </span>
        ))}
        {files.length === 0 ? <span className="text-gray-500">Awaiting files</span> : null}
      </div>
    </NodeShell>
  )
}

export function VlmDoubleCheckNode({ id, data }: NodeProps<Node<WorkflowNodeData>>) {
  return (
    <NodeShell title="VLM Double Check" nodeState={data.nodeState}>
      <VlmProviderSelect id={id} data={data} />
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          className="nodrag"
          checked={data.enabled !== false}
          onChange={e => data.onGraphDataChange?.(id, { enabled: e.target.checked })}
        />
        Enabled
      </label>
    </NodeShell>
  )
}

export function IfNode({ id, data }: NodeProps<Node<WorkflowNodeData>>) {
  return (
    <NodeShell title="If" nodeState={data.nodeState} sourceHandles={['true', 'false']}>
      <div className="text-gray-500">Condition: {data.condition ?? 'needs_double_check'}</div>
    </NodeShell>
  )
}

export function SwitchNode({ id, data }: NodeProps<Node<WorkflowNodeData>>) {
  return (
    <NodeShell title="Switch" nodeState={data.nodeState} sourceHandles={['out0', 'out1', 'default']}>
      <div className="text-gray-500">Switch on: {data.switchOn ?? 'file_status'}</div>
    </NodeShell>
  )
}

export function TableReviewNode({ data }: NodeProps<Node<WorkflowNodeData>>) {
  const mode = (data.processingMode ?? 'AR').toUpperCase()
  const payload = data.tablePayload ?? {}
  const arap = (payload.arapTransactions as ARAPTransaction[]) ?? []
  const bank = (payload.bankTransactions as BankTransaction[]) ?? []
  const count = mode === 'BANK' ? bank.length : arap.length
  const status =
    data.runStatus === 'awaiting_review'
      ? 'Awaiting review'
      : data.runStatus === 'completed'
        ? 'Completed'
        : count > 0
          ? `${count} rows ready`
          : 'No rows yet'

  return (
    <NodeShell title="Table review" nodeState={data.nodeState}>
      <p className="mb-2">
        {count} rows · {status}
      </p>
      <button type="button" className="btn-primary w-full" onClick={data.onOpenReview}>
        Open review
      </button>
    </NodeShell>
  )
}

export function CoaDeployNode({ data }: NodeProps<Node<WorkflowNodeData>>) {
  return (
    <NodeShell title="CoA deploy" nodeState={data.nodeState}>
      {data.coaBusy ? <p>Deploying codes…</p> : <p>Runs after Approve in review panel</p>}
    </NodeShell>
  )
}

export function SaveResultNode({ data }: NodeProps<Node<WorkflowNodeData>>) {
  const done = data.runStatus === 'completed'
  return (
    <NodeShell title="Save" nodeState={data.nodeState}>
      <p className={`m-0 ${done ? 'text-green-600 dark:text-green-400' : 'text-gray-500'}`}>
        {done ? 'Pool 2 saved' : `Status: ${data.runStatus ?? 'draft'}`}
      </p>
    </NodeShell>
  )
}

const PLUGIN_SOURCE_HANDLES: Record<string, string[]> = {
  ConditionRouter: ['pass', 'retry', 'fail'],
}

function pluginSummary(data: WorkflowNodeData): string[] {
  const rows: string[] = []
  if (data.provider) rows.push(`Provider: ${data.provider}`)
  if (data.policy) rows.push(`Policy: ${data.policy}`)
  if (data.skillKey) rows.push(`Skill: ${data.skillKey}`)
  if (data.proposalName) rows.push(`Proposal: ${data.proposalName}`)
  if (data.endpointEnvKey) rows.push(`Endpoint: ${data.endpointEnvKey}`)
  if (data.retryOnFail != null) rows.push(`Retry on fail: ${data.retryOnFail ? 'yes' : 'no'}`)
  return rows
}

function SkillSummary({ data }: { data: WorkflowNodeData }) {
  return data.skillKey ? (
    <p className="mb-1 text-xs text-gray-600 dark:text-gray-300">Skill: {data.skillKey}</p>
  ) : null
}

export function VlmProposerNode({ id, data }: NodeProps<Node<WorkflowNodeData>>) {
  return (
    <NodeShell title={data.label ?? 'VLM Proposer'} nodeState={data.nodeState}>
      <VlmProviderSelect id={id} data={data} fallback="Qwen" />
      <p className="mb-1 text-xs text-gray-600 dark:text-gray-300">
        Proposal: {data.proposalName ?? 'Proposal'}
      </p>
      <SkillSummary data={data} />
    </NodeShell>
  )
}

export function VlmJudgeNode({ id, data }: NodeProps<Node<WorkflowNodeData>>) {
  return (
    <NodeShell title={data.label ?? 'VLM Judge'} nodeState={data.nodeState}>
      <VlmProviderSelect id={id} data={data} />
      <SkillSummary data={data} />
    </NodeShell>
  )
}

export function ManagerReviewNode({ id, data }: NodeProps<Node<WorkflowNodeData>>) {
  return (
    <NodeShell title={data.label ?? 'Manager Review'} nodeState={data.nodeState}>
      <VlmProviderSelect id={id} data={data} />
      <SkillSummary data={data} />
      {data.retryOnFail != null ? (
        <p className="text-xs text-gray-600 dark:text-gray-300">
          Retry on fail: {data.retryOnFail ? 'yes' : 'no'}
        </p>
      ) : null}
    </NodeShell>
  )
}

export function GenericPluginNode({ data }: NodeProps<Node<WorkflowNodeData>>) {
  const nodeType = data.nodeType ?? 'Plugin'
  const handles = PLUGIN_SOURCE_HANDLES[nodeType] ?? ['out']
  const rows = pluginSummary(data)
  return (
    <NodeShell title={data.label ?? nodeType} nodeState={data.nodeState} sourceHandles={handles}>
      {data.description ? (
        <p className="mb-2 text-xs text-gray-500 dark:text-gray-400">{data.description}</p>
      ) : null}
      {rows.length ? (
        <ul className="space-y-1 text-xs text-gray-600 dark:text-gray-300">
          {rows.slice(0, 4).map(row => (
            <li key={row}>{row}</li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-gray-500">Configure this workflow plugin from the node panel.</p>
      )}
    </NodeShell>
  )
}

export const workflowNodeTypes = {
  Files: FilesNode,
  ModeConfig: ModeConfigNode,
  ReceiptStyle: ReceiptStyleNode,
  VLM_API: VlmApiNode,
  VLMDoubleCheck: VlmDoubleCheckNode,
  If: IfNode,
  Switch: SwitchNode,
  VLMProposer: VlmProposerNode,
  ProposalPoolJoin: GenericPluginNode,
  VLMJudge: VlmJudgeNode,
  VoteSelector: GenericPluginNode,
  ManagerReview: ManagerReviewNode,
  ConditionRouter: GenericPluginNode,
  ExternalApiCall: GenericPluginNode,
  MergeResult: GenericPluginNode,
  TableReview: TableReviewNode,
  CoADeploy: CoaDeployNode,
  SaveResult: SaveResultNode,
}

function nodeStateFromRun(run: WorkflowRun, nodeId: string): { status?: string; detail?: unknown } | undefined {
  const states = run.node_states_json
  if (!states || typeof states !== 'object' || Array.isArray(states)) return undefined
  const raw = (states as Record<string, unknown>)[nodeId]
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return undefined
  const entry = raw as { status?: string; detail?: unknown }
  if (!entry.status) return undefined
  return entry
}

export function enrichNodesFromRun(
  graphNodes: WorkflowGraph['nodes'],
  run: WorkflowRun,
  handlers: Partial<WorkflowNodeData>,
): Node<WorkflowNodeData>[] {
  return graphNodes.map(n => ({
    id: n.id,
    type: n.type,
    position: n.position,
    data: {
      ...n.data,
      nodeType: n.type,
      processingMode: run.processing_mode,
      files: run.files,
      queueFiles: handlers.queueFiles,
      runStatus: run.run_status,
      nodeState: nodeStateFromRun(run, n.id),
      ...handlers,
      ...(n.type === 'TableReview' ? { tablePayload: handlers.tablePayload } : {}),
    },
  }))
}
