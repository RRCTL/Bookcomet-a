import type { DemoRegion } from './demoData'
import { DEMO_PAGE } from './demoData'

type Props = {
  regions: DemoRegion[]
  selectedRegionId: string | null
  onSelect: (regionId: string) => void
}

function strokeFor(region: DemoRegion, selected: boolean): { stroke: string; width: number; dash?: string } {
  if (region.status === 'superseded') {
    return { stroke: '#94a3b8', width: 1.5, dash: '4 3' }
  }
  if (region.status === 'conflict' || region.hold_ocr) {
    return { stroke: selected ? '#dc2626' : '#ef4444', width: selected ? 3 : 2 }
  }
  if (region.confidence < 0.8) {
    return { stroke: selected ? '#ca8a04' : '#eab308', width: selected ? 3 : 2 }
  }
  return { stroke: selected ? '#2563eb' : '#93c5fd', width: selected ? 3 : 1.5 }
}

/** Abstract page canvas with normalized region boxes — no receipt photos. */
export function RegionPreviewPanel({ regions, selectedRegionId, onSelect }: Props) {
  const visible = regions.filter(r => r.status !== 'superseded')

  return (
    <div className="flex h-full min-h-[280px] flex-col gap-2">
      <div className="flex items-center justify-between text-xs text-slate-600">
        <span>{DEMO_PAGE.title}</span>
        <span>
          {visible.length} regions · synthetic geometry
        </span>
      </div>
      <div className="relative flex-1 overflow-hidden rounded-md border border-slate-200 bg-slate-100">
        <svg
          viewBox={`0 0 ${DEMO_PAGE.width} ${DEMO_PAGE.height}`}
          className="h-full w-full"
          role="img"
          aria-label="Synthetic multi-receipt page preview"
        >
          <rect x={0} y={0} width={DEMO_PAGE.width} height={DEMO_PAGE.height} fill="#f8fafc" />
          <rect
            x={18}
            y={18}
            width={DEMO_PAGE.width - 36}
            height={DEMO_PAGE.height - 36}
            fill="#fff"
            stroke="#e2e8f0"
            strokeWidth={2}
          />
          {regions.map(region => {
            const selected = region.region_id === selectedRegionId
            const { stroke, width, dash } = strokeFor(region, selected)
            const x = region.geometry.x * DEMO_PAGE.width
            const y = region.geometry.y * DEMO_PAGE.height
            const w = region.geometry.w * DEMO_PAGE.width
            const h = region.geometry.h * DEMO_PAGE.height
            const faded = region.status === 'superseded'
            return (
              <g
                key={region.region_id}
                opacity={faded ? 0.35 : 1}
                style={{ cursor: faded ? 'default' : 'pointer' }}
                onClick={() => {
                  if (!faded) onSelect(region.region_id)
                }}
              >
                <rect
                  x={x}
                  y={y}
                  width={w}
                  height={h}
                  fill={
                    region.hold_ocr
                      ? 'rgba(239,68,68,0.08)'
                      : selected
                        ? 'rgba(37,99,235,0.06)'
                        : 'rgba(148,163,184,0.06)'
                  }
                  stroke={stroke}
                  strokeWidth={width}
                  strokeDasharray={dash}
                />
                <rect x={x + 8} y={y + 8} width={52} height={22} rx={3} fill={stroke} />
                <text
                  x={x + 34}
                  y={y + 23}
                  textAnchor="middle"
                  fill="#fff"
                  fontSize={12}
                  fontFamily="ui-sans-serif, system-ui, sans-serif"
                  fontWeight={600}
                >
                  {region.label}
                </text>
                <text
                  x={x + 12}
                  y={y + 48}
                  fill="#64748b"
                  fontSize={11}
                  fontFamily="ui-sans-serif, system-ui, sans-serif"
                >
                  {region.source.replace('_', ' ')}
                  {region.hold_ocr ? ' · OCR held' : ''}
                </text>
                {/* Abstract content lines — not real receipt text */}
                <line x1={x + 16} y1={y + h * 0.35} x2={x + w - 16} y2={y + h * 0.35} stroke="#cbd5e1" strokeWidth={6} />
                <line x1={x + 16} y1={y + h * 0.48} x2={x + w * 0.7} y2={y + h * 0.48} stroke="#e2e8f0" strokeWidth={5} />
                <line x1={x + 16} y1={y + h * 0.61} x2={x + w * 0.55} y2={y + h * 0.61} stroke="#e2e8f0" strokeWidth={5} />
              </g>
            )
          })}
        </svg>
      </div>
      <p className="m-0 text-[11px] leading-snug text-slate-500">
        Blue = ready · Yellow = provisional · Red = conflict (OCR held) · Click a box or table row to sync
        highlight.
      </p>
    </div>
  )
}
