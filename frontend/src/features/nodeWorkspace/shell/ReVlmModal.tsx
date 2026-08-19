import { useEffect, useMemo, useState } from 'react'

import {

  RE_VLM_EXPECTED_COUNT_MAX,

  RE_VLM_NOTE_MAX_LEN,

  RE_VLM_REASON_CHIPS,

  suggestRescanReasonsForFiles,

  type ReVlmConfirmPayload,

  type ReVlmReasonId,

  type ReVlmWorkflowSettings,

} from '../reVlmReasonChips'

import type { WorkflowGraph, WorkflowRunFile, WorkflowTemplate } from '../workflowApi'

import {

  AP_RECEIPT_OPTIONS_ORDER,

  AP_TABLE_OPTIONS_ORDER,

  type ApVlmReceiptSignal,

  type ApVlmTablePreset,

} from '../../workspace/apComposerOptions'

import { workflowSettingsFromGraph } from './processingWorkflowHeader'



const RECEIPT_SIGNAL_LABELS: Record<ApVlmReceiptSignal, string> = {

  guess: 'Guess (auto)',

  single_per_page: 'Single receipt / page',

  multi_per_page: 'Multi receipt / page',

  single_span_pages: 'Receipt spans pages',

}



const TABLE_PRESET_LABELS: Record<ApVlmTablePreset, string> = {

  default: 'Default columns',

  ap_table: 'AP table',

}



export type ReVlmWorkflowContext = {

  graph: WorkflowGraph

  templates: WorkflowTemplate[]

  processingMode: string

}



type Props = {

  files: WorkflowRunFile[]

  initialSelectedFileIds: string[]

  busy: boolean

  workflowContext?: ReVlmWorkflowContext

  onConfirm: (payload: ReVlmConfirmPayload) => void

  onCancel: () => void

}



