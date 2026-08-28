import React, { useState, useCallback, useEffect, useMemo } from 'react'
import type { CSSProperties } from 'react'
import type { ReconState } from '../types/reconciliation'
import { useViewport } from '../hooks/useViewport'
import { isBankRowGlPosted } from '../utils/glPostedOcrLock'
import { formatMatchedIdForDisplay } from '../utils/reconMatchedSpreadsheet'
import { BANK_ACCOUNT_TYPES_VALID, coalesceBankAccountTypeRows, normalizeBankAccountType } from '../utils/bankAccountTypeCoalesce'
import { formatBankSourceFile, bankSourceFileStem } from '../utils/bankSourceFile'
import { coaNameMapFromOptionLabels } from '../utils/coaDisplay'

const EMPTY_LOCK_KEYS: ReadonlySet<string> = new Set()

// ─── Types ────────────────────────────────────────────────────────────────────

export type BankTransaction = {
  needs_review?: boolean
  validation_flags?: string[]
  extraction_provenance?: Record<string, unknown>
  id_number?: string        // reference / 憑證號 (e.g. "BR-2505-001")
  matched_id?: string       // set by RECON mode (update C); empty until matched
  transaction_date?: string
  date?: string
  source_file?: string
  account_type?: string
  account_number?: string
  particulars?: string
  description?: string
  deposit?: number | null
  withdrawal?: number | null
  balance?: number
  currency?: string
  account_code?: string     // Chart of Accounts code (assigned by AI or user)
  category?: string         // CoA account name (derived from account_code)
  categorise?: string
  confidence_score?: number
  _duplicateLevel?: 1 | 2 | 3 | 4
  _duplicateOf?: string
  _duplicateConfirmed?: boolean
  /** HSBC cross-VLM AR manager pass (model B) */
  _ar_manager_amended?: boolean
  _ar_manager_added?: boolean
  _ar_manager_fields?: string[]
  /** Per-page AR outcome after secondary VLM merge */
  _ar_manager_status?: 'verified' | 'needs_review' | 'error'
  [key: string]: any
}

type Row = BankTransaction & { _id: number }

