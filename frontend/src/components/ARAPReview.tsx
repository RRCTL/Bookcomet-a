import { useState, useCallback, useEffect, useMemo } from 'react'
import type { CSSProperties } from 'react'
import type { ReconState } from '../types/reconciliation'
import { useViewport } from '../hooks/useViewport'
import { isLedgerRowGlPosted } from '../utils/glPostedOcrLock'
import { formatMatchedIdForDisplay } from '../utils/reconMatchedSpreadsheet'
import { validateRowsMovable, arapRowIdentity } from '../features/workspace/arapTableMove'
import {
  applyDebitCreditSide,
  defaultDrCr,
  hydrateDebitCredit,
  normalizeDrCr,
} from '../features/erpShell/arapDebitCredit'
import { coaNameMapFromOptionLabels } from '../utils/coaDisplay'
import {
  IMAGE_QUALITY_METRIC_KEYS,
  imageQualityChipStyle,
  readImageQuality,
} from '../utils/imageQualityUi'
import { formatBankSourceFile } from '../utils/bankSourceFile'

const EMPTY_LOCK_KEYS: ReadonlySet<string> = new Set()

// ─── Types ────────────────────────────────────────────────────────────────────

export type ARAPTransaction = {
  needs_review?: boolean
  validation_flags?: string[]
  extraction_provenance?: Record<string, unknown>
  id_number?: string        // voucher_no / reference, e.g. "AR-20250101-001"
  matched_id?: string       // set by RECON mode (update C); empty for now
  date?: string
  /** Invoice due date (AP table schema); separate from `date`. */
  due_date?: string
  invoice_number?: string
  vendor_tax_id?: string
  tax_amount?: number | null
  payment_status?: string
  source_file?: string
  transaction_type?: string // "AR" | "AP"
  amount?: number | null
  /** Bank-style sides; amount + dr_cr stay in sync. */
  debit?: number | null
  credit?: number | null
  /** Ledger side for RECON / GL ("Dr" | "Cr"). */
  dr_cr?: 'Dr' | 'Cr'
  currency?: string
  payer?: string
  payee?: string
  bank?: string
  account_code?: string     // Chart of Accounts code (assigned by AI or user)
  category?: string
  memo?: string
  confidence?: string | number
  [key: string]: any
}

type Row = ARAPTransaction & { _id: number }

