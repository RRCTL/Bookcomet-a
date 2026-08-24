import { useMemo, useState } from 'react'
import { AQ_PREVIEW_CASES, type AqPreviewCase } from './aqPreviewData'

const STATUS_CLASS: Record<string, string> = {
  clear: 'bg-emerald-50 text-emerald-800 border-emerald-200',
  recoverable: 'bg-amber-50 text-amber-900 border-amber-200',
  unrecoverable: 'bg-red-50 text-red-800 border-red-200',
}

function metricRows(q?: Record<string, number> | null) {
  if (!q) return []
  const keys = [
    'blur_variance',
    'local_contrast',
    'glare_hotspot_fraction',
    'glare_fraction',
    'ink_fraction',
    'edge_density',
  ]
  return keys.filter(k => q[k] != null).map(k => ({ k, v: q[k] }))
}

/** Abstract bar sketch — never a photographed receipt. */
function SyntheticSketch({
  sketch,
  enhanced,
}: {
  sketch: AqPreviewCase['sketch']
  enhanced?: boolean
}) {
  const ink =
    sketch === 'faded' && !enhanced ? '#c8c8c8' : sketch === 'faded' ? '#6b6b6b' : sketch === 'blurry' ? '#9a9a9a' : '#3f3f46'
  const blurFilter = sketch === 'blurry' ? 'url(#softBlur)' : undefined
  return (
    <svg
      viewBox="0 0 320 420"
      className="mx-auto max-h-[360px] w-full bg-slate-100"
      role="img"
      aria-label={`Synthetic ${sketch} crop sketch`}
    >
      <defs>
        <filter id="softBlur">
          <feGaussianBlur stdDeviation="2.2" />
        </filter>
      </defs>
      <rect x={0} y={0} width={320} height={420} fill="#e8e8e8" />
      <rect x={20} y={20} width={280} height={380} fill="#f3f3f3" stroke="#d4d4d8" />
      <g filter={blurFilter}>
        {Array.from({ length: 11 }).map((_, i) => {
          const y = 55 + i * 28
          const w = 220 - (i % 5) * 12
          return <rect key={i} x={40} y={y} width={w} height={i % 3 === 0 ? 6 : 4} fill={ink} rx={1} />
        })}
        <rect x={40} y={370} width={240} height={16} fill={ink} rx={1} />
      </g>
      {sketch === 'glare' ? (
        <>
          <circle cx={160} cy={200} r={88} fill="white" opacity={0.95} />
          <circle cx={175} cy={185} r={48} fill="white" />
        </>
      ) : null}
      <text x={160} y={410} textAnchor="middle" fill="#94a3b8" fontSize={11} fontFamily="ui-sans-serif, system-ui">
        synthetic · not a receipt photo
      </text>
    </svg>
  )
}

export default function AqQualityPreviewPage() {
  const cases = AQ_PREVIEW_CASES
  const [selectedId, setSelectedId] = useState<string>(cases[0]?.id ?? 'clean')
  const [showEnhanced, setShowEnhanced] = useState(true)

  const selected = useMemo(
    () => cases.find(c => c.id === selectedId) ?? null,
    [cases, selectedId],
  )

  const showingEnhanced = Boolean(selected?.has_enhanced_sketch && showEnhanced)

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white px-5 py-3">
        <div className="mx-auto max-w-[1400px]">
          <p className="m-0 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            M-VDU preview · AQ-01 + AQ-02 · synthetic metrics only · no image uploads
          </p>
          <h1 className="m-0 text-lg font-semibold tracking-tight">Receipt image quality</h1>
          <p className="m-0 mt-0.5 text-sm text-slate-600">
            Local OpenCV probes and minimal reversible recipes. Original crops stay immutable; primary OCR
            uses the selected variant. Preview uses SVG sketches + VM metric snapshots — no receipt photos in
            the repo.
          </p>
        </div>
      </header>

      <main className="mx-auto grid max-w-[1400px] gap-4 p-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(300px,0.9fr)]">
        <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
            <h2 className="m-0 text-sm font-semibold">Quality triage (table-first chips)</h2>
            <span className="text-xs text-slate-500">{cases.length} synthetic cases</span>
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
                        setShowEnhanced(Boolean(c.has_enhanced_sketch))
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
            <h2 className="m-0 text-sm font-semibold">Crop sketch</h2>
            {selected?.has_enhanced_sketch ? (
              <button
                type="button"
                className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50"
                onClick={() => setShowEnhanced(v => !v)}
              >
                {showingEnhanced ? 'Show original sketch' : 'Show enhanced sketch'}
              </button>
            ) : (
              <span className="text-[11px] text-slate-500">SVG only</span>
            )}
          </div>
          {selected ? (
            <>
              <div className="overflow-hidden rounded-md border border-slate-200">
                <SyntheticSketch sketch={selected.sketch} enhanced={showingEnhanced} />
              </div>
              <p className="mt-2 mb-1 text-[11px] text-slate-500">
                {showingEnhanced ? 'Enhanced sketch' : 'Original sketch'} · no photo assets in git
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
        DEV preview only. Metrics from VM OpenCV runs; sketches are inline SVG. No receipt photos committed.
        Route excluded from production builds.
      </footer>
    </div>
  )
}
