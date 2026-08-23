import { useEffect, useMemo, useRef, useState } from 'react'
import {
  applySplitConflict,
  initialRegions,
  initialRows,
  type DemoRegion,
  type DemoRow,
  type RowReviewState,
} from './demoData'
import { RegionPreviewPanel } from './RegionPreviewPanel'

const STATE_LABEL: Record<RowReviewState, string> = {
  ready: 'Ready',
  provisional: 'Check source',
  needs_region_review: 'Region issue',
  needs_data_review: 'Data review',
  superseded: 'Superseded',
  rejected: 'Rejected',
  processing: 'Processing',
}

const STATE_CLASS: Record<RowReviewState, string> = {
  ready: 'bg-emerald-50 text-emerald-800 border-emerald-200',
  provisional: 'bg-amber-50 text-amber-900 border-amber-200',
  needs_region_review: 'bg-red-50 text-red-800 border-red-200',
  needs_data_review: 'bg-amber-50 text-amber-900 border-amber-200',
  superseded: 'bg-slate-100 text-slate-500 border-slate-200',
  rejected: 'bg-slate-100 text-slate-500 border-slate-200',
  processing: 'bg-slate-50 text-slate-600 border-slate-200',
}

function fmtAmt(amount: number | null, currency: string): string {
  if (amount == null) return '—'
  return `${currency || 'HKD'} ${amount.toLocaleString('en-HK', { minimumFractionDigits: 2 })}`
}