interface Props {
  transactions: ARAPTransaction[]
  filename?: string
  /** Per-type CoA lists keyed by transaction_type ('AR' | 'AP'). Each row's dropdown is limited to its own type. */
  coaOptionsByType?: Record<string, string[]>
  onDeploy?: () => void         // triggered by "Deploy Codes" button
  onDataChange?: (updated: ARAPTransaction[]) => void
  reconState?: ReconState       // (C) lock map from RECON mode: id_number → {status, matched_id}
  onUnlock?: (id_number: string) => void  // (C) called when user unlocks a matched row
  /** When true, shows a live-updating banner — OCR is still processing more files */
  isProcessing?: boolean
  completedFiles?: number
  totalFiles?: number
  /** Keys for rows whose match group has a POSTED GL — row read-only */
  glPostedLedgerLockKeys?: ReadonlySet<string>
  glVoucherNoByGroupId?: Record<string, string>
  /** Same-task AR/AP cross-table move (WorkspaceApp). */
  messageId?: string
  crossTableMoveEnabled?: boolean
  onMoveToRows?: (rows: ARAPTransaction[]) => void
  /** Re-run a failed PDF page via /api/jobs/{id}/ocr-retry-page (multi-page Scenario D). */
  onRetryOcrPage?: (jobId: string, page: number) => void
  /** When true, show AP-focused columns (composer AP table / ap_table preset). */
  useApTableSchema?: boolean
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
 * Normalise a raw backend row (SpreadsheetRow or ARAPTransaction) into the
 * canonical shape expected by ARAPReview.  Safe to call on already-normalised
 * objects — existing non-null values are preserved.
 */
function normalizeARAPRow(t: ARAPTransaction, filename?: string): ARAPTransaction {
  const raw = t as Record<string, any>
  const pageNum = raw['_page']
  const fileLabel = filename?.trim() || ''

  const rawAmt  = pickVal(raw, ['amount', '金額', 'total_amount', 'amount_numeric'])
  const amtNum  = (t.amount !== null && t.amount !== undefined) ? t.amount : toNum(rawAmt)
  const rawTax  = pickVal(raw, ['tax_amount', '稅額'])
  const taxNum  = (t.tax_amount !== null && t.tax_amount !== undefined) ? t.tax_amount : toNum(rawTax)
  const txType = (t.transaction_type || pickVal(raw, ['transaction_type', '類型', '类型']) || '').toUpperCase()
  const sides = hydrateDebitCredit(
    {
      ...raw,
      amount: amtNum,
      debit: t.debit ?? raw.debit,
      credit: t.credit ?? raw.credit,
      dr_cr: t.dr_cr ?? pickVal(raw, ['dr_cr', 'debit_credit']),
      transaction_type: txType,
    },
    txType,
  )

  return {
    ...t,
    id_number:        t.id_number        || pickVal(raw, ['voucher_no', '憑證號', 'reference', 'id']) || '',
    matched_id:       t.matched_id       || '',
    date:             t.date             || pickVal(raw, ['date', '日期', 'transaction_date', 'invoice_date']) || '',
    due_date:         t.due_date         || pickVal(raw, ['due_date', '到期日']) || '',
    invoice_number:   t.invoice_number   || pickVal(raw, ['invoice_number', 'invoice_no', '發票號碼']) || '',
    vendor_tax_id:    t.vendor_tax_id    || pickVal(raw, ['vendor_tax_id', 'tax_id', '統一編號']) || '',
    tax_amount:       taxNum,
    payment_status:   t.payment_status   || pickVal(raw, ['payment_status', '付款狀態']) || '',
    source_file:      formatBankSourceFile(
      fileLabel,
      pageNum,
      pageNum != null && Number(pageNum) >= 1
        ? String(t.source_file || raw['file_position'] || '').replace(/ P\d+\b/, '')
        : (t.source_file || raw['file_position'] || ''),
    ) || fileLabel,
    transaction_type: txType,
    amount:           sides.amount,
    debit:            sides.debit,
    credit:           sides.credit,
    dr_cr:            sides.dr_cr,
    currency:         t.currency         || pickVal(raw, ['currency', '幣別']) || 'HKD',
    payer:            t.payer            || pickVal(raw, ['payer', '付款人']) || '',
    payee:            t.payee            || pickVal(raw, ['payee', '收款人', 'vendor', 'vendor_name', 'supplier', 'merchant_name']) || '',
    bank:             t.bank             || pickVal(raw, ['bank', '銀行']) || '',
    account_code:     t.account_code     || pickVal(raw, ['account_code', '科目代碼']) || '',
    category:         t.category         || pickVal(raw, ['category', 'categorise', '分類']) || '',
    memo:             t.memo             || pickVal(raw, ['memo', '備註', 'description']) || '',
    confidence:       t.confidence       || pickVal(raw, ['confidence', '信心度']) || '',
  }
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

function toClean(r: Row): ARAPTransaction {
  const { _id, ...rest } = r
  void _id
  return rest
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

export function ARAPReview({
  transactions,
  filename,
  coaOptionsByType = {},
  onDeploy,
  onDataChange,
  reconState,
  onUnlock,
  isProcessing = false,
  completedFiles = 0,
  totalFiles = 0,
  glPostedLedgerLockKeys,
  glVoucherNoByGroupId = {},
  onRetryOcrPage,
  messageId,
  crossTableMoveEnabled = false,
  onMoveToRows,
  useApTableSchema = false,
  readOnly = false,
  onApprove,
  canApprove = false,
  approveBusy = false,
}: Props) {
  const [rows, setRows] = useState<Row[]>(() =>
    transactions.map((t, i) => ({ ...normalizeARAPRow(t, filename), _id: i }))
  )
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [qualityPanelOpen, setQualityPanelOpen] = useState(true)

  // Re-sync rows when the parent updates the transactions prop.
  // Match by list index so each row keeps a unique _id (duplicate id_number must not share _id).
  useEffect(() => {
    setRows(prev => {
      let nextId = prev.length > 0 ? Math.max(...prev.map(r => r._id)) + 1 : 0
      return transactions.map((t, i) => {
        const norm = normalizeARAPRow(t, filename)
        const existing = prev[i]
        if (existing) {
          return { ...existing, ...norm, _id: existing._id }
        }
        const row: Row = { ...norm, _id: nextId }
        nextId += 1
        return row
      })
    })
  }, [transactions, filename])

  const emit = useCallback((next: Row[]) => {
    onDataChange?.(next.map(toClean))
  }, [onDataChange])

  function updateRows(next: Row[]) {
    setRows(next)
    emit(next)
  }

  // (C) Returns true if the row is currently matched/locked
  function isLocked(row: Row): boolean {
    return reconState?.[row.id_number ?? '']?.status === 'matched'
  }

  const glLockKeys = glPostedLedgerLockKeys ?? EMPTY_LOCK_KEYS
  function isGlPostedRow(row: Row): boolean {
    return isLedgerRowGlPosted(row, glLockKeys)
  }

  // (C) Confirm + unlock before allowing edits to crucial fields
  const LOCK_GUARDED_FIELDS = ['amount', 'debit', 'credit', 'date', 'due_date', 'dr_cr', 'transaction_type']

  const reconCoaOptions = useMemo(() => {
    const ar = coaOptionsByType['AR'] ?? []
    const ap = coaOptionsByType['AP'] ?? []
    return Array.from(new Set([...ar, ...ap]))
  }, [coaOptionsByType])

  const coaNameByCode = useMemo(
    () => coaNameMapFromOptionLabels(reconCoaOptions),
    [reconCoaOptions],
  )

  const coaCodeSet = useMemo(
    () => new Set(reconCoaOptions.map(opt => String(opt).trim().split(/\s+/)[0]).filter(Boolean)),
    [reconCoaOptions],
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
      const ok = window.confirm('This transaction is matched. Editing it will unmatch it and move it back to the unmatched list. Continue?')
      if (!ok) return
      onUnlock?.(row.id_number ?? '')
    }
    if (field === 'account_code') {
      updateAccountCode(id, String(value ?? ''))
      return
    }
    const next = rows.map(r => {
      if (r._id !== id) return r
      if (field === 'transaction_type') {
        const tx = String(value ?? '').toUpperCase()
        const side = defaultDrCr(tx)
        const mag = r.amount != null ? Math.abs(Number(r.amount)) : null
        const sides = hydrateDebitCredit(
          { amount: mag, dr_cr: side, transaction_type: tx },
          tx,
        )
        return { ...r, transaction_type: tx, ...sides }
      }
      if (field === 'debit' || field === 'credit') {
        return { ...r, ...applyDebitCreditSide(field, value as number | null) }
      }
      if (field === 'amount') {
        const side = normalizeDrCr(r.dr_cr, String(r.transaction_type ?? ''))
        const mag = value == null ? null : Math.abs(Number(value))
        return {
          ...r,
          ...hydrateDebitCredit(
            { amount: mag, dr_cr: side, transaction_type: r.transaction_type },
            String(r.transaction_type ?? ''),
          ),
        }
      }
      return { ...r, [field]: value }
    })
    updateRows(next)
  }

  function addRow() {
    if (readOnly) return
    const newId = rows.length > 0 ? Math.max(...rows.map(r => r._id)) + 1 : 0
    const newRow: Row = {
      _id: newId,
      id_number: '',
      matched_id: '',
      date: new Date().toISOString().slice(0, 10),
      due_date: '',
      invoice_number: '',
      vendor_tax_id: '',
      tax_amount: null,
      payment_status: '',
      source_file: '',
      transaction_type: useApTableSchema ? 'AP' : 'AR',
      amount: null,
      debit: null,
      credit: null,
      dr_cr: useApTableSchema ? 'Dr' : 'Cr',
      currency: 'HKD',
      payer: '',
      payee: '',
      bank: '',
      account_code: '',
      category: '',
      memo: '',
      confidence: '',
    }
    if (selected.size === 0) {
      updateRows([...rows, newRow])
      return
    }
    const lastSelectedId = Math.max(...Array.from(selected))
    const insertAfterIdx = rows.findIndex(r => r._id === lastSelectedId)
    const next = [...rows]
    next.splice(insertAfterIdx + 1, 0, newRow)
    updateRows(next)
    setSelected(new Set([newId]))
  }

  function deleteSelected() {
    if (readOnly) return
    if (selected.size === 0) return
    const selectedRows = rows.filter(r => selected.has(r._id))
    const glPostedSelected = selectedRows.filter(r => isGlPostedRow(r))
    if (glPostedSelected.length > 0) {
      window.alert(`${glPostedSelected.length} selected transaction(s) are posted to the general ledger and cannot be deleted. Unselect those rows and try again.`)
      return
    }
    const lockedSelected = selectedRows.filter(r => isLocked(r))
    if (lockedSelected.length > 0) {
      const ok = window.confirm(`${lockedSelected.length} selected transaction(s) are matched. Deleting them will unmatch and move them back to the unmatched list. Continue?`)
      if (!ok) return
      lockedSelected.forEach(r => onUnlock?.(r.id_number ?? ''))
    }
    updateRows(rows.filter(r => !selected.has(r._id)))
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
    const headers = useApTableSchema
      ? [
          'id_number',
          'matched_id',
          'source_file',
          'date',
          'due_date',
          'invoice_number',
          'payee',
          'vendor_tax_id',
          'debit',
          'credit',
          'amount',
          'dr_cr',
          'tax_amount',
          'currency',
          'account_code',
          'category',
          'memo',
          'payment_status',
          'transaction_type',
          'confidence',
        ]
      : [
          'id_number',
          'matched_id',
          'source_file',
          'date',
          'transaction_type',
          'debit',
          'credit',
          'amount',
          'dr_cr',
          'currency',
          'payer',
          'payee',
          'bank',
          'account_code',
          'category',
          'memo',
          'confidence',
        ]
    const lines = [headers.join(',')]
    rows.forEach(r => {
      lines.push(headers.map(h => {
        const v = r[h] ?? ''
        const str = String(v)
        return str.includes(',') || str.includes('"') || str.includes('\n')
          ? `"${str.replace(/"/g, '""')}"` : str
      }).join(','))
    })
    const stem = (filename ?? 'arap').replace(/\.[^.]+$/i, '')
    downloadBlob(`${stem}_transactions.csv`, lines.join('\n'), 'text/csv')
  }

  function exportJSON() {
    const clean = rows.map(toClean)
    const stem = (filename ?? 'arap').replace(/\.[^.]+$/i, '')
    downloadBlob(`${stem}_transactions.json`, JSON.stringify(clean, null, 2), 'application/json')
  }

  // Status bar totals
  const arRows = rows.filter(r => String(r.transaction_type).toUpperCase() === 'AR')
  const apRows = rows.filter(r => String(r.transaction_type).toUpperCase() === 'AP')
  const totalAR = arRows.reduce((s, r) => s + (r.amount ?? 0), 0)
  const totalAP = apRows.reduce((s, r) => s + (r.amount ?? 0), 0)
  const qualityRows = useMemo(
    () => rows.filter(r => readImageQuality(r).present).length,
    [rows],
  )
  const selectedQualityRow = useMemo(() => {
    if (selected.size !== 1) return null
    const id = Array.from(selected)[0]
    const row = rows.find(r => r._id === id)
    if (!row) return null
    const iq = readImageQuality(row)
    return iq.present ? { row, iq } : null
  }, [rows, selected])

  const { isMobile } = useViewport()
  const S = useMemo(() => resolveARAPStyles(isMobile), [isMobile])

  const TX_TYPES = ['AR', 'AP']

  return (
    <div className="arap-review-wrapper" style={S.wrapper}>
      <style>{`
        @keyframes arapRowStaggerIn {
          from { opacity: 0; transform: translateY(6px); }
          to { opacity: 1; transform: none; }
        }
      `}</style>
      {/* ── Header ── */}
      <div style={S.header}>
        <div style={S.headerTitle}>AR / AP Review</div>
        <div style={S.headerMeta}>
          {filename && <span>{filename}</span>}
          {useApTableSchema && (
            <span style={{ marginLeft: 12, opacity: 0.85 }}>(AP columns)</span>
          )}
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
        {!readOnly && crossTableMoveEnabled && onMoveToRows && (
          <button
            type="button"
            style={{ ...S.btn, opacity: selected.size === 0 || isProcessing ? 0.5 : 1 }}
            disabled={selected.size === 0 || isProcessing}
            onClick={() => {
              const selectedRows = rows.filter((r) => selected.has(r._id)).map(toClean)
              onMoveToRows(selectedRows)
            }}
          >
            Move to…
          </button>
        )}
        <div style={{ flex: 1 }} />
        {qualityRows > 0 && (
          <div style={{ ...S.rowCount, marginRight: 12 }} title="Rows with AQ image-quality provenance">
            Image quality: {qualityRows} rows
          </div>
        )}
        <div style={S.rowCount}>{rows.length} transactions</div>
      </div>

      {/* ── Processing banner ── */}
      {isProcessing && (
        <div style={S.processingBanner}>
          <span style={S.processingDot} />
          <span>
            OCR is still processing
            {totalFiles > 0 && ` (${completedFiles} / ${totalFiles} files done)`}
            ; the table will keep updating...
          </span>
        </div>
      )}

      {/* ── Table ── */}
      <div style={S.tableContainer}>
        <table style={S.table}>
          <thead>
            <tr style={S.theadRow}>
              <th style={{ ...S.th, width: 36 }}>#</th>
              <th style={{ ...S.th, minWidth: 120 }}>ID No.</th>
              <th style={{ ...S.th, minWidth: 110, color: '#aaa' }}>Matched ID</th>
              <th style={{ ...S.th, minWidth: 120 }}>Source Page</th>
              {useApTableSchema ? (
                <>
                  <th style={S.th}>Invoice Date</th>
                  <th style={S.th}>Due Date</th>
                  <th style={{ ...S.th, minWidth: 100 }}>Invoice No.</th>
                  <th style={{ ...S.th, minWidth: 110 }}>Supplier</th>
                  <th style={{ ...S.th, minWidth: 100 }}>Supplier Tax ID</th>
                  <th style={{ ...S.th, textAlign: 'right', minWidth: 100 }}>Debit</th>
                  <th style={{ ...S.th, textAlign: 'right', minWidth: 100 }}>Credit</th>
                  <th style={{ ...S.th, textAlign: 'right', minWidth: 90 }}>Tax</th>
                  <th style={{ ...S.th, width: 55 }}>Cur</th>
                  <th style={{ ...S.th, minWidth: 130 }}>Account Code</th>
                  <th style={{ ...S.th, minWidth: 90 }}>Category</th>
                  <th style={{ ...S.th, minWidth: 200 }}>Description</th>
                  <th style={{ ...S.th, minWidth: 90 }}>Payment Status</th>
                  <th style={{ ...S.th, minWidth: 120 }}>Image quality</th>
                  <th style={{ ...S.th, width: 70 }}>Confidence</th>
                </>
              ) : (
                <>
                  <th style={S.th}>Date</th>
                  <th style={{ ...S.th, width: 70 }}>Type</th>
                  <th style={{ ...S.th, textAlign: 'right', minWidth: 100 }}>Debit</th>
                  <th style={{ ...S.th, textAlign: 'right', minWidth: 100 }}>Credit</th>
                  <th style={{ ...S.th, width: 55 }}>Cur</th>
                  <th style={{ ...S.th, minWidth: 110 }}>Payer</th>
                  <th style={{ ...S.th, minWidth: 110 }}>Payee</th>
                  <th style={{ ...S.th, minWidth: 90 }}>Bank</th>
                  <th style={{ ...S.th, minWidth: 130 }}>Account Code</th>
                  <th style={{ ...S.th, minWidth: 90 }}>Category</th>
                  <th style={{ ...S.th, minWidth: 220 }}>Memo</th>
                  <th style={{ ...S.th, minWidth: 120 }}>Image quality</th>
                  <th style={{ ...S.th, width: 70 }}>Confidence</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const isSelected = selected.has(row._id)
              const txType = String(row.transaction_type ?? '').toUpperCase()
              const isAR = txType === 'AR'
              const locked = isLocked(row)
              const glPosted = isGlPostedRow(row)
              const extractionFlags: string[] = Array.isArray(row.validation_flags) ? row.validation_flags.map(String) : []
              const needsReview = row.needs_manual_review === true || row.needs_review === true || extractionFlags.length > 0
              const exclusionReasons: string[] = Array.isArray(row.exclusion_reasons) ? row.exclusion_reasons : []
              const reviewTitle = [...exclusionReasons, ...extractionFlags].filter(Boolean).join(' | ')
              const rowBg = isSelected ? '#e6f4ea' : needsReview ? '#fff5f5' : glPosted ? '#fffbeb' : locked ? '#f0fdf4' : undefined
              const imageQuality = readImageQuality(row)
              const iqChip = imageQualityChipStyle(imageQuality.status)
              return (
                <tr
                  key={row._id}
                  draggable={Boolean(
                    crossTableMoveEnabled &&
                      messageId &&
                      selected.size > 0 &&
                      selected.has(row._id),
                  )}
                  onDragStart={(e) => {
                    if (!crossTableMoveEnabled || !messageId) {
                      e.preventDefault()
                      return
                    }
                    const selectedRows = rows.filter((r) => selected.has(r._id))
                    const clean = selectedRows.map(toClean)
                    const movable = validateRowsMovable(clean, reconState, glPostedLedgerLockKeys)
                    if (movable.ok === false) {
                      e.preventDefault()
                      window.alert(
                        movable.reason === 'recon_locked'
                          ? 'Cannot move: the selection includes reconciled (matched) rows.'
                          : 'Cannot move: the selection includes GL-posted rows.',
                      )
                      return
                    }
                    e.dataTransfer.setData(
                      'application/json',
                      JSON.stringify({
                        sourceMessageId: messageId,
                        rowIdentities: selectedRows.map((r) => arapRowIdentity(toClean(r))),
                      }),
                    )
                    e.dataTransfer.effectAllowed = 'move'
                  }}
                  style={{
                    ...S.tbodyRow,
                    background: rowBg,
                    animation: `arapRowStaggerIn 0.38s ease ${Math.min(i * 55, 900)}ms backwards`,
                    ...(needsReview ? { borderLeft: '3px solid #ef4444' } : {}),
                  }}
                  onClick={e => toggleSelect(row._id, e.ctrlKey || e.metaKey)}
                >
                  <td style={{ ...S.rowNum, ...(glPosted ? { background: '#fde68a' } : {}), ...(locked && !glPosted ? { background: '#dcfce7' } : {}), ...(needsReview ? { background: '#fff5f5' } : {}) }}>
                    {glPosted ? (
                      <span title="Posted to general ledger - read-only" style={{ fontSize: 10, display: 'block', textAlign: 'center', lineHeight: 1, color: '#92400e', fontWeight: 700 }}>GL</span>
                    ) : (
                      i + 1
                    )}
                  </td>

                  {/* ID號碼 */}
                  <td style={S.td}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      {needsReview && (
                        <span
                          title={reviewTitle || (row.needs_manual_review ? 'Manual review required' : 'Needs review')}
                          style={{
                            fontSize: 9, fontWeight: 700, color: '#fff', background: '#ef4444',
                            padding: '1px 5px', borderRadius: 4, flexShrink: 0, letterSpacing: '0.03em',
                          }}
                        >REVIEW</span>
                      )}
                      {locked && !glPosted && <span title="Matched and locked" style={{ marginRight: 2, fontSize: 10, color: '#6b7280', flexShrink: 0 }}>LCK</span>}
                      <input
                        style={{ ...S.input, fontFamily: "'Courier New', monospace", fontSize: 11 }}
                        value={row.id_number ?? ''}
                        disabled={glPosted || readOnly}
                        onChange={e => updateField(row._id, 'id_number', e.target.value)}
                        onClick={e => e.stopPropagation()}
                      />
                    </div>
                  </td>

                  {/* 配對ID — read-only; group matches show GL-000006 when draft is loaded */}
                  <td style={{ ...S.td, background: locked ? '#dcfce7' : '#fafafa' }}>
                    <span
                      title={row.matched_id ? String(row.matched_id) : undefined}
                      style={{ ...S.matchedId, color: locked ? '#166534' : '#aaa' }}
                    >
                      {row.matched_id
                        ? formatMatchedIdForDisplay(row.matched_id, glVoucherNoByGroupId)
                        : '—'}
                    </span>
                  </td>

                  {/* 來源頁面 */}
                  <td style={{ ...S.td, fontSize: 11, color: '#555', whiteSpace: 'nowrap' }}>
                    <span style={{ padding: '7px 10px', display: 'block' }}>{row.source_file ?? ''}</span>
                  </td>

                  {useApTableSchema ? (
                    <>
                      <td style={S.td}>
                        <input
                          type="date"
                          style={S.input}
                          value={row.date ?? ''}
                          disabled={glPosted || readOnly}
                          onChange={e => updateField(row._id, 'date', e.target.value)}
                          onClick={e => e.stopPropagation()}
                        />
                      </td>
                      <td style={S.td}>
                        <input
                          type="date"
                          style={S.input}
                          value={row.due_date ?? ''}
                          disabled={glPosted || readOnly}
                          onChange={e => updateField(row._id, 'due_date', e.target.value)}
                          onClick={e => e.stopPropagation()}
                        />
                      </td>
                      <td style={S.td}>
                        <input
                          style={S.input}
                          value={row.invoice_number ?? ''}
                          disabled={glPosted || readOnly}
                          onChange={e => updateField(row._id, 'invoice_number', e.target.value)}
                          onClick={e => e.stopPropagation()}
                        />
                      </td>
                      <td style={S.td}>
                        <input
                          style={S.input}
                          value={row.payee ?? ''}
                          disabled={glPosted || readOnly}
                          onChange={e => updateField(row._id, 'payee', e.target.value)}
                          onClick={e => e.stopPropagation()}
                        />
                      </td>
                      <td style={S.td}>
                        <input
                          style={S.input}
                          value={row.vendor_tax_id ?? ''}
                          disabled={glPosted || readOnly}
                          onChange={e => updateField(row._id, 'vendor_tax_id', e.target.value)}
                          onClick={e => e.stopPropagation()}
                        />
                      </td>
                      <td style={S.tdAmt}>
                        <input
                          type="number"
                          step="0.01"
                          style={S.inputAmt}
                          value={row.debit ?? ''}
                          placeholder="—"
                          disabled={glPosted || readOnly}
                          onChange={e => updateField(row._id, 'debit', parseAmt(e.target.value))}
                          onClick={e => e.stopPropagation()}
                        />
                        {row.debit != null && (
                          <span style={S.fmtAP}>{fmtAmt(row.debit)}</span>
                        )}
                      </td>
                      <td style={S.tdAmt}>
                        <input
                          type="number"
                          step="0.01"
                          style={S.inputAmt}
                          value={row.credit ?? ''}
                          placeholder="—"
                          disabled={glPosted || readOnly}
                          onChange={e => updateField(row._id, 'credit', parseAmt(e.target.value))}
                          onClick={e => e.stopPropagation()}
                        />
                        {row.credit != null && (
                          <span style={S.fmtAR}>{fmtAmt(row.credit)}</span>
                        )}
                      </td>
                      <td style={S.tdAmt}>
                        <input
                          type="number"
                          step="0.01"
                          style={S.inputAmt}
                          value={row.tax_amount ?? ''}
                          placeholder="—"
                          disabled={glPosted || readOnly}
                          onChange={e => updateField(row._id, 'tax_amount', parseAmt(e.target.value))}
                          onClick={e => e.stopPropagation()}
                        />
                        {row.tax_amount != null && (
                          <span style={{ fontSize: 10, color: '#64748b' }}>{fmtAmt(row.tax_amount)}</span>
                        )}
                      </td>
                      <td style={S.td}>
                        <input
                          style={{ ...S.input, width: 46 }}
                          value={row.currency ?? 'HKD'}
                          disabled={glPosted || readOnly}
                          onChange={e => updateField(row._id, 'currency', e.target.value)}
                          onClick={e => e.stopPropagation()}
                        />
                      </td>
                    </>
                  ) : (
                    <>
                      <td style={S.td}>
                        <input
                          type="date"
                          style={S.input}
                          value={row.date ?? ''}
                          disabled={glPosted || readOnly}
                          onChange={e => updateField(row._id, 'date', e.target.value)}
                          onClick={e => e.stopPropagation()}
                        />
                      </td>

                      <td
                        style={S.td}
                        title={
                          glPosted
                            ? 'GL posted - unpost in RECON before changing type'
                            : undefined
                        }
                        onClick={(e) => {
                          e.stopPropagation()
                          if (!glPosted) return
                          window.alert(
                            'This row\u2019s GL voucher is already posted, so the AR / AP type cannot be changed here. In RECON mode, open the match group and unpost the journal (back to draft), then return to this table to change the type.',
                          )
                        }}
                      >
                        <select
                          style={{
                            ...S.select,
                            ...(glPosted ? { pointerEvents: 'none' as const, cursor: 'not-allowed' as const } : {}),
                          }}
                          value={row.transaction_type ?? ''}
                          disabled={glPosted || readOnly}
                          onChange={e => updateField(row._id, 'transaction_type', e.target.value)}
                        >
                          {TX_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                        </select>
                        <span style={isAR ? S.badgeAR : S.badgeAP}>
                          {txType || '?'}
                        </span>
                      </td>

                      <td style={S.tdAmt}>
                        <input
                          type="number"
                          step="0.01"
                          style={S.inputAmt}
                          value={row.debit ?? ''}
                          placeholder="—"
                          disabled={glPosted || readOnly}
                          onChange={e => updateField(row._id, 'debit', parseAmt(e.target.value))}
                          onClick={e => e.stopPropagation()}
                        />
                        {row.debit != null && (
                          <span style={S.fmtAP}>{fmtAmt(row.debit)}</span>
                        )}
                      </td>

                      <td style={S.tdAmt}>
                        <input
                          type="number"
                          step="0.01"
                          style={S.inputAmt}
                          value={row.credit ?? ''}
                          placeholder="—"
                          disabled={glPosted || readOnly}
                          onChange={e => updateField(row._id, 'credit', parseAmt(e.target.value))}
                          onClick={e => e.stopPropagation()}
                        />
                        {row.credit != null && (
                          <span style={S.fmtAR}>{fmtAmt(row.credit)}</span>
                        )}
                      </td>

                      <td style={S.td}>
                        <input
                          style={{ ...S.input, width: 46 }}
                          value={row.currency ?? 'HKD'}
                          disabled={glPosted || readOnly}
                          onChange={e => updateField(row._id, 'currency', e.target.value)}
                          onClick={e => e.stopPropagation()}
                        />
                      </td>

                      <td style={S.td}>
                        <input
                          style={S.input}
                          value={row.payer ?? ''}
                          disabled={glPosted || readOnly}
                          onChange={e => updateField(row._id, 'payer', e.target.value)}
                          onClick={e => e.stopPropagation()}
                        />
                      </td>

                      <td style={S.td}>
                        <input
                          style={S.input}
                          value={row.payee ?? ''}
                          disabled={glPosted || readOnly}
                          onChange={e => updateField(row._id, 'payee', e.target.value)}
                          onClick={e => e.stopPropagation()}
                        />
                      </td>

                      <td style={S.td}>
                        <input
                          style={S.input}
                          value={row.bank ?? ''}
                          disabled={glPosted || readOnly}
                          onChange={e => updateField(row._id, 'bank', e.target.value)}
                          onClick={e => e.stopPropagation()}
                        />
                      </td>
                    </>
                  )}

                  {/* Account code — full company CoA */}
                  <td
                    style={S.td}
                    onClick={e => e.stopPropagation()}
                    title={glPosted ? 'Posted to general ledger - read-only' : undefined}
                  >
                    {(() => {
                      const opts = reconCoaOptions
                      const selectValue =
                        row.account_code && coaCodeSet.has(String(row.account_code).trim())
                          ? String(row.account_code).trim()
                          : ''
                      const conflicts: Array<{ field: string; extracted_value: string; rule_value: string; rule_source: string }> =
                        Array.isArray(row.rule_conflicts) ? row.rule_conflicts : []
                      const acConflict = conflicts.find(c => c.field === 'account_code')
                      const acGlLock = glPosted || readOnly
                      return (
                        <div style={{ position: 'relative' }}>
                          {acConflict && (
                            <div style={{
                              position: 'absolute', top: -2, right: -2, width: 8, height: 8,
                              background: '#f59e0b', borderRadius: '50%', zIndex: 10,
                            }} title={`Rule suggests ${acConflict.rule_value} (${acConflict.rule_source})`} />
                          )}
                          {opts.length > 0 ? (
                            <select
                              style={{
                                ...S.select, maxWidth: 130, minWidth: 100,
                                color: selectValue ? '#2563eb' : '#9ca3af',
                                ...(acConflict ? { border: '1px solid #f59e0b', background: '#fffbeb' } : {}),
                              }}
                              value={selectValue}
                              disabled={acGlLock}
                              onChange={e => updateAccountCode(row._id, e.target.value)}
                            >
                              <option value="">- Select account -</option>
                              {opts.map(opt => <option key={opt} value={opt.split(' ')[0]}>{opt}</option>)}
                            </select>
                          ) : (
                            <span style={{ ...S.input, color: '#9ca3af', display: 'block' }}>—</span>
                          )}
                          {acConflict && !acGlLock && (
                            <div style={{ display: 'flex', gap: 4, marginTop: 3 }}>
                              <button
                                type="button"
                                onClick={() => updateAccountCode(row._id, acConflict.rule_value)}
                                style={{ fontSize: 10, padding: '1px 6px', background: '#111827', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}
                                title={`Apply rule: ${acConflict.rule_value}`}
                              >Rule {acConflict.rule_value}</button>
                              <button
                                type="button"
                                onClick={() => {
                                  const updated = rows.map(r => r._id === row._id
                                    ? { ...r, rule_conflicts: conflicts.filter(c => c.field !== 'account_code') }
                                    : r
                                  )
                                  onDataChange?.(updated.map(toClean))
                                }}
                                style={{ fontSize: 10, padding: '1px 6px', background: '#f3f4f6', color: '#374151', border: '1px solid #e5e7eb', borderRadius: 4, cursor: 'pointer' }}
                              >Keep</button>
                            </div>
                          )}
                        </div>
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

                  {/* 備註 */}
                  <td style={S.td}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                      <input
                        style={S.input}
                        value={row.memo ?? ''}
                        disabled={glPosted || readOnly}
                        onChange={e => updateField(row._id, 'memo', e.target.value)}
                        onClick={e => e.stopPropagation()}
                      />
                      {onRetryOcrPage &&
                        row.ocr_background_job_id &&
                        row.ocr_retry_page != null &&
                        String(row.memo || '').includes('[OCR failed]') && (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation()
                              onRetryOcrPage(String(row.ocr_background_job_id), Number(row.ocr_retry_page))
                            }}
                            style={{
                              alignSelf: 'flex-start',
                              fontSize: 11,
                              padding: '3px 8px',
                              background: '#1d4ed8',
                              color: '#fff',
                              border: 'none',
                              borderRadius: 4,
                              cursor: 'pointer',
                            }}
                          >
                            Retry page
                          </button>
                        )}
                    </div>
                  </td>

                  {useApTableSchema && (
                    <td style={S.td}>
                      <input
                        style={S.input}
                        value={row.payment_status ?? ''}
                        disabled={glPosted || readOnly}
                        onChange={e => updateField(row._id, 'payment_status', e.target.value)}
                        onClick={e => e.stopPropagation()}
                      />
                    </td>
                  )}

                  {/* Image quality (AQ provenance) */}
                  <td style={S.td} onClick={e => e.stopPropagation()}>
                    {imageQuality.present ? (
                      <button
                        type="button"
                        title={imageQuality.reason || imageQuality.uiLabel}
                        onClick={() => {
                          setSelected(new Set([row._id]))
                          setQualityPanelOpen(true)
                        }}
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          maxWidth: 140,
                          border: `1px solid ${iqChip.border}`,
                          background: iqChip.bg,
                          color: iqChip.fg,
                          borderRadius: 4,
                          fontSize: 11,
                          fontWeight: 600,
                          padding: '3px 8px',
                          cursor: 'pointer',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {imageQuality.uiLabel || imageQuality.status || 'Quality'}
                      </button>
                    ) : (
                      <span style={{ color: '#9ca3af', fontSize: 11 }}>—</span>
                    )}
                  </td>

                  {/* 信心度 */}
                  <td style={{ ...S.td, textAlign: 'center', fontSize: 11, color: '#888' }}>
                    <span style={{ padding: '7px 6px', display: 'block' }}>
                      {row.confidence ?? ''}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* ── Receipt image quality panel (real AQ provenance; no crop photos) ── */}
      {selectedQualityRow && (
        <div
          style={{
            margin: '0 12px 12px',
            border: '1px solid #e5e7eb',
            borderRadius: 8,
            background: '#fff',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 8,
              padding: '8px 12px',
              borderBottom: qualityPanelOpen ? '1px solid #f3f4f6' : 'none',
              background: '#f8fafc',
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 600 }}>
              Receipt image quality
              <span style={{ marginLeft: 8, fontWeight: 400, color: '#6b7280' }}>
                {selectedQualityRow.iq.uiLabel}
                {selectedQualityRow.row.source_file
                  ? ` · ${String(selectedQualityRow.row.source_file)}`
                  : ''}
              </span>
            </div>
            <button
              type="button"
              style={{ ...S.btn, fontSize: 12, padding: '4px 10px' }}
              onClick={() => setQualityPanelOpen(v => !v)}
            >
              {qualityPanelOpen ? 'Collapse' : 'Expand'}
            </button>
          </div>
          {qualityPanelOpen && (
            <div style={{ padding: 12, fontSize: 12, color: '#374151' }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
                <span
                  style={{
                    border: `1px solid ${imageQualityChipStyle(selectedQualityRow.iq.status).border}`,
                    background: imageQualityChipStyle(selectedQualityRow.iq.status).bg,
                    color: imageQualityChipStyle(selectedQualityRow.iq.status).fg,
                    borderRadius: 4,
                    padding: '2px 8px',
                    fontWeight: 600,
                  }}
                >
                  {selectedQualityRow.iq.status || 'unknown'}
                </span>
                <span style={{ color: '#6b7280' }}>
                  selection: {selectedQualityRow.iq.selection || '—'}
                </span>
                {selectedQualityRow.iq.scoreBefore != null && (
                  <span style={{ color: '#6b7280' }}>
                    score: {selectedQualityRow.iq.scoreBefore.toFixed(3)}
                    {selectedQualityRow.iq.scoreAfter != null
                      ? ` → ${selectedQualityRow.iq.scoreAfter.toFixed(3)}`
                      : ''}
                  </span>
                )}
              </div>
              {selectedQualityRow.iq.reason ? (
                <p style={{ margin: '0 0 8px', color: '#4b5563' }}>{selectedQualityRow.iq.reason}</p>
              ) : null}
              {selectedQualityRow.iq.issues.length > 0 ? (
                <p style={{ margin: '0 0 8px' }}>
                  Issues: {selectedQualityRow.iq.issues.join(', ')}
                </p>
              ) : null}
              <p style={{ margin: '0 0 8px' }}>
                Recipe:{' '}
                {selectedQualityRow.iq.recipeOps.length
                  ? selectedQualityRow.iq.recipeOps.join(' → ')
                  : 'none'}
              </p>
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 1fr',
                  gap: 12,
                }}
              >
                <div>
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>Before</div>
                  {(selectedQualityRow.iq.qualityBefore
                    ? IMAGE_QUALITY_METRIC_KEYS.filter(
                        k => selectedQualityRow.iq.qualityBefore?.[k] != null,
                      )
                    : []
                  ).map(k => (
                    <div
                      key={k}
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        borderTop: '1px solid #f3f4f6',
                        padding: '2px 0',
                        fontSize: 11,
                      }}
                    >
                      <span>{k}</span>
                      <span>{Number(selectedQualityRow.iq.qualityBefore?.[k]).toFixed(4)}</span>
                    </div>
                  ))}
                  {!selectedQualityRow.iq.qualityBefore && (
                    <div style={{ color: '#9ca3af', fontSize: 11 }}>No metrics</div>
                  )}
                </div>
                <div>
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>After</div>
                  {selectedQualityRow.iq.qualityAfter ? (
                    IMAGE_QUALITY_METRIC_KEYS.filter(
                      k => selectedQualityRow.iq.qualityAfter?.[k] != null,
                    ).map(k => (
                      <div
                        key={k}
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          borderTop: '1px solid #f3f4f6',
                          padding: '2px 0',
                          fontSize: 11,
                        }}
                      >
                        <span>{k}</span>
                        <span>{Number(selectedQualityRow.iq.qualityAfter?.[k]).toFixed(4)}</span>
                      </div>
                    ))
                  ) : (
                    <div style={{ color: '#9ca3af', fontSize: 11 }}>No enhancement applied</div>
                  )}
                </div>
              </div>
              <p style={{ margin: '10px 0 0', fontSize: 11, color: '#9ca3af' }}>
                Metrics from local OpenCV AQ audit on the crop used for OCR. Crop photos are not shown
                here.
              </p>
            </div>
          )}
        </div>
      )}

      {/* ── Status bar ── */}
      <div style={S.statusBar}>
        <span style={S.statusItem}>
          <span style={{ ...S.dot, background: '#3b82f6' }} />
          AR: {arRows.length} items &middot; Total HKD {fmtAmt(totalAR)}
        </span>
        <span style={S.statusItem}>
          <span style={{ ...S.dot, background: '#dc2626' }} />
          AP: {apRows.length} items &middot; Total HKD {fmtAmt(totalAP)}
        </span>
      </div>
    </div>
  )
}

// ─── Inline styles ────────────────────────────────────────────────────────────

const styles: Record<string, CSSProperties> = {
  wrapper: {
    fontFamily: "'Segoe UI', Arial, sans-serif",
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
    border: '1px solid #f59e0b',
    borderRadius: 6,
    background: '#fffbeb',
    color: '#92400e',
    cursor: 'pointer',
    fontSize: 12,
    fontWeight: 600,
  },
  rowCount: { fontSize: 12, color: '#666' },

  processingBanner: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    margin: '0 20px 4px',
    padding: '7px 12px',
    background: '#eff6ff',
    border: '1px solid #bfdbfe',
    borderRadius: 6,
    fontSize: 12,
    color: '#1d4ed8',
    fontWeight: 500,
  } as CSSProperties,
  processingDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    background: '#3b82f6',
    flexShrink: 0,
    animation: 'arap-pulse 1.2s ease-in-out infinite',
  } as CSSProperties,

  tableContainer: {
    padding: '16px 20px',
    overflowX: 'auto',
    width: '100%',
    maxWidth: '100%',
    boxSizing: 'border-box',
    WebkitOverflowScrolling: 'touch',
    overscrollBehaviorX: 'contain',
  } as CSSProperties,
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
    color: '#555',
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
    maxWidth: 46,
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
  fmtAR: {
    display: 'block',
    padding: '7px 10px',
    fontFamily: "'Courier New', monospace",
    fontSize: 12,
    color: '#2563eb',
    textAlign: 'right',
    pointerEvents: 'none',
  },
  fmtAP: {
    display: 'block',
    padding: '7px 10px',
    fontFamily: "'Courier New', monospace",
    fontSize: 12,
    color: '#dc2626',
    textAlign: 'right',
    pointerEvents: 'none',
  },
  matchedId: {
    display: 'block',
    padding: '7px 10px',
    fontFamily: "'Courier New', monospace",
    fontSize: 11,
    color: '#aaa',
    whiteSpace: 'nowrap',
  },
  badgeAR: {
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
  badgeAP: {
    display: 'inline-block',
    padding: '1px 6px',
    borderRadius: 10,
    fontSize: 10,
    fontWeight: 600,
    background: '#fef2f2',
    color: '#dc2626',
    marginLeft: 4,
    verticalAlign: 'middle',
  },

  jsonPanel: {
    margin: '0',
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
}

function resolveARAPStyles(isMobile: boolean): Record<string, CSSProperties> {
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
    processingBanner: {
      ...styles.processingBanner,
      fontSize: 10,
      margin: '0 10px 4px',
      padding: '6px 8px',
      gap: 6,
    },
    tableContainer: { ...styles.tableContainer, padding: '8px 10px' },
    table: { ...styles.table, fontSize: 10 },
    th: { ...styles.th, padding: '6px 6px', fontSize: 10 },
    rowNum: { ...styles.rowNum, fontSize: 9, padding: '4px 2px', width: 30 },
    input: { ...styles.input, fontSize: 10, padding: '5px 6px' },
    select: { ...styles.select, fontSize: 9, padding: '5px 4px' },
    inputAmt: { ...styles.inputAmt, fontSize: 10, padding: '5px 6px' },
    fmtAR: { ...styles.fmtAR, fontSize: 10, padding: '5px 6px' },
    fmtAP: { ...styles.fmtAP, fontSize: 10, padding: '5px 6px' },
    matchedId: { ...styles.matchedId, fontSize: 9, padding: '5px 6px' },
    badgeAR: { ...styles.badgeAR, fontSize: 9 },
    badgeAP: { ...styles.badgeAP, fontSize: 9 },
    jsonHeader: { ...styles.jsonHeader, padding: '6px 12px', fontSize: 11 },
    jsonPre: { ...styles.jsonPre, padding: '10px 12px', fontSize: 10 },
    statusBar: { ...styles.statusBar, padding: '6px 10px', fontSize: 10, gap: 10 },
    statusItem: { ...styles.statusItem, gap: 4 },
    dot: { ...styles.dot, width: 6, height: 6 },
  }
}
