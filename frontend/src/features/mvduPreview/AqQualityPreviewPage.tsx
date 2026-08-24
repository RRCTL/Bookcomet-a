import { useEffect, useMemo, useState } from 'react'

type QualityCase = {
  id: string
  label: string
  original_url: string
  enhanced_url: string | null
  selection: string
  ui_label: string
  status: string
  ui_state: string
  issues: string[]
  reason?: string
  score_before?: number
  score_after?: number
  recipe: Array<Record<string, unknown>>
  quality_before?: Record<string, number>
  quality_after?: Record<string, number>
}

const STATUS_CLASS: Record<string, string> = {
  clear: 'bg-emerald-50 text-emerald-800 border-emerald-200',
  recoverable: 'bg-amber-50 text-amber-900 border-amber-200',
  unrecoverable: 'bg-red-50 text-red-800 border-red-200',
}

function metricRows(q?: Record<string, number>) {
  if (!q) return []
  const keys = [
    'blur_variance',
    'local_contrast',
    'glare_hotspot_fraction',
    'glare_fraction',
    'ink_fraction',
    'edge_density',
  ]
  return keys
    .filter(k => q[k] != null)
    .map(k => ({ k, v: q[k] }))
}

export default function AqQualityPreviewPage() {
  const [cases, setCases] = useState<QualityCase[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showEnhanced, setShowEnhanced] = useState(true)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const res = await fetch('/mvdu-aq-preview/demo.json', { cache: 'no-store' })
        if (!res.ok) throw new Error(`demo.json HTTP ${res.status}`)
        const data = (await res.json()) as { cases: QualityCase[] }
        if (cancelled) return
        setCases(data.cases ?? [])
        setSelectedId(data.cases?.[0]?.id ?? null)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const selected = useMemo(
    () => cases.find(c => c.id === selectedId) ?? null,
    [cases, selectedId],
  )

  const previewUrl =
    selected && showEnhanced && selected.enhanced_url
      ? selected.enhanced_url
      : selected?.original_url

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white px-5 py-3">
        <div className="mx-auto max-w-[1400px]">
          <p className="m-0 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            M-VDU preview · AQ-01 + AQ-02 · VM test · synthetic only · no PR
          </p>
          <h1 className="m-0 text-lg font-semibold tracking-tight">Receipt image quality</h1>
          <p className="m-0 mt-0.5 text-sm text-slate-600">
            Local OpenCV probes and minimal reversible recipes. Original crops stay immutable; primary OCR
            uses the selected variant.
          </p>
        </div>
      </header>

      {error ? (
        <div className="border-b border-red-200 bg-red-50 px-5 py-2 text-sm text-red-800">{error}</div>
      ) : null}

      <main className="mx-auto grid max-w-[1400px] gap-4 p-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(300px,0.9fr)]">
        <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
            <h2 className="m-0 text-sm font-semibold">Quality triage (table-first chips)</h2>
            <span className="text-xs text-slate-500">{cases.length} synthetic crops</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] border-collapse text-left text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-3 py-2 font-medium">Case</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Selection</th>
                  <th className="px-3 py-2 font-medium">Score</th>
                  <th className="px-3 py-2 font-medium">Issues</th>
                </tr>
              </thead>
              <tbody>
                {cases.map(c => {
                  const active = c.id === selectedId
                  return (
                    <tr
                      key={c.id}
                      className={`cursor-pointer border-t border-slate-100 ${
                        active ? 'bg-blue-50/70' : 'hover:bg-slate-50'
                      }`}
                      onClick={() => {
                        setSelectedId(c.id)
                        setShowEnhanced(Boolean(c.enhanced_url))
                      }}
                    >
                      <td className="px-3 py-2 font-medium">{c.label}</td>
                      <td className="px-3 py-2">
                        <span
                          className={`inline-flex rounded border px-2 py-0.5 text-xs font-medium ${
                            STATUS_CLASS[c.status] ?? 'bg-slate-50 text-slate-700 border-slate-200'
                          }`}
                        >
                          {c.status}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-xs text-slate-700">{c.ui_label}</td>
                      <td className="px-3 py-2 tabular-nums text-slate-700">
                        {c.score_before?.toFixed(3) ?? '—'}
                        {c.score_after != null ? ` → ${c.score_after.toFixed(3)}` : ''}
                      </td>
                      <td className="px-3 py-2 text-xs text-slate-600">
                        {c.issues.length ? c.issues.join(', ') : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {selected?.reason ? (
            <div className="border-t border-slate-100 bg-slate-50 px-4 py-2 text-xs text-slate-600">
              {selected.reason}
            </div>
          ) : null}
        </section>

        <aside className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
          <div className="mb-2 flex items-center justify-between gap-2">
            <h2 className="m-0 text-sm font-semibold">Crop preview</h2>
            {selected?.enhanced_url ? (
              <button
                type="button"
                className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50"
                onClick={() => setShowEnhanced(v => !v)}
              >
                {showEnhanced ? 'Show original' : 'Show enhanced'}
              </button>
            ) : (
              <span className="text-[11px] text-slate-500">original only</span>
            )}
          </div>
          {previewUrl ? (
            <div className="overflow-hidden rounded-md border border-slate-200 bg-slate-100">
              <img
                src={previewUrl}
                alt={`${selected?.label ?? 'crop'} synthetic preview`}
                className="mx-auto max-h-[360px] w-auto object-contain"
              />
            </div>
          ) : (
            <p className="m-0 text-sm text-slate-500">Select a case</p>
          )}
          {selected ? (
            <>
              <p className="mt-2 mb-1 text-[11px] text-slate-500">
                Showing {showEnhanced && selected.enhanced_url ? 'enhanced variant' : 'original'} · abstract
                bars, not a real receipt
              </p>
              <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-slate-600">
                <dt className="text-slate-400">Selection</dt>
                <dd className="m-0 font-medium text-slate-800">{selected.selection}</dd>
                <dt className="text-slate-400">UI state</dt>
                <dd className="m-0">{selected.ui_state}</dd>
                <dt className="text-slate-400">Recipe</dt>
                <dd className="m-0">
                  {selected.recipe.length
                    ? selected.recipe.map(r => String(r.op)).join(' → ')
                    : 'none'}
                </dd>
              </dl>
              <div className="mt-3 grid grid-cols-2 gap-3">
                <div>
                  <h3 className="m-0 mb-1 text-xs font-semibold text-slate-700">Before</h3>
                  <ul className="m-0 list-none p-0 text-[11px] text-slate-600">
                    {metricRows(selected.quality_before).map(m => (
                      <li key={m.k} className="flex justify-between gap-2 border-t border-slate-100 py-0.5">
                        <span>{m.k}</span>
                        <span className="tabular-nums">{Number(m.v).toFixed(4)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <h3 className="m-0 mb-1 text-xs font-semibold text-slate-700">After</h3>
                  {selected.quality_after ? (
                    <ul className="m-0 list-none p-0 text-[11px] text-slate-600">
                      {metricRows(selected.quality_after).map(m => (
                        <li
                          key={m.k}
                          className="flex justify-between gap-2 border-t border-slate-100 py-0.5"
                        >
                          <span>{m.k}</span>
                          <span className="tabular-nums">{Number(m.v).toFixed(4)}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="m-0 text-[11px] text-slate-400">No enhancement applied</p>
                  )}
                </div>
              </div>
            </>
          ) : null}
        </aside>
      </main>

      <footer className="mx-auto max-w-[1400px] px-4 pb-8 text-xs text-slate-500">
        DEV preview only. Assets under <code>/mvdu-aq-preview/</code> are synthetic OpenCV drawings generated
        on the VM. No real receipts. Route excluded from production builds.
      </footer>
    </div>
  )
}
