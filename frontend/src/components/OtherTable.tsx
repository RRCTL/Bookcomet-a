/**
 * OtherTable — editable table for OTHER task records.
 *
 * Shows different columns depending on record_type:
 *   loan       → lender, principal, rate, tenor, monthly installment, dates, outstanding, status
 *   fixed_asset → name, type, purchase amount, acquisition date, useful life, residual,
 *                 depreciation method, accumulated depreciation, net book value
 *
 * Supports double-click cell editing, CSV export, and expandable depreciation schedule.
 */
import { useState, useRef, useCallback } from 'react'
import type { OtherRow } from '../types/other'
import { api } from '../services/api'
import './OtherTable.css'

type Props = {
  records: OtherRow[]
  onRecordChange?: (recordId: string, updated: OtherRow) => void
  readOnly?: boolean
}

// ── Column definitions ────────────────────────────────────────────────────────

const LOAN_COLUMNS: { key: keyof OtherRow; label: string; width?: number }[] = [
  { key: 'source_file_label', label: 'Source File', width: 160 },
  { key: 'lender_name', label: 'Lender', width: 160 },
  { key: 'loan_reference', label: 'Reference', width: 120 },
  { key: 'principal_amount', label: 'Principal', width: 110 },
  { key: 'currency', label: 'Ccy', width: 55 },
  { key: 'interest_rate_pct', label: 'Rate (%)', width: 80 },
  { key: 'tenor_months', label: 'Tenor (M)', width: 80 },
  { key: 'monthly_installment', label: 'Monthly Inst.', width: 110 },
  { key: 'start_date', label: 'Start Date', width: 100 },
  { key: 'maturity_date', label: 'Maturity', width: 100 },
  { key: 'outstanding_principal', label: 'Outstanding', width: 110 },
  { key: 'status', label: 'Status', width: 80 },
  { key: 'memo', label: 'Memo', width: 160 },
]