export default function TableFirstPreviewPage() {
  const [regions, setRegions] = useState<DemoRegion[]>(() => initialRegions())
  const [rows, setRows] = useState<DemoRow[]>(() => initialRows())
  const [selectedRegionId, setSelectedRegionId] = useState<string | null>('rrg_r3')
  const [previewOpen, setPreviewOpen] = useState(true)
  const [showSuperseded, setShowSuperseded] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const toastTimerRef = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (toastTimerRef.current != null) window.clearTimeout(toastTimerRef.current)
    }
  }, [])

  const visibleRows = useMemo(
    () => rows.filter(r => showSuperseded || r.review_state !== 'superseded'),
    [rows, showSuperseded],
  )

  const selectedRow = visibleRows.find(r => r.region_id === selectedRegionId) ?? null
  const selectedRegion = regions.find(r => r.region_id === selectedRegionId) ?? null

  function flash(msg: string) {
    setToast(msg)
    if (toastTimerRef.current != null) window.clearTimeout(toastTimerRef.current)
    toastTimerRef.current = window.setTimeout(() => setToast(null), 3200)
  }

  /** Pass regionId explicitly — do not rely on React state that may not have flushed yet. */
  function onSplitConflict(regionId?: string | null) {
    const id = regionId ?? selectedRegionId
    const target = regions.find(r => r.region_id === id)
    if (!target || target.status !== 'conflict') {
      flash('Select the red conflict region (R3) to split + targeted re-OCR.')
      return
    }
    const next = applySplitConflict(regions, rows, target.region_id)
    setRegions(next.regions)
    setRows(next.rows)
    const firstChildId = `${target.region_id}_a`
    setSelectedRegionId(
      next.regions.some(r => r.region_id === firstChildId) ? firstChildId : null,
    )
    flash('Targeted re-OCR: only split crops re-ran. Other rows unchanged.')
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white px-5 py-3">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-3">
          <div>
            <p className="m-0 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              M-VDU preview · TF-01 + TF-02 · synthetic only
            </p>
            <h1 className="m-0 text-lg font-semibold tracking-tight">Table-First Immediate OCR</h1>
            <p className="m-0 mt-0.5 text-sm text-slate-600">
              Table is primary; region overlay is evidence-on-demand. Conflict candidates hold OCR but keep a
              placeholder row.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm hover:bg-slate-50"
              onClick={() => setPreviewOpen(v => !v)}
            >
              {previewOpen ? 'Collapse page preview' : 'Expand page preview'}
            </button>
            <label className="flex items-center gap-1.5 text-sm text-slate-600">
              <input
                type="checkbox"
                checked={showSuperseded}
                onChange={e => setShowSuperseded(e.target.checked)}
              />
              Show superseded
            </label>
          </div>
        </div>
      </header>

      {toast ? (
        <div className="border-b border-emerald-200 bg-emerald-50 px-5 py-2 text-sm text-emerald-900">
          {toast}
        </div>
      ) : null}

      <main className="mx-auto grid max-w-[1400px] gap-4 p-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(280px,0.9fr)]">
        <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
            <h2 className="m-0 text-sm font-semibold">Table Review (inside current table)</h2>
            <span className="text-xs text-slate-500">{visibleRows.length} rows · AR+AP path</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] border-collapse text-left text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-3 py-2 font-medium">State</th>
                  <th className="px-3 py-2 font-medium">Source</th>
                  <th className="px-3 py-2 font-medium">Date</th>
                  <th className="px-3 py-2 font-medium">Payee</th>
                  <th className="px-3 py-2 font-medium">Amount</th>
                  <th className="px-3 py-2 font-medium">Category</th>
                  <th className="px-3 py-2 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map(row => {
                  const active = row.region_id === selectedRegionId
                  return (
                    <tr
                      key={row.row_id}
                      className={`border-t border-slate-100 ${active ? 'bg-blue-50/70' : 'hover:bg-slate-50'} ${
                        row.review_state === 'needs_region_review' ? 'bg-red-50/40' : ''
                      }`}
                      onClick={() => setSelectedRegionId(row.region_id)}
                    >
                      <td className="px-3 py-2 align-middle">
                        <span
                          className={`inline-flex rounded border px-2 py-0.5 text-xs font-medium ${STATE_CLASS[row.review_state]}`}
                        >
                          {STATE_LABEL[row.review_state]}
                        </span>
                      </td>
                      <td className="px-3 py-2 align-middle">
                        <button
                          type="button"
                          className="rounded border border-slate-200 bg-white px-2 py-0.5 text-xs text-slate-700 hover:border-blue-300 hover:text-blue-700"
                          onClick={e => {
                            e.stopPropagation()
                            setSelectedRegionId(row.region_id)
                            setPreviewOpen(true)
                          }}
                        >
                          P{row.page} · {row.region_label} · {row.source}
                        </button>
                      </td>
                      <td className="px-3 py-2 align-middle text-slate-700">{row.date || '—'}</td>
                      <td className="px-3 py-2 align-middle text-slate-700">{row.payee || '—'}</td>
                      <td className="px-3 py-2 align-middle tabular-nums text-slate-700">
                        {fmtAmt(row.amount, row.currency)}
                      </td>
                      <td className="px-3 py-2 align-middle text-slate-700">{row.category || '—'}</td>
                      <td className="px-3 py-2 align-middle">
                        <div className="flex flex-wrap gap-1">
                          <button
                            type="button"
                            className="rounded border border-slate-200 px-2 py-0.5 text-xs hover:bg-white"
                            onClick={e => {
                              e.stopPropagation()
                              setSelectedRegionId(row.region_id)
                              setPreviewOpen(true)
                            }}
                          >
                            View source
                          </button>
                          {row.review_state === 'needs_region_review' ? (
                            <button
                              type="button"
                              className="rounded border border-red-300 bg-red-50 px-2 py-0.5 text-xs text-red-800 hover:bg-red-100"
                              onClick={e => {
                                e.stopPropagation()
                                setSelectedRegionId(row.region_id)
                                setPreviewOpen(true)
                                onSplitConflict(row.region_id)
                              }}
                            >
                              Split + re-OCR
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {selectedRow?.note ? (
            <div className="border-t border-slate-100 bg-slate-50 px-4 py-2 text-xs text-slate-600">
              {selectedRow.note}
            </div>
          ) : null}
        </section>

        <aside
          className={`rounded-lg border border-slate-200 bg-white p-3 shadow-sm ${
            previewOpen ? '' : 'opacity-70'
          }`}
        >
          <div className="mb-2 flex items-center justify-between">
            <h2 className="m-0 text-sm font-semibold">Page preview / Region Review</h2>
            <span className="text-[11px] text-slate-500">on demand</span>
          </div>
          {previewOpen ? (
            <>
              <RegionPreviewPanel
                regions={regions}
                selectedRegionId={selectedRegionId}
                onSelect={setSelectedRegionId}
              />
              <div className="mt-3 flex flex-wrap gap-2 border-t border-slate-100 pt-3">
                <button
                  type="button"
                  className="rounded-md border border-slate-300 px-3 py-1.5 text-xs hover:bg-slate-50"
                  disabled={!selectedRegion || selectedRegion.status === 'superseded'}
                  onClick={() => flash('Adjust geometry (preview): would create a new region revision only.')}
                >
                  Adjust
                </button>
                <button
                  type="button"
                  className="rounded-md border border-red-300 bg-red-50 px-3 py-1.5 text-xs text-red-800 hover:bg-red-100 disabled:opacity-40"
                  disabled={!selectedRegion || selectedRegion.status !== 'conflict'}
                  onClick={onSplitConflict}
                >
                  Split + targeted re-OCR
                </button>
                <button
                  type="button"
                  className="rounded-md border border-slate-300 px-3 py-1.5 text-xs hover:bg-slate-50"
                  onClick={() => flash('Merge (preview): would re-OCR only the merged crop.')}
                >
                  Merge
                </button>
              </div>
              {selectedRegion ? (
                <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-slate-600">
                  <dt className="text-slate-400">Region</dt>
                  <dd className="m-0 font-medium text-slate-800">{selectedRegion.label}</dd>
                  <dt className="text-slate-400">Detector</dt>
                  <dd className="m-0">{selectedRegion.source}</dd>
                  <dt className="text-slate-400">OCR</dt>
                  <dd className="m-0">
                    {selectedRegion.hold_ocr ? 'Held (conflict)' : selectedRegion.status}
                  </dd>
                  <dt className="text-slate-400">Confidence</dt>
                  <dd className="m-0">{selectedRegion.confidence.toFixed(2)}</dd>
                </dl>
              ) : null}
            </>
          ) : (
            <p className="m-0 text-sm text-slate-500">
              Preview collapsed — table stays primary. Expand to inspect region evidence.
            </p>
          )}
        </aside>
      </main>

      <footer className="mx-auto max-w-[1400px] px-4 pb-8 text-xs text-slate-500">
        Preview only: synthetic boxes, no real receipt images. Node workflow graph unchanged. Provisional rows
        remain exportable; conflict placeholders are not OCR’d until split/merge.
      </footer>
    </div>
  )
}