interface Props {
  transactions: BankTransaction[]
  filename?: string
  processingTime?: string
  coaOptions?: string[]         // e.g. ["4030 Interest Received", "5080 Bank Fee", ...]
  onDeploy?: () => void         // triggered by "Deploy Codes" button
  onDataChange?: (updated: BankTransaction[]) => void
  reconState?: ReconState       // (C) lock map from RECON mode: id_number → {status, matched_id}
  onUnlock?: (id_number: string) => void  // (C) called when user unlocks a matched row
  isCashTable?: boolean         // when true: balance auto-calculated; no RECON locking
  /** Keys (db_id, vouchers, snapshot ids, …) for rows whose match group has a POSTED GL — row read-only */
  glPostedBankLockKeys?: ReadonlySet<string>
  /** Show GL-000006 in 配對ID when matched_id holds reconciliation group UUID */
  glVoucherNoByGroupId?: Record<string, string>
  /** When true, the whole table is view-only: no field edits, no add/delete rows. */
  readOnly?: boolean
  /** Approve the table and transfer it to the destination module (Processing). */
  onApprove?: () => void
  canApprove?: boolean
  approveBusy?: boolean
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function pickVal(obj: Record<string, any>, keys: string[]): any {
  for (const k of keys) {
    const v = obj[k]
    if (v !== undefined && v !== null && String(v).trim() !== '') return v
  }
  return ''
}

function toNum(v: any): number | null {
  if (v === null || v === undefined || String(v).trim() === '') return null
  const n = parseFloat(String(v).replace(/,/g, ''))
  return isNaN(n) ? null : n
}

/**
 * Normalise a raw backend transaction (which may use Chinese field names or
 * English equivalents) into the canonical shape expected by the component.
 * Safe to call on already-normalised objects – existing values are preserved.
 */
function normalizeRow(t: BankTransaction, filename?: string): BankTransaction {
  const raw = t as Record<string, any>
  const rawDep    = pickVal(raw, ['存入', 'received', 'deposit'])
  const rawWit    = pickVal(raw, ['提取', 'spent', 'withdrawal'])
  const rawBal    = pickVal(raw, ['原幣結餘', 'balance', '結餘', '结余'])
  const rawAcc    = pickVal(raw, ['賬戶類型', '帳戶類型', '账户类型', 'account_type'])
  const rawDate   = pickVal(raw, ['日期', 'date', 'transaction_date', 'bank_date'])
  const rawDesc   = pickVal(raw, ['備註', 'description', 'memo', 'description_raw'])
  const rawCurr   = pickVal(raw, ['幣別', 'currency'])
  const pageNum   = raw['_page']

  const depNum = (t.deposit  !== null && t.deposit  !== undefined) ? t.deposit  : toNum(rawDep)
  const witNum = (t.withdrawal !== null && t.withdrawal !== undefined) ? t.withdrawal : toNum(rawWit)
  const balRaw = toNum(rawBal)
  const balNum = (t.balance  !== null && t.balance  !== undefined) ? t.balance  : (balRaw ?? undefined)

  return {
    ...t,
    id_number:      t.id_number  || pickVal(raw, ['reference', '憑證號', 'voucher_no']) || '',
    matched_id:     t.matched_id ?? '',
    date:           rawDate || '',
    source_file:    formatBankSourceFile(filename || t.source_file || '', pageNum, t.source_file),
    account_type:   normalizeAccountType(t.account_type || rawAcc || '') || 'HKD CURRENT',
    account_number: t.account_number || raw['account_number'] || '',
    deposit:        depNum,
    withdrawal:     witNum,
    balance:        balNum,
    particulars:    t.particulars || rawDesc || '',
    currency:       t.currency    || rawCurr || 'HKD',
    account_code:   t.account_code || pickVal(raw, ['account_code', '科目代碼']) || '',
    category:       t.category || pickVal(raw, ['category', '分類']) || '',
  }
}

function getDate(r: BankTransaction): string {
  return r.date ?? r.transaction_date ?? ''
}

function getParticulars(r: BankTransaction): string {
  return r.particulars ?? r.description ?? ''
}

function fmtAmt(val: number | null | undefined): string {
  if (val === null || val === undefined) return ''
  return Number(val).toLocaleString('en-HK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function parseAmt(s: string): number | null {
  if (!s || s.trim() === '') return null
  const n = parseFloat(s.replace(/,/g, ''))
  return isNaN(n) ? null : n
}

function toClean(r: Row): BankTransaction {
  const { _id, ...rest } = r
  void _id
  return rest
}

/**
 * Re-computes running balance for every row in order.
 * balance[i] = balance[i-1] + deposit[i] - withdrawal[i]
 * The starting balance is 0 (opening balance).
 */
function recalcBalances(rows: Row[]): Row[] {
  let running = 0
  return rows.map(r => {
    running = Math.round((running + (r.deposit ?? 0) - (r.withdrawal ?? 0)) * 100) / 100
    return { ...r, balance: running }
  })
}

const ACCOUNT_TYPES_VALID = [...BANK_ACCOUNT_TYPES_VALID]

function normalizeAccountType(raw: string): string {
  const normalized = normalizeBankAccountType(raw)
  if (normalized) return normalized
  return (raw || '').trim() ? '' : 'HKD CURRENT'
}

function rowSourceStem(row: BankTransaction): string {
  return bankSourceFileStem(row.source_file)
}

function accountSectionKey(row: BankTransaction): string {
  return `${row.account_type ?? ''}|${row.account_number ?? ''}`
}

function fileSeparatorLabel(row: BankTransaction, filename?: string): string {
  const stem = rowSourceStem(row)
  return stem || filename || 'Statement'
}

function syntaxHighlight(json: string): string {
  return json.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g,
    (match) => {
      if (/^"/.test(match)) {
        if (/:$/.test(match)) return `<span style="color:#89b4fa">${match}</span>`
        return `<span style="color:#a6e3a1">${match}</span>`
      }
      if (/null/.test(match)) return `<span style="color:#6c7086">${match}</span>`
      return `<span style="color:#fab387">${match}</span>`
    }
  )
}

function downloadBlob(filename: string, content: string, type: string) {
  const blob = new Blob(['\uFEFF' + content], { type })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
  URL.revokeObjectURL(a.href)
}

// ─── Component ────────────────────────────────────────────────────────────────

export function BankStatementReview({
  transactions,
  filename,
  processingTime,
  coaOptions = [],
  onDeploy,
  onDataChange,
  reconState,
  onUnlock,
  isCashTable,
  glPostedBankLockKeys,
  glVoucherNoByGroupId = {},
  readOnly = false,
  onApprove,
  canApprove = false,
  approveBusy = false,
}: Props) {
  const prepareRows = useCallback(
    (source: BankTransaction[]) => {
      const coalesced = coalesceBankAccountTypeRows(
        source.map(t => ({ ...(t as Record<string, unknown>) })),
      ) as BankTransaction[]
      return coalesced.map((t, i) => ({ ...normalizeRow(t, filename), _id: i }))
    },
    [filename],
  )

  const [rows, setRows] = useState<Row[]>(() => {
    const normalized = prepareRows(transactions)
    return isCashTable ? recalcBalances(normalized) : normalized
  })
  const [selected, setSelected] = useState<Set<number>>(new Set())

  // Re-sync rows when the parent updates the transactions prop (e.g. after Deploy Codes)
  useEffect(() => {
    const normalized = prepareRows(transactions)
    setRows(isCashTable ? recalcBalances(normalized) : normalized)
  }, [filename, isCashTable, prepareRows, transactions])

  const emit = useCallback((next: Row[]) => {
    onDataChange?.(next.map(toClean))
  }, [onDataChange])

  function updateRows(next: Row[]) {
    setRows(next)
    emit(next)
  }

  // (C) Returns true if the row is currently matched/locked
  function isLocked(row: Row): boolean {
    const key = row.id_number || (row as any).reference || ''
    return reconState?.[key]?.status === 'matched'
  }

  const glLockKeys = glPostedBankLockKeys ?? EMPTY_LOCK_KEYS
  function isGlPostedRow(row: Row): boolean {
    return isBankRowGlPosted(row, glLockKeys)
  }

  function isDuplicate(row: Row): boolean {
    return row._duplicateConfirmed === true
  }

  function getDuplicateLevel(row: Row): number | undefined {
    return row._duplicateLevel
  }

  // (C) Confirm + unlock before allowing edits to crucial fields
  const LOCK_GUARDED_FIELDS = ['deposit', 'withdrawal', 'date', 'transaction_date']

  const coaNameByCode = useMemo(() => coaNameMapFromOptionLabels(coaOptions), [coaOptions])
  const coaCodeSet = useMemo(
    () => new Set(coaOptions.map(opt => String(opt).trim().split(/\s+/)[0]).filter(Boolean)),
    [coaOptions],
  )

  function updateAccountCode(id: number, code: string) {
    if (readOnly) return
    const row = rows.find(r => r._id === id)
    if (row && isGlPostedRow(row)) return
    const category = code ? coaNameByCode.get(code) || '' : ''
    updateRows(rows.map(r => (r._id === id ? { ...r, account_code: code, category } : r)))
  }

  function updateField(id: number, field: string, value: any) {
    if (readOnly) return
    const row = rows.find(r => r._id === id)
    if (row && isGlPostedRow(row)) return
    if (row && isLocked(row) && LOCK_GUARDED_FIELDS.includes(field)) {
      const ok = window.confirm('This transaction is matched. Editing will unmatch it and move it back to unmatched. Continue?')
      if (!ok) return
      onUnlock?.(row.id_number ?? '')
    }
    if (field === 'account_code') {
      updateAccountCode(id, String(value ?? ''))
      return
    }
    let next = rows.map(r => r._id === id ? { ...r, [field]: value } : r)
    if (isCashTable && (field === 'deposit' || field === 'withdrawal')) {
      next = recalcBalances(next)
    }
    updateRows(next)
  }

  function addRow() {
    if (readOnly) return
    const newId = rows.length > 0 ? Math.max(...rows.map(r => r._id)) + 1 : 0
    const newRow: Row = {
      _id: newId,
      account_type: isCashTable ? 'CASH' : 'HKD CURRENT',
      account_number: '',
      date: new Date().toISOString().slice(0, 10),
      particulars: '',
      deposit: null,
      withdrawal: null,
      balance: 0,
      currency: 'HKD',
      account_code: '',
      category: '',
    }
    if (selected.size === 0) {
      const next = isCashTable ? recalcBalances([...rows, newRow]) : [...rows, newRow]
      updateRows(next)
      return
    }
    // Insert after the last selected row (Excel-style)
    const lastSelectedId = Math.max(...Array.from(selected))
    const insertAfterIdx = rows.findIndex(r => r._id === lastSelectedId)
    const spliced = [...rows]
    spliced.splice(insertAfterIdx + 1, 0, newRow)
    const next = isCashTable ? recalcBalances(spliced) : spliced
    updateRows(next)
    setSelected(new Set([newId]))
  }

  function deleteSelected() {
    if (readOnly) return
    if (selected.size === 0) return
    const selectedRows = rows.filter(r => selected.has(r._id))
    const glPostedSelected = selectedRows.filter(r => isGlPostedRow(r))
    if (glPostedSelected.length > 0) {
      window.alert(`${glPostedSelected.length} selected row(s) are posted to the GL and cannot be deleted. Deselect them and try again.`)
      return
    }
    const lockedSelected = selectedRows.filter(r => isLocked(r))
    if (lockedSelected.length > 0) {
      const ok = window.confirm(`${lockedSelected.length} selected row(s) are matched. Deleting will unmatch them and move them back to unmatched. Continue?`)
      if (!ok) return
      lockedSelected.forEach(r => onUnlock?.(r.id_number ?? ''))
    }
    let next = rows.filter(r => !selected.has(r._id))
    if (isCashTable) next = recalcBalances(next)
    updateRows(next)
    setSelected(new Set())
  }

  function toggleSelect(id: number, multi: boolean) {
    setSelected(prev => {
      const next = new Set(prev)
      if (multi) {
        if (next.has(id)) next.delete(id); else next.add(id)
      } else {
        if (next.size === 1 && next.has(id)) next.clear(); else { next.clear(); next.add(id) }
      }
      return next
    })
  }

  function exportCSV() {
    const headers = ['id_number', 'matched_id', 'source_file', 'date', 'account_type', 'account_number', 'deposit', 'withdrawal', 'balance', 'account_code', 'category', 'particulars']
    const headerLine = headers.join(',')

    function csvCell(v: any): string {
      const str = String(v ?? '')
      return str.includes(',') || str.includes('"') || str.includes('\n')
        ? `"${str.replace(/"/g, '""')}"` : str
    }

    const lines: string[] = []
    rows.forEach((r, i) => {
      const prev = i > 0 ? rows[i - 1] : null
      const showFileSep = i === 0 || rowSourceStem(r) !== rowSourceStem(prev!)
      const showAccountSep =
        showFileSep || !prev || accountSectionKey(r) !== accountSectionKey(prev)
      if (showFileSep) {
        if (i > 0) lines.push('')
        lines.push(`"=== ${fileSeparatorLabel(r, filename)} ==="`)
      }
      if (showAccountSep) {
        const acctLabel = [r.account_type || 'Unknown', r.account_number].filter(Boolean).join(' · ')
        lines.push(`"--- ${acctLabel} ---"`)
        lines.push(headerLine)
      } else if (showFileSep) {
        lines.push(headerLine)
      }
      lines.push(headers.map(h => {
        let v: any
        if (h === 'date') v = getDate(r)
        else if (h === 'particulars') v = getParticulars(r)
        else if (h === 'source_file') v = r.source_file ?? ''
        else v = r[h] ?? ''
        return csvCell(v)
      }).join(','))
    })
    // If no rows produced any section headers (empty table), still output header
    if (lines.length === 0) lines.push(headerLine)

    const stem = (filename ?? 'bank').replace(/\.pdf$/i, '')
    downloadBlob(`${stem}_transactions.csv`, lines.join('\n'), 'text/csv')
  }

  function exportJSON() {
    const clean = rows.map(toClean)
    const stem = (filename ?? 'bank').replace(/\.pdf$/i, '')
    downloadBlob(`${stem}_transactions.json`, JSON.stringify(clean, null, 2), 'application/json')
  }

  // Status bar totals — grouped dynamically by whatever account_type values exist
  const accountTypeSummary = rows.reduce<Record<string, { count: number; deposit: number; withdrawal: number }>>(
    (acc, r) => {
      const t = r.account_type || 'Unknown'
      if (!acc[t]) acc[t] = { count: 0, deposit: 0, withdrawal: 0 }
      acc[t].count += 1
      acc[t].deposit += r.deposit ?? 0
      acc[t].withdrawal += r.withdrawal ?? 0
      return acc
    },
    {}
  )
  const totalDeposit = rows.reduce((s, r) => s + (r.deposit ?? 0), 0)
  const totalWithdrawal = rows.reduce((s, r) => s + (r.withdrawal ?? 0), 0)

  const { isMobile } = useViewport()
  const S = useMemo(() => resolveBankStyles(isMobile), [isMobile])

  const ACCOUNT_TYPES = ACCOUNT_TYPES_VALID

  return (
    <div className="bank-review-wrapper" style={S.wrapper}>
      {/* ── Header ── */}
      <div style={S.header}>
        <div style={S.headerTitle}>{isCashTable ? 'Cash records' : 'Bank statement review'}</div>
        <div style={S.headerMeta}>
          {filename && <span>{filename}</span>}
          {processingTime && <span style={{ marginLeft: 12 }}>Processing time: {processingTime}</span>}
          {isCashTable && <span style={{ marginLeft: 12, fontSize: 11, background: 'rgba(255,255,255,0.2)', padding: '2px 8px', borderRadius: 10 }}>Balance auto-calculated</span>}
        </div>
      </div>

      {/* ── Toolbar ── */}
      <div style={S.toolbar}>
        {!readOnly && <button style={S.btn} onClick={addRow}>+ Add row</button>}
        {!readOnly && (
          <button
            style={{ ...S.btn, opacity: selected.size === 0 ? 0.5 : 1 }}
            onClick={deleteSelected}
            disabled={selected.size === 0}
          >
            Delete selected
          </button>
        )}
        <button style={S.btnPrimary} onClick={exportCSV}>Export CSV</button>
        {onApprove && (
          <button
            style={{ ...S.btnPrimary, opacity: !canApprove || approveBusy ? 0.5 : 1 }}
            onClick={onApprove}
            disabled={!canApprove || approveBusy}
            title="Approve the table and transfer it to the destination module"
          >
            {approveBusy ? 'Approving...' : 'Approve'}
          </button>
        )}
        {!readOnly && onDeploy && (
          <button style={S.btnDeploy} onClick={onDeploy} title="Use AI to assign Chart of Accounts codes to all rows">
            Deploy Codes
          </button>
        )}
        <div style={{ flex: 1 }} />
        <div style={S.rowCount}>{rows.length} transaction{rows.length === 1 ? '' : 's'}</div>
      </div>

      {/* ── Table ── */}
      <div style={S.tableContainer}>
        <table style={S.table}>
          <thead>
            <tr style={S.theadRow}>
              <th style={{ ...S.th, width: 36 }}>#</th>
              <th style={{ ...S.th, minWidth: 110 }}>ID</th>
              <th style={{ ...S.th, minWidth: 90, color: '#999' }}>Match ID</th>
              <th style={{ ...S.th, minWidth: 130 }}>Source page</th>
              <th style={S.th}>Date</th>
              <th style={S.th}>Account type</th>
              <th style={S.th}>Account no.</th>
              <th style={{ ...S.th, textAlign: 'right' }}>Deposit (HKD)</th>
              <th style={{ ...S.th, textAlign: 'right' }}>Withdrawal (HKD)</th>
              <th style={{ ...S.th, textAlign: 'right' }}>Balance (HKD)</th>
              <th style={{ ...S.th, minWidth: 130 }}>GL code</th>
              <th style={{ ...S.th, minWidth: 110 }}>Category</th>
              <th style={{ ...S.th, minWidth: 72, textAlign: 'center' }}>AR review</th>
              <th style={{ ...S.th, minWidth: 300 }}>Memo / description</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const isSelected = selected.has(row._id)
              const locked = isLocked(row)
              const glPosted = isGlPostedRow(row)
              const dup = isDuplicate(row)
              const dupLevel = getDuplicateLevel(row)
              const extractionFlags: string[] = Array.isArray((row as Record<string, unknown>).validation_flags)
                ? ((row as Record<string, unknown>).validation_flags as string[]).map(String)
                : []
              const needsReview =
                (row as Record<string, unknown>).needs_manual_review === true
                || (row as Record<string, unknown>).needs_review === true
                || extractionFlags.length > 0
              const exclusionReasons: string[] = Array.isArray((row as Record<string, unknown>).exclusion_reasons)
                ? (row as Record<string, unknown>).exclusion_reasons as string[]
                : []
              const reviewBankTitle = [...exclusionReasons, ...extractionFlags].filter(Boolean).join(' | ')
              const rowBg = isSelected ? '#e8f0fe' : needsReview ? '#fff5f5' : dup ? '#fafafa' : glPosted ? '#fffbeb' : locked ? '#f0fdf4' : undefined
              const dupRowStyle = dup ? { opacity: 0.35, textDecoration: 'line-through' as const, pointerEvents: 'none' as const } : {}
              const prev = i > 0 ? rows[i - 1] : null
              const showFileSeparator = i === 0 || rowSourceStem(row) !== rowSourceStem(prev!)
              const showAccountSeparator =
                showFileSeparator || !prev || accountSectionKey(row) !== accountSectionKey(prev)
              return (
                <React.Fragment key={row._id}>
                  {showFileSeparator && (
                    <tr>
                      <td colSpan={13} style={S.fileSeparator}>
                        {fileSeparatorLabel(row, filename)}
                      </td>
                    </tr>
                  )}
                  {showAccountSeparator && (
                    <tr>
                      <td colSpan={13} style={S.accountSeparator}>
                        <span style={S.accountSeparatorLabel}>
                          {row.account_type || 'Unknown account'}
                        </span>
                        {row.account_number && (
                          <span style={S.accountSeparatorAcctNo}>
                            Acct:&nbsp;{row.account_number}
                          </span>
                        )}
                      </td>
                    </tr>
                  )}
                <tr
                  style={{ ...S.tbodyRow, background: rowBg, ...dupRowStyle, ...(needsReview ? { borderLeft: '3px solid #ef4444' } : {}) }}
                  onClick={e => !dup && toggleSelect(row._id, e.ctrlKey || e.metaKey)}
                >
                  {/* # — shows GL posted / lock / dup / review */}
                  <td style={{ ...S.rowNum, ...(glPosted ? { background: '#fde68a' } : {}), ...(locked && !glPosted ? { background: '#dcfce7' } : {}), ...(dup ? { background: '#fef2f2' } : {}), ...(needsReview ? { background: '#fff5f5' } : {}) }}>
                    {needsReview && !dup
                      ? <span title={reviewBankTitle || 'Needs manual review'} style={{ fontSize: 9, display: 'block', textAlign: 'center', lineHeight: 1, color: '#ef4444', fontWeight: 700 }}>REVIEW</span>
                      : dup
                      ? <span title={`Duplicate (L${dupLevel})`} style={{ fontSize: 10, display: 'block', textAlign: 'center', lineHeight: 1, color: '#dc2626', fontWeight: 700 }}>DUP</span>
                      : glPosted
                      ? <span title="Posted to GL — read-only" style={{ fontSize: 10, display: 'block', textAlign: 'center', lineHeight: 1, color: '#92400e', fontWeight: 700 }}>GL</span>
                      : locked
                      ? <span title="Matched lock — editing will unmatch" style={{ fontSize: 10, display: 'block', textAlign: 'center', lineHeight: 1, color: '#6b7280' }}>LCK</span>
                      : i + 1
                    }
                  </td>

                  {/* ID號碼 */}
                  <td style={S.td}>
                    <input
                      style={{ ...S.input, fontFamily: "'Courier New', monospace", fontSize: 11 }}
                      value={row.id_number ?? ''}
                      disabled={glPosted || readOnly}
                      onChange={e => updateField(row._id, 'id_number', e.target.value)}
                      onClick={e => e.stopPropagation()}
                    />
                  </td>

                  {/* 配對ID — read-only; group matches show GL-000006 when draft is loaded */}
                  <td style={{ ...S.td, background: locked ? '#dcfce7' : '#fafafa' }}>
                    <span
                      title={row.matched_id ? String(row.matched_id) : undefined}
                      style={{ display: 'block', padding: '7px 10px', fontSize: 11, fontFamily: "'Courier New', monospace", color: locked ? '#166534' : '#999', whiteSpace: 'nowrap' }}
                    >
                      {row.matched_id
                        ? formatMatchedIdForDisplay(row.matched_id, glVoucherNoByGroupId)
                        : '—'}
                    </span>
                  </td>

                  <td style={{ ...S.td, fontSize: 11, color: '#555', whiteSpace: 'nowrap' }}>
                    {row.source_file ?? ''}
                  </td>

                  <td style={S.td}>
                    <input
                      type="date"
                      style={S.input}
                      value={getDate(row)}
                      disabled={glPosted || readOnly}
                      onChange={e => updateField(row._id, 'date', e.target.value)}
                      onClick={e => e.stopPropagation()}
                    />
                  </td>

                  <td style={S.td} onClick={e => e.stopPropagation()}>
                    <select
                      style={S.select}
                      value={row.account_type ?? ''}
                      disabled={glPosted || readOnly}
                      onChange={e => updateField(row._id, 'account_type', e.target.value)}
                    >
                      {[...ACCOUNT_TYPES, ...(row.account_type && !ACCOUNT_TYPES.includes(row.account_type) ? [row.account_type] : [])].map(t => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </td>

                  <td style={S.td}>
                    <input
                      style={S.input}
                      value={row.account_number ?? ''}
                      disabled={glPosted || readOnly}
                      onChange={e => updateField(row._id, 'account_number', e.target.value)}
                      onClick={e => e.stopPropagation()}
                    />
                  </td>

                  <td style={S.tdAmt}>
                    <input
                      type="number"
                      step="0.01"
                      style={S.inputAmt}
                      value={row.deposit ?? ''}
                      placeholder="—"
                      disabled={glPosted || readOnly}
                      onChange={e => updateField(row._id, 'deposit', parseAmt(e.target.value))}
                      onClick={e => e.stopPropagation()}
                    />
                    {row.deposit != null && (
                      <span style={S.fmtDeposit}>{fmtAmt(row.deposit)}</span>
                    )}
                  </td>

                  <td style={S.tdAmt}>
                    <input
                      type="number"
                      step="0.01"
                      style={S.inputAmt}
                      value={row.withdrawal ?? ''}
                      placeholder="—"
                      disabled={glPosted || readOnly}
                      onChange={e => updateField(row._id, 'withdrawal', parseAmt(e.target.value))}
                      onClick={e => e.stopPropagation()}
                    />
                    {row.withdrawal != null && (
                      <span style={S.fmtWithdrawal}>{fmtAmt(row.withdrawal)}</span>
                    )}
                  </td>

                  <td style={{ ...S.tdAmt, ...(isCashTable ? { background: '#f8faff' } : {}) }}>
                    {isCashTable ? (
                      <span
                        style={{
                          ...S.fmtBalance,
                          display: 'block',
                          color: (row.balance ?? 0) < 0 ? '#dc2626' : '#111827',
                        }}
                        title="Balance auto-calculated"
                      >
                        {fmtAmt(row.balance ?? 0)}
                      </span>
                    ) : (
                      <>
                        <input
                          type="number"
                          step="0.01"
                          style={S.inputAmt}
                          value={row.balance ?? ''}
                          disabled={glPosted || readOnly}
                          onChange={e => updateField(row._id, 'balance', parseAmt(e.target.value) ?? 0)}
                          onClick={e => e.stopPropagation()}
                        />
                        {row.balance != null && (
                          <span style={S.fmtBalance}>{fmtAmt(row.balance)}</span>
                        )}
                      </>
                    )}
                  </td>

                  {/* GL code — full company CoA */}
                  <td
                    style={S.td}
                    onClick={e => e.stopPropagation()}
                    title={glPosted ? 'Posted to GL — read-only' : undefined}
                  >
                    {(() => {
                      const selectValue =
                        row.account_code && coaCodeSet.has(String(row.account_code).trim())
                          ? String(row.account_code).trim()
                          : ''
                      return coaOptions.length > 0 ? (
                        <select
                          style={{ ...S.select, maxWidth: 130, minWidth: 100, color: selectValue ? '#2563eb' : '#9ca3af' }}
                          value={selectValue}
                          disabled={glPosted || readOnly}
                          onChange={e => updateAccountCode(row._id, e.target.value)}
                        >
                          <option value="">Select account</option>
                          {coaOptions.map(opt => <option key={opt} value={opt.split(' ')[0]}>{opt}</option>)}
                        </select>
                      ) : (
                        <span style={{ ...S.input, color: '#9ca3af', display: 'block' }}>—</span>
                      )
                    })()}
                  </td>

                  {/* Category — read-only CoA account name */}
                  <td style={S.td}>
                    <span style={{ ...S.input, display: 'block', color: '#374151', background: '#f9fafb' }}>
                      {(() => {
                        const code =
                          row.account_code && coaCodeSet.has(String(row.account_code).trim())
                            ? String(row.account_code).trim()
                            : ''
                        return (code ? coaNameByCode.get(code) || '' : '') || '—'
                      })()}
                    </span>
                  </td>

                  <td style={{ ...S.td, textAlign: 'center', fontSize: 11 }}>
                    {(() => {
                      const r = row as BankTransaction
                      const st = r._ar_manager_status
                      if (st === 'verified') {
                        return (
                          <span
                            title={'\u4e8c\u6b21\u6a21\u578b AR \u5df2\u8986\u6838\u901a\u904e'}
                            style={{
                              display: 'inline-block',
                              fontSize: 16,
                              lineHeight: 1,
                              color: '#16a34a',
                              fontWeight: 700,
                            }}
                          >
                            {'\u2713'}
                          </span>
                        )
                      }
                      if (st === 'needs_review') {
                        return (
                          <span
                            title={'\u8acb\u4eba\u5de5\u6838\u5c0d\uff08\u8207\u6b21\u8981\u6a21\u578b\u5217\u6578\u4e0d\u4e00\u81f4\uff09'}
                            style={{
                              display: 'inline-block',
                              fontSize: 16,
                              lineHeight: 1,
                              color: '#dc2626',
                              fontWeight: 700,
                            }}
                          >
                            {'\u2717'}
                          </span>
                        )
                      }
                      if (st === 'error') {
                        return (
                          <span
                            title={'\u8986\u6838\u904e\u7a0b\u5931\u6557\uff0c\u8acb\u4eba\u5de5\u6838\u5c0d'}
                            style={{
                              display: 'inline-block',
                              fontSize: 16,
                              lineHeight: 1,
                              color: '#dc2626',
                              fontWeight: 700,
                            }}
                          >
                            {'\u2717'}
                          </span>
                        )
                      }
                      if (r._ar_manager_added) {
                        return (
                          <span
                            title="AR secondary model added this row"
                            style={{
                              display: 'inline-block',
                              padding: '2px 8px',
                              borderRadius: 6,
                              background: '#ede9fe',
                              color: '#5b21b6',
                              fontWeight: 600,
                            }}
                          >
                            Added
                          </span>
                        )
                      }
                      if (r._ar_manager_amended) {
                        return (
                          <span
                            title={
                              Array.isArray(r._ar_manager_fields)
                                ? r._ar_manager_fields.join(', ')
                                : 'AR manager amended fields'
                            }
                            style={{
                              display: 'inline-block',
                              padding: '2px 8px',
                              borderRadius: 6,
                              background: '#dbeafe',
                              color: '#1d4ed8',
                              fontWeight: 600,
                            }}
                          >
                            Yes
                          </span>
                        )
                      }
                      return null
                    })()}
                  </td>

                  {/* 備註 / 交易描述 */}
                  <td style={S.td}>
                    <input
                      style={S.input}
                      value={getParticulars(row)}
                      disabled={glPosted || readOnly}
                      onChange={e => updateField(row._id, 'particulars', e.target.value)}
                      onClick={e => e.stopPropagation()}
                    />
                  </td>
                </tr>
                </React.Fragment>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* ── Status bar ── */}
      <div style={S.statusBar}>
        {Object.entries(accountTypeSummary).map(([acctType, summary], idx) => {
          const dotColors = ['#3b82f6', '#22c55e', '#eab308', '#dc2626', '#9333ea', '#06b6d4']
          const dotColor = dotColors[idx % dotColors.length]
          return (
            <span key={acctType} style={S.statusItem}>
              <span style={{ ...S.dot, background: dotColor }} />
              {acctType}: {summary.count}
            </span>
          )
        })}
        <span style={S.statusItem}>Total deposits: HKD {fmtAmt(totalDeposit)}</span>
        <span style={S.statusItem}>Total withdrawals: HKD {fmtAmt(totalWithdrawal)}</span>
      </div>
    </div>
  )
}

// ─── Inline styles (no external CSS dependency) ───────────────────────────────

const styles: Record<string, CSSProperties> = {
  wrapper: {
    fontFamily: "'Inter', 'Segoe UI', Arial, sans-serif",
    background: '#f9fafb',
    color: '#111827',
    borderRadius: 12,
    overflow: 'visible',
    boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
    margin: '8px 0',
  },
  header: {
    background: '#111827',
    color: 'white',
    padding: '14px 20px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  headerTitle: { fontSize: 16, fontWeight: 600 },
  headerMeta: { fontSize: 12, opacity: 0.85 },

  toolbar: {
    background: 'white',
    borderBottom: '1px solid #e5e7eb',
    padding: '8px 20px',
    display: 'flex',
    gap: 8,
    alignItems: 'center',
  },
  btn: {
    padding: '5px 12px',
    border: '1px solid #e5e7eb',
    borderRadius: 6,
    background: 'white',
    cursor: 'pointer',
    fontSize: 12,
    color: '#374151',
  },
  btnPrimary: {
    padding: '5px 12px',
    border: '1px solid #111827',
    borderRadius: 6,
    background: '#111827',
    color: 'white',
    cursor: 'pointer',
    fontSize: 12,
  },
  btnDeploy: {
    padding: '5px 12px',
    border: '1px solid #9333ea',
    borderRadius: 6,
    background: '#9333ea',
    color: 'white',
    cursor: 'pointer',
    fontSize: 12,
  },
  rowCount: { fontSize: 12, color: '#666' },

  tableContainer: {
    padding: '16px 20px',
    overflowX: 'auto',
    width: '100%',
    maxWidth: '100%',
    boxSizing: 'border-box',
    WebkitOverflowScrolling: 'touch',
    overscrollBehaviorX: 'contain',
  } as React.CSSProperties,
  table: {
    borderCollapse: 'collapse',
    width: 'max-content',
    minWidth: '100%',
    background: 'white',
    boxShadow: '0 1px 4px rgba(0,0,0,0.08)',
    borderRadius: 6,
    fontSize: 12,
  },
  theadRow: { background: '#f9fafb', borderBottom: '2px solid #e5e7eb' },
  th: {
    padding: '9px 10px',
    textAlign: 'left',
    fontWeight: 600,
    color: '#374151',
    whiteSpace: 'nowrap',
    position: 'sticky',
    top: 0,
    zIndex: 2,
    background: '#f9fafb',
  },
  tbodyRow: {
    borderBottom: '1px solid #f0f0f0',
    cursor: 'pointer',
    transition: 'background 0.1s',
  },
  rowNum: {
    width: 36,
    textAlign: 'center',
    color: '#aaa',
    fontSize: 11,
    padding: '6px 4px',
    background: '#fafafa',
    borderRight: '1px solid #f0f0f0',
  },
  td: { padding: 0, verticalAlign: 'middle' },
  tdAmt: { padding: 0, verticalAlign: 'middle', position: 'relative' },
  input: {
    width: '100%',
    padding: '7px 10px',
    border: 'none',
    background: 'transparent',
    fontSize: 12,
    fontFamily: 'inherit',
    color: '#333',
    outline: 'none',
  },
  select: {
    padding: '7px 6px',
    border: 'none',
    background: 'transparent',
    fontSize: 11,
    fontFamily: 'inherit',
    color: '#333',
    outline: 'none',
    maxWidth: 80,
  },
  inputAmt: {
    width: '100%',
    padding: '7px 10px',
    border: 'none',
    background: 'transparent',
    fontSize: 12,
    fontFamily: "'Courier New', monospace",
    textAlign: 'right',
    color: 'transparent',
    outline: 'none',
    position: 'absolute',
    top: 0, left: 0, right: 0, bottom: 0,
    zIndex: 1,
  },
  fmtDeposit: {
    display: 'block',
    padding: '7px 10px',
    fontFamily: "'Courier New', monospace",
    fontSize: 12,
    color: '#2563eb',
    textAlign: 'right',
    pointerEvents: 'none',
  },
  fmtWithdrawal: {
    display: 'block',
    padding: '7px 10px',
    fontFamily: "'Courier New', monospace",
    fontSize: 12,
    color: '#dc2626',
    textAlign: 'right',
    pointerEvents: 'none',
  },
  fmtBalance: {
    display: 'block',
    padding: '7px 10px',
    fontFamily: "'Courier New', monospace",
    fontSize: 12,
    color: '#333',
    textAlign: 'right',
    pointerEvents: 'none',
  },
  badgeCurrent: {
    display: 'inline-block',
    padding: '1px 6px',
    borderRadius: 10,
    fontSize: 10,
    fontWeight: 600,
    background: '#eff6ff',
    color: '#2563eb',
    marginLeft: 4,
    verticalAlign: 'middle',
  },
  badgeSavings: {
    display: 'inline-block',
    padding: '1px 6px',
    borderRadius: 10,
    fontSize: 10,
    fontWeight: 600,
    background: '#e6f4ea',
    color: '#188038',
    marginLeft: 4,
    verticalAlign: 'middle',
  },
  badgeCash: {
    display: 'inline-block',
    padding: '1px 6px',
    borderRadius: 10,
    fontSize: 10,
    fontWeight: 600,
    background: '#fef3c7',
    color: '#92400e',
    marginLeft: 4,
    verticalAlign: 'middle',
  },
  badgeFcy: {
    display: 'inline-block',
    padding: '1px 6px',
    borderRadius: 10,
    fontSize: 10,
    fontWeight: 600,
    background: '#ede9fe',
    color: '#5b21b6',
    marginLeft: 4,
    verticalAlign: 'middle',
  },

  jsonPanel: {
    margin: '0 0 0',
    background: '#1e1e2e',
    borderRadius: 0,
    overflow: 'hidden',
  },
  jsonHeader: {
    background: '#2d2d3f',
    padding: '8px 20px',
    color: '#cdd6f4',
    fontSize: 12,
    fontWeight: 600,
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  toggleBtn: {
    fontSize: 11,
    cursor: 'pointer',
    color: '#89b4fa',
    border: 'none',
    background: 'none',
  },
  jsonPre: {
    padding: '12px 20px',
    color: '#cdd6f4',
    fontSize: 11,
    lineHeight: 1.6,
    overflowX: 'auto',
    maxHeight: 420,
    overflowY: 'auto',
    margin: 0,
  },

  statusBar: {
    padding: '7px 20px',
    fontSize: 11,
    color: '#6b7280',
    background: 'white',
    borderTop: '1px solid #e5e7eb',
    display: 'flex',
    gap: 20,
    flexWrap: 'wrap',
  },
  statusItem: { display: 'flex', alignItems: 'center', gap: 5 },
  dot: { width: 8, height: 8, borderRadius: '50%', display: 'inline-block' },

  fileSeparator: {
    background: '#0f172a',
    color: '#f8fafc',
    padding: '9px 16px',
    fontSize: 13,
    fontWeight: 700,
    letterSpacing: 0.3,
    borderTop: '3px solid #2563eb',
    userSelect: 'none' as const,
  },
  accountSeparator: {
    background: '#1e293b',
    color: '#eff6ff',
    padding: '7px 16px',
    fontSize: 12,
    fontWeight: 600,
    letterSpacing: 0.4,
    borderTop: '2px solid #3b82f6',
    userSelect: 'none' as const,
  },
  accountSeparatorLabel: {
    marginRight: 16,
  },
  accountSeparatorAcctNo: {
    fontSize: 11,
    fontWeight: 400,
    opacity: 0.7,
    fontFamily: "'Courier New', monospace",
  },
}

function resolveBankStyles(isMobile: boolean): Record<string, CSSProperties> {
  if (!isMobile) return styles
  return {
    ...styles,
    wrapper: { ...styles.wrapper, margin: '6px 0' },
    header: { ...styles.header, padding: '10px 12px' },
    headerTitle: { ...styles.headerTitle, fontSize: 13 },
    headerMeta: { ...styles.headerMeta, fontSize: 10 },
    toolbar: { ...styles.toolbar, padding: '6px 8px', flexWrap: 'wrap', gap: 6 },
    btn: { ...styles.btn, padding: '4px 8px', fontSize: 10 },
    btnPrimary: { ...styles.btnPrimary, padding: '4px 8px', fontSize: 10 },
    btnDeploy: { ...styles.btnDeploy, padding: '4px 8px', fontSize: 10 },
    rowCount: { ...styles.rowCount, fontSize: 10 },
    tableContainer: { ...styles.tableContainer, padding: '8px 10px' },
    table: { ...styles.table, fontSize: 10 },
    th: { ...styles.th, padding: '6px 6px', fontSize: 10 },
    rowNum: { ...styles.rowNum, fontSize: 9, padding: '4px 2px' },
    td: { ...styles.td, fontSize: 10 },
    input: { ...styles.input, fontSize: 10, padding: '5px 6px' },
    select: { ...styles.select, fontSize: 9, padding: '5px 4px' },
    inputAmt: { ...styles.inputAmt, fontSize: 10, padding: '5px 6px' },
    fmtDeposit: { ...styles.fmtDeposit, fontSize: 10, padding: '5px 6px' },
    fmtWithdrawal: { ...styles.fmtWithdrawal, fontSize: 10, padding: '5px 6px' },
    fmtBalance: { ...styles.fmtBalance, fontSize: 10, padding: '5px 6px' },
    jsonHeader: { ...styles.jsonHeader, padding: '6px 12px', fontSize: 11 },
    jsonPre: { ...styles.jsonPre, padding: '10px 12px', fontSize: 10 },
    statusBar: { ...styles.statusBar, padding: '6px 10px', fontSize: 10, gap: 10 },
    statusItem: { ...styles.statusItem, gap: 4 },
    dot: { ...styles.dot, width: 6, height: 6 },
    fileSeparator: {
      ...styles.fileSeparator,
      padding: '6px 10px',
      fontSize: 11,
    },
    accountSeparator: {
      ...styles.accountSeparator,
      padding: '6px 10px',
      fontSize: 10,
    },
    accountSeparatorAcctNo: { ...styles.accountSeparatorAcctNo, fontSize: 10 },
  }
}