const ASSET_COLUMNS: { key: keyof OtherRow; label: string; width?: number }[] = [
  { key: 'source_file_label', label: 'Source File', width: 160 },
  { key: 'asset_name', label: 'Asset Name', width: 180 },
  { key: 'asset_type', label: 'Type', width: 90 },
  { key: 'purchase_amount', label: 'Purchase Amt', width: 120 },
  { key: 'currency', label: 'Ccy', width: 55 },
  { key: 'acquisition_date', label: 'Acq. Date', width: 100 },
  { key: 'vendor', label: 'Vendor', width: 140 },
  { key: 'useful_life_months', label: 'Life (M)', width: 75 },
  { key: 'residual_value', label: 'Residual', width: 90 },
  { key: 'depreciation_method', label: 'Method', width: 120 },
  { key: 'accumulated_depreciation', label: 'Accum. Depr.', width: 120 },
  { key: 'net_book_value', label: 'NBV', width: 100 },
  { key: 'status', label: 'Status', width: 80 },
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function exportToCsv(rows: OtherRow[], columns: typeof LOAN_COLUMNS): void {
  const header = columns.map(c => `"${c.label}"`).join(',')
  const body = rows.map(r =>
    columns.map(c => {
      const v = r[c.key]
      return v == null ? '' : `"${String(v).replace(/"/g, '""')}"`
    }).join(',')
  )
  const csv = [header, ...body].join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `other_${Date.now()}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

type DepreciationRow = {
  period_number: number
  period_start: string
  period_end: string
  depreciation_amount: number
  accumulated_at_period_end: number
  net_book_value_at_period_end: number
}

type DepreciationScheduleData = {
  asset_name: string
  purchase_amount: number
  residual_value: number
  useful_life_months: number
  depreciation_method: string
  accumulated_depreciation: number
  net_book_value: number
  schedule: DepreciationRow[]
}

// ── Component ─────────────────────────────────────────────────────────────────

export function OtherTable({ records, onRecordChange, readOnly = false }: Props) {
  const [editingCell, setEditingCell] = useState<{ recordId: string; field: string } | null>(null)
  const [editValue, setEditValue] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const [expandedDeprId, setExpandedDeprId] = useState<string | null>(null)
  const [deprData, setDeprData] = useState<Record<string, DepreciationScheduleData>>({})
  const [deprLoading, setDeprLoading] = useState<string | null>(null)

  const toggleDeprSchedule = async (recordId: string) => {
    if (expandedDeprId === recordId) {
      setExpandedDeprId(null)
      return
    }
    setExpandedDeprId(recordId)
    if (!deprData[recordId]) {
      setDeprLoading(recordId)
      try {
        const data = await api.getDepreciationSchedule(recordId)
        setDeprData(prev => ({ ...prev, [recordId]: data as unknown as DepreciationScheduleData }))
      } finally {
        setDeprLoading(null)
      }
    }
  }

  // Group records by type; render each group separately
  const loanRecords = records.filter(r => r.record_type === 'loan')
  const assetRecords = records.filter(r => r.record_type === 'fixed_asset')

  const startEdit = useCallback((recordId: string, field: string, currentValue: unknown) => {
    if (readOnly) return
    setEditingCell({ recordId, field })
    setEditValue(currentValue == null ? '' : String(currentValue))
    setTimeout(() => inputRef.current?.focus(), 0)
  }, [readOnly])

  const commitEdit = useCallback(() => {
    if (!editingCell || !onRecordChange) { setEditingCell(null); return }
    const rec = records.find(r => r.id === editingCell.recordId)
    if (!rec) { setEditingCell(null); return }
    const updated = { ...rec, [editingCell.field]: editValue }
    onRecordChange(editingCell.recordId, updated)
    setEditingCell(null)
  }, [editingCell, editValue, onRecordChange, records])

  const renderCell = (row: OtherRow, field: string, width?: number) => {
    const value = row[field as keyof OtherRow]
    if (field === 'source_file_label') {
      const label = value == null ? '' : String(value)
      return (
        <td
          key={field}
          style={{ width: width ? `${width}px` : undefined, minWidth: width ? `${width}px` : undefined }}
          className="alt-cell"
        >
          <span className="alt-cell-text" title={label || undefined}>
            {label}
          </span>
        </td>
      )
    }
    const isEditing = editingCell?.recordId === row.id && editingCell.field === field

    return (
      <td
        key={field}
        style={{ width: width ? `${width}px` : undefined, minWidth: width ? `${width}px` : undefined }}
        onDoubleClick={() => startEdit(row.id, field, value)}
        className={`alt-cell${isEditing ? ' alt-cell-editing' : ''}`}
      >
        {isEditing ? (
          <input
            ref={inputRef}
            className="alt-cell-input"
            value={editValue}
            onChange={e => setEditValue(e.target.value)}
            onBlur={commitEdit}
            onKeyDown={e => {
              if (e.key === 'Enter') commitEdit()
              if (e.key === 'Escape') setEditingCell(null)
            }}
          />
        ) : (
          <span className="alt-cell-text">{value == null ? '' : String(value)}</span>
        )}
      </td>
    )
  }

  const renderTable = (
    rows: OtherRow[],
    columns: typeof LOAN_COLUMNS,
    title: string,
    typeLabel: string
  ) => {
    if (rows.length === 0) return null
    return (
      <div className="alt-group">
        <div className="alt-group-header">
          <span className="alt-group-title">{title}</span>
          <span className="alt-group-count">{rows.length} record{rows.length > 1 ? 's' : ''}</span>
          <button className="alt-export-btn" onClick={() => exportToCsv(rows, columns)}>
            ↓ CSV
          </button>
        </div>
        <div className="alt-scroll-wrapper">
          <table className="alt-table">
            <thead>
              <tr>
                {columns.map(c => (
                  <th key={c.key as string} style={{ width: c.width ? `${c.width}px` : undefined }}>
                    {c.label}
                  </th>
                ))}
                {typeLabel === 'fixed_asset' && <th style={{ width: 110 }}>Schedule</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map(row => (
                <>
                  <tr key={row.id} className="alt-row">
                    {columns.map(c => renderCell(row, c.key as string, c.width))}
                    {typeLabel === 'fixed_asset' && (
                      <td style={{ padding: '0.25rem 0.5rem' }}>
                        <button
                          className="alt-depr-toggle"
                          onClick={() => toggleDeprSchedule(row.id)}
                        >
                          {expandedDeprId === row.id ? '▲ Hide' : '▼ Depreciation'}
                        </button>
                      </td>
                    )}
                  </tr>
                  {typeLabel === 'fixed_asset' && expandedDeprId === row.id && (
                    <tr key={`${row.id}-depr`}>
                      <td colSpan={columns.length + 1} style={{ padding: '0.5rem 1rem', background: '#f0fdf4' }}>
                        {deprLoading === row.id ? (
                          <span style={{ color: '#6b7280', fontSize: '0.82rem' }}>Loading...</span>
                        ) : deprData[row.id] ? (
                          <div className="alt-depr-section">
                            <div style={{ fontSize: '0.82rem', color: '#166534', marginBottom: '0.35rem', fontWeight: 600 }}>
                              Depreciation Schedule — {deprData[row.id].depreciation_method?.replace('_', ' ')}
                              {' · '}Accum: {deprData[row.id].accumulated_depreciation?.toLocaleString()}
                              {' · '}NBV: {deprData[row.id].net_book_value?.toLocaleString()}
                            </div>
                            <table className="alt-depr-table">
                              <thead>
                                <tr>
                                  <th style={{ textAlign: 'left' }}>Period</th>
                                  <th>Start</th>
                                  <th>Dep. Amt</th>
                                  <th>Accumulated</th>
                                  <th>Net Book Value</th>
                                </tr>
                              </thead>
                              <tbody>
                                {deprData[row.id].schedule.slice(0, 60).map((s) => (
                                  <tr key={s.period_number}>
                                    <td style={{ textAlign: 'left' }}>{s.period_number}</td>
                                    <td>{s.period_start}</td>
                                    <td>{s.depreciation_amount?.toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                                    <td>{s.accumulated_at_period_end?.toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                                    <td>{s.net_book_value_at_period_end?.toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                            {deprData[row.id].schedule.length > 60 && (
                              <div style={{ fontSize: '0.75rem', color: '#9ca3af', marginTop: '0.3rem' }}>
                                Showing first 60 of {deprData[row.id].schedule.length} periods
                              </div>
                            )}
                          </div>
                        ) : (
                          <span style={{ color: '#6b7280', fontSize: '0.82rem' }}>No depreciation schedule available.</span>
                        )}
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
        {!readOnly && (
          <div className="alt-hint">Double-click any cell to edit · Changes auto-sync to formal records</div>
        )}
      </div>
    )
  }

  if (records.length === 0) {
    return <div className="alt-empty">No assets/liabilities records yet.</div>
  }

  return (
    <div className="alt-container">
      {renderTable(loanRecords, LOAN_COLUMNS, 'Loans & Liabilities', 'loan')}
      {renderTable(assetRecords, ASSET_COLUMNS, 'Fixed Assets', 'fixed_asset')}
    </div>
  )
}