export function ReVlmModal({

  files,

  initialSelectedFileIds,

  busy,

  workflowContext,

  onConfirm,

  onCancel,

}: Props) {

  const [selectedFileIds, setSelectedFileIds] = useState<string[]>(initialSelectedFileIds)

  const [selectedReasons, setSelectedReasons] = useState<ReVlmReasonId[]>(() =>

    suggestRescanReasonsForFiles(files, initialSelectedFileIds),

  )

  const [note, setNote] = useState('')

  const [expectedCount, setExpectedCount] = useState('')

  const [workflowDraft, setWorkflowDraft] = useState<ReVlmWorkflowSettings | null>(null)



  const modeTemplates = useMemo(

    () =>

      workflowContext

        ? workflowContext.templates.filter(t => t.processing_mode === workflowContext.processingMode)

        : [],

    [workflowContext],

  )

  const showReceiptOptions =

    workflowContext != null &&

    (workflowContext.processingMode === 'AP' || workflowContext.processingMode === 'AR') &&

    workflowContext.graph.nodes.some(n => n.type === 'ReceiptStyle')

  const tablePresetOptions =

    workflowContext?.processingMode === 'AR'

      ? AP_TABLE_OPTIONS_ORDER.filter(opt => opt === 'default')

      : AP_TABLE_OPTIONS_ORDER



  useEffect(() => {

    setSelectedFileIds(initialSelectedFileIds)

    setSelectedReasons(suggestRescanReasonsForFiles(files, initialSelectedFileIds))

    setNote('')

    if (workflowContext) {

      setWorkflowDraft(

        workflowSettingsFromGraph(

          workflowContext.graph,

          workflowContext.templates,

          workflowContext.processingMode,

        ),

      )

    } else {

      setWorkflowDraft(null)

    }

  }, [files, initialSelectedFileIds, workflowContext])



  const suggested = useMemo(

    () => suggestRescanReasonsForFiles(files, selectedFileIds),

    [files, selectedFileIds],

  )



  const toggleFile = (taskFileId: string, checked: boolean) => {

    setSelectedFileIds(prev => {

      const next = checked ? [...prev, taskFileId] : prev.filter(id => id !== taskFileId)

      setSelectedReasons(suggestRescanReasonsForFiles(files, next))

      return next

    })

  }



  const toggleReason = (id: ReVlmReasonId) => {

    setSelectedReasons(prev =>

      prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id],

    )

  }



  const patchWorkflow = (patch: Partial<ReVlmWorkflowSettings>) => {

    setWorkflowDraft(prev => (prev ? { ...prev, ...patch } : prev))

  }



  return (

    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">

      <div className="ow-card max-h-[90vh] w-full max-w-lg overflow-y-auto p-6">

        <h3 className="mb-3 text-lg font-semibold">Re-VLM files</h3>



        {workflowContext && workflowDraft ? (

          <div className="mb-4 rounded-lg border border-gray-200 p-3 dark:border-gray-700">

            <p className="mb-2 text-sm font-medium">Workflow settings</p>

            <p className="mb-3 text-xs text-gray-500">

              Current workflow selection. Change here before re-running VLM.

            </p>

            <div className="grid gap-2 sm:grid-cols-2">

              <label className="block text-xs">

                <span className="mb-1 block text-gray-500">Template</span>

                <select

                  className="ow-input w-full text-xs"

                  value={workflowDraft.templateId}

                  disabled={modeTemplates.length === 0 || busy}

                  onChange={e => patchWorkflow({ templateId: e.target.value })}

                >

                  {modeTemplates.length === 0 && <option value="">Default</option>}

                  {modeTemplates.map(t => (

                    <option key={t.id} value={t.id}>

                      {t.name}

                    </option>

                  ))}

                </select>

              </label>

              <label className="block text-xs">

                <span className="mb-1 block text-gray-500">Provider</span>

                <select

                  className="ow-input w-full text-xs"

                  value={workflowDraft.provider}

                  disabled={busy}

                  onChange={e => patchWorkflow({ provider: e.target.value })}

                >

                  <option value="Qwen">Qwen</option>

                  <option value="DeepSeek">DeepSeek</option>

                </select>

              </label>

              {showReceiptOptions ? (

                <>

                  <label className="block text-xs">

                    <span className="mb-1 block text-gray-500">Receipt layout</span>

                    <select

                      className="ow-input w-full text-xs"

                      value={workflowDraft.receiptSignal ?? 'guess'}

                      disabled={busy}

                      onChange={e =>

                        patchWorkflow({ receiptSignal: e.target.value as ApVlmReceiptSignal })

                      }

                    >

                      {AP_RECEIPT_OPTIONS_ORDER.map(opt => (

                        <option key={opt} value={opt}>

                          {RECEIPT_SIGNAL_LABELS[opt]}

                        </option>

                      ))}

                    </select>

                  </label>

                  <label className="block text-xs">

                    <span className="mb-1 block text-gray-500">Table style</span>

                    <select

                      className="ow-input w-full text-xs"

                      value={workflowDraft.tablePreset ?? 'default'}

                      disabled={busy}

                      onChange={e =>

                        patchWorkflow({ tablePreset: e.target.value as ApVlmTablePreset })

                      }

                    >

                      {tablePresetOptions.map(opt => (

                        <option key={opt} value={opt}>

                          {TABLE_PRESET_LABELS[opt]}

                        </option>

                      ))}

                    </select>

                  </label>

                </>

              ) : null}

            </div>

          </div>

        ) : null}



        <div className="mb-4 space-y-2">

          {files.map(f => (

            <label key={f.task_file_id} className="flex items-center gap-2 text-sm">

              <input

                type="checkbox"

                checked={selectedFileIds.includes(f.task_file_id)}

                onChange={e => toggleFile(f.task_file_id, e.target.checked)}

              />

              {f.original_filename ?? f.task_file_id} ({f.file_status}

              {f.gate_result ? ` · gate: ${f.gate_result}` : ''})

            </label>

          ))}

        </div>



        <div className="mb-4">

          <p className="mb-2 text-sm font-medium">What should VLM focus on? (optional)</p>

          {suggested.length > 0 ? (

            <p className="mb-2 text-xs text-gray-500">

              Suggested from file status; adjust as needed.

            </p>

          ) : null}

          <div className="flex flex-wrap gap-2">

            {RE_VLM_REASON_CHIPS.map(chip => {

              const active = selectedReasons.includes(chip.id)

              return (

                <button

                  key={chip.id}

                  type="button"

                  aria-pressed={active}

                  className={`rounded-full border px-3 py-1 text-xs transition-colors ${

                    active

                      ? 'border-blue-600 bg-blue-50 text-blue-800 dark:border-blue-500 dark:bg-blue-950/40 dark:text-blue-200'

                      : 'border-gray-200 bg-gray-50 text-gray-700 hover:bg-gray-100 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300 dark:hover:bg-gray-800'

                  }`}

                  onClick={() => toggleReason(chip.id)}

                >

                  {chip.label}

                </button>

              )

            })}

          </div>

        </div>



        <label className="mb-4 block text-sm">

          <span className="mb-1 block font-medium">Expected physical receipts (optional)</span>

          <input

            type="number"

            min={2}

            max={RE_VLM_EXPECTED_COUNT_MAX}

            className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"

            value={expectedCount}

            placeholder="e.g. 7, 9, or 10 — any count"

            onChange={e => setExpectedCount(e.target.value)}

          />

          <span className="mt-1 block text-xs text-gray-500">

            Hard count assertion for this Re-VLM. Used to rank segmentation hypotheses and

            block silent success on mismatch. Leave blank if unsure.

          </span>

        </label>



        <label className="mb-4 block text-sm">

          <span className="mb-1 block font-medium">Additional note (optional)</span>

          <textarea

            className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"

            rows={3}

            maxLength={RE_VLM_NOTE_MAX_LEN}

            value={note}

            placeholder="e.g. Page has 3 JPY taxi receipts"

            onChange={e => setNote(e.target.value)}

          />

          <span className="mt-1 block text-xs text-gray-500">

            {note.length}/{RE_VLM_NOTE_MAX_LEN} — used once for this Re-VLM only

          </span>

        </label>



        <div className="flex gap-2">

          <button

            type="button"

            className="btn-primary"

            disabled={!selectedFileIds.length || busy}

            onClick={() =>

              onConfirm({

                taskFileIds: selectedFileIds,

                rescanReasons: selectedReasons,

                rescanNote: note.trim(),

                expectedReceiptCount: (() => {

                  const n = Number.parseInt(expectedCount, 10)

                  if (!Number.isFinite(n) || n < 2 || n > RE_VLM_EXPECTED_COUNT_MAX) return null

                  return n

                })(),

                workflow: workflowDraft ?? undefined,

              })

            }

          >

            Run Re-VLM

          </button>

          <button type="button" className="btn-secondary" disabled={busy} onClick={onCancel}>

            Cancel

          </button>

        </div>

      </div>

    </div>

  )

}


