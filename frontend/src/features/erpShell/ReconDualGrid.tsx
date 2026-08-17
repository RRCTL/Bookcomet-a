import { useEffect, useMemo, useRef, useState } from 'react'

import type { ReconGridRow } from '../recon/reconTypes'



type Props = {

  title: string

  rows: ReconGridRow[]

  selectedIds: string[]

  matchedIds: Set<string>

  onToggle: (id: string) => void

  onToggleAll: (ids: string[], select: boolean) => void

}



const ROW_HEIGHT = 34

const OVERSCAN = 8



function statusLabel(status: string): { cls: string; label: string } {

  const s = status.toLowerCase()

  if (s === 'reconciled' || s === 'matched') return { cls: 'posted', label: 'Reconciled' }

  if (s === 'partial') return { cls: 'open', label: 'Partial' }

  return { cls: 'review', label: 'Unreconciled' }

}



function fmtAmount(n: number, currency: string, drCr?: 'Dr' | 'Cr'): string {

  const v = Number(n)

  const mag = Number.isNaN(v) ? 0 : Math.abs(v)

  const text = mag.toLocaleString('en-HK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

  const side = drCr ?? (Number.isNaN(v) || v >= 0 ? 'Dr' : 'Cr')

  const body = currency ? `${currency} ${text}` : text

  return `${side} ${body}`

}



function fmtDate(iso: string): string {

  if (!iso) return '-'

  return iso.slice(0, 10)

}



export function ReconDualGrid({ title, rows, selectedIds, matchedIds, onToggle, onToggleAll }: Props) {

  const wrapRef = useRef<HTMLDivElement>(null)

  const [scrollTop, setScrollTop] = useState(0)

  const [viewportH, setViewportH] = useState(320)



  const rowIds = useMemo(() => rows.map(r => r.id), [rows])

  const allSelected = rowIds.length > 0 && rowIds.every(id => selectedIds.includes(id))



  useEffect(() => {

    const el = wrapRef.current

    if (!el) return

    const ro = new ResizeObserver(() => setViewportH(el.clientHeight || 320))

    ro.observe(el)

    setViewportH(el.clientHeight || 320)

    return () => ro.disconnect()

  }, [])



  const startIdx = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN)

  const visibleCount = Math.ceil(viewportH / ROW_HEIGHT) + OVERSCAN * 2

  const endIdx = Math.min(rows.length, startIdx + visibleCount)

  const visibleRows = rows.slice(startIdx, endIdx)

  const padTop = startIdx * ROW_HEIGHT

  const padBottom = Math.max(0, (rows.length - endIdx) * ROW_HEIGHT)



  return (

    <div className="erp-recon-pane">

      <div className="erp-recon-pane-head">

        <span className="erp-recon-pane-title">{title}</span>

        <span className="erp-recon-pane-count">{rows.length} rows</span>

      </div>

      <div

        ref={wrapRef}

        className="erp-recon-grid-wrap"

        onScroll={e => setScrollTop(e.currentTarget.scrollTop)}

      >

        <table className="erp-recon-grid">

          <thead>

            <tr>

              <th className="erp-recon-col-check">

                <input

                  type="checkbox"

                  checked={allSelected}

                  disabled={rows.length === 0}

                  onChange={e => onToggleAll(rowIds, e.target.checked)}

                  aria-label="Select all"

                />

              </th>

              <th>Date</th>

              <th>Reference</th>

              <th>Description</th>

              <th>Amount</th>

              <th>Status</th>

            </tr>

          </thead>

          <tbody>

            {rows.length === 0 ? (

              <tr>

                <td colSpan={6} className="erp-recon-empty">

                  No transactions in this pane.

                </td>

              </tr>

            ) : (

              <>

                {padTop > 0 && (

                  <tr aria-hidden="true" className="erp-recon-spacer">

                    <td colSpan={6} style={{ height: padTop, padding: 0, border: 'none' }} />

                  </tr>

                )}

                {visibleRows.map(row => {

                  const selected = selectedIds.includes(row.id)

                  const matched = matchedIds.has(row.id)

                  const badge = statusLabel(matched ? 'matched' : row.status)

                  const rowCls = [

                    selected ? 'sel' : '',

                    matched ? 'matched' : '',

                    row.status === 'partial' ? 'warn' : '',

                  ]

                    .filter(Boolean)

                    .join(' ')

                  return (

                    <tr key={row.id} className={rowCls}>

                      <td className="erp-recon-col-check">

                        <input

                          type="checkbox"

                          checked={selected}

                          onChange={() => onToggle(row.id)}

                          aria-label="Select row"

                        />

                      </td>

                      <td>{fmtDate(row.date)}</td>

                      <td className="erp-recon-ref" title={row.reference || undefined}>

                        {row.reference || '-'}

                      </td>

                      <td className="erp-recon-desc" title={row.description || undefined}>

                        {row.description || '-'}

                      </td>

                      <td className="erp-recon-amt">{fmtAmount(row.amount, row.currency, row.drCr)}</td>

                      <td>

                        <span className={`erp-badge ${badge.cls}`}>{badge.label}</span>

                      </td>

                    </tr>

                  )

                })}

                {padBottom > 0 && (

                  <tr aria-hidden="true" className="erp-recon-spacer">

                    <td colSpan={6} style={{ height: padBottom, padding: 0, border: 'none' }} />

                  </tr>

                )}

              </>

            )}

          </tbody>

        </table>

      </div>

    </div>

  )

}


