import { useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react'
import { isModuleTxnLocked } from '../recon/moduleReconKeys'
import { useAuth } from '../../contexts/AuthContext'
import { FilePreviewModal } from '../../components/filePreview'
import { reconciliationApi } from '../../services/reconciliation'
import type { ChartOfAccountItem } from '../../types/reconciliation'
import type { ModuleDef } from './moduleRegistry'
import { DataGridShell, type Column } from './DataGridShell'
import { FilterBar } from './FilterBar'
import { GridFooter } from './GridFooter'
import { useRowFilePreview } from './useRowFilePreview'
import { useModuleTransactions, type FlatRow } from './useModuleTransactions'
import { txSourceLabel } from '../../utils/rowSourceLabel'
import {
  bankModuleSectionHeaders,
  bankSortGroupKey,
  deriveBankAccountOptions,
  deriveBankBatchOptions,
  filterBankModuleRows,
  type BankModuleFilters,
} from './bankModuleRowFilters'
import { BANK_ACCOUNT_TYPES_VALID } from '../../utils/bankAccountTypeCoalesce'
import {
  coaCodeSet,
  coaLocalizedName,
  coaNameByCodeMap,
  coaOptionLabel,
  validCoaCode,
} from '../../utils/coaDisplay'
import { csvSampleForMode } from '../workspace/parseArapCsv'
import { parseModuleCsvTransactions, type ModuleCsvMode } from './parseModuleCsv'

type Props = { module: ModuleDef }

/** Select sentinel: switch Add Row bank account cell to manual text input. */
const BANK_ACCOUNT_CUSTOM = '__custom__'

type FieldType = 'text' | 'num' | 'date'
type FieldCol = {
  key: string
  header: string
  field: string
  type: FieldType
  fallbackField?: string
  numeric?: boolean
  /** Statement reference from Processing; not editable in Books. */
  readOnly?: boolean
}

const COLS_BY_MODE: Record<string, FieldCol[]> = {
  AP: [
    { key: 'id_number', header: 'ID No.', field: 'id_number', type: 'text' },
    { key: 'invoice_number', header: 'Invoice No.', field: 'invoice_number', type: 'text' },
    { key: 'date', header: 'Date', field: 'date', type: 'date' },
    { key: 'due_date', header: 'Due Date', field: 'due_date', type: 'date' },
    { key: 'payee', header: 'Supplier', field: 'payee', type: 'text' },
    { key: 'debit', header: 'Debit', field: 'debit', type: 'num', numeric: true },
    { key: 'credit', header: 'Credit', field: 'credit', type: 'num', numeric: true },
    { key: 'tax_amount', header: 'Tax', field: 'tax_amount', type: 'num', numeric: true },
    { key: 'currency', header: 'Cur', field: 'currency', type: 'text' },
    { key: 'account_code', header: 'Account', field: 'account_code', type: 'text' },
    { key: 'category', header: 'Category', field: 'category', type: 'text', readOnly: true },
    { key: 'payment_status', header: 'Payment', field: 'payment_status', type: 'text' },
  ],
  AR: [
    { key: 'id_number', header: 'ID No.', field: 'id_number', type: 'text' },
    { key: 'date', header: 'Date', field: 'date', type: 'date' },
    { key: 'debit', header: 'Debit', field: 'debit', type: 'num', numeric: true },
    { key: 'credit', header: 'Credit', field: 'credit', type: 'num', numeric: true },
    { key: 'currency', header: 'Cur', field: 'currency', type: 'text' },
    { key: 'payer', header: 'Payer', field: 'payer', type: 'text' },
    { key: 'payee', header: 'Payee', field: 'payee', type: 'text' },
    { key: 'bank', header: 'Bank', field: 'bank', type: 'text' },
    { key: 'account_code', header: 'Account', field: 'account_code', type: 'text' },
    { key: 'category', header: 'Category', field: 'category', type: 'text', readOnly: true },
    { key: 'memo', header: 'Memo', field: 'memo', type: 'text' },
  ],
  BANK: [
    { key: 'date', header: 'Date', field: 'date', type: 'date', fallbackField: 'transaction_date' },
    { key: 'account_type', header: 'Bank account', field: 'account_type', type: 'text' },
    { key: 'particulars', header: 'Description', field: 'particulars', type: 'text', fallbackField: 'description' },
    { key: 'id_number', header: 'Reference', field: 'id_number', type: 'text' },
    { key: 'withdrawal', header: 'Withdrawal', field: 'withdrawal', type: 'num', numeric: true },
    { key: 'deposit', header: 'Deposit', field: 'deposit', type: 'num', numeric: true },
    { key: 'balance', header: 'Balance', field: 'balance', type: 'num', numeric: true },
    { key: 'currency', header: 'Cur', field: 'currency', type: 'text' },
    { key: 'account_code', header: 'GL code', field: 'account_code', type: 'text' },
    { key: 'category', header: 'Category', field: 'category', type: 'text', readOnly: true },
  ],
}

function statusBadge(status: string): { cls: string; label: string } {
  const s = status.toLowerCase()
  if (s === 'completed' || s === 'done' || s === 'saved') return { cls: 'posted', label: 'Done' }
  if (s === 'failed' || s === 'error') return { cls: 'review', label: 'Failed' }
  if (s === 'executing' || s === 'coa_running') return { cls: 'open', label: 'Running' }
  if (s === 'awaiting_review') return { cls: 'open', label: 'Review' }
  return { cls: 'open', label: status || 'Draft' }
}

function fmtDate(iso: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '-' : d.toLocaleString()
}

function fmtNum(v: unknown): string {
  if (v === null || v === undefined || v === '') return ''
  const n = Number(v)
  return Number.isNaN(n) ? String(v) : n.toLocaleString('en-HK', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function parseNum(s: string): number | null {
  if (!s || s.trim() === '') return null
  const n = parseFloat(s.replace(/,/g, ''))
  return Number.isNaN(n) ? null : n
}

const BANK_ACCOUNT_TYPE_KEYS = ['account_type', '賬戶類型', '帳戶類型', '账户类型'] as const

function cellValue(
  row: FlatRow,
  col: FieldCol,
  coaCodes?: Set<string>,
  nameByCode?: Map<string, string>,
): string {
  if (col.field === 'account_type') {
    for (const key of BANK_ACCOUNT_TYPE_KEYS) {
      const v = row.tx[key]
      if (v != null && String(v).trim() !== '') return String(v)
    }
    return ''
  }
  if (col.field === 'account_code' && coaCodes) {
    return validCoaCode(row.tx.account_code, coaCodes)
  }
  if (col.field === 'category' && coaCodes && nameByCode) {
    const code = validCoaCode(row.tx.account_code, coaCodes)
    return code ? nameByCode.get(code) || '' : ''
  }
  const v = row.tx[col.field] ?? (col.fallbackField ? row.tx[col.fallbackField] : undefined)
  return v == null ? '' : String(v)
}

function csvCell(v: string): string {
  return v.includes(',') || v.includes('"') || v.includes('\n') ? `"${v.replace(/"/g, '""')}"` : v
}

function downloadCsv(filename: string, content: string) {
  const blob = new Blob(['\uFEFF' + content], { type: 'text/csv' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
  URL.revokeObjectURL(a.href)
}

const BANK_STATUS_VALUES = new Set(['all', 'reconciled', 'unreconciled', 'open'])

function initialBankStatus(raw: string | undefined): string {
  const s = (raw ?? 'all').toLowerCase()
  return BANK_STATUS_VALUES.has(s) ? s : 'all'
}

export function ModuleTransactionGrid({ module }: Props) {
  const { activeCompany } = useAuth()
  const companyId = activeCompany?.id ?? 'default'
  const mode = (module.mode ?? '').toUpperCase()
  const fieldCols = COLS_BY_MODE[mode] ?? COLS_BY_MODE.AR
  const isBank = mode === 'BANK'
  const csvSample = csvSampleForMode(mode)
  const importInputRef = useRef<HTMLInputElement>(null)
  const [coaList, setCoaList] = useState<ChartOfAccountItem[]>([])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const { accounts } = await reconciliationApi.getChartOfAccounts()
        if (!cancelled) setCoaList(accounts ?? [])
      } catch (err) {
        console.warn('[ModuleTransactionGrid] CoA load failed', err)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [companyId])

  const coaCodes = useMemo(() => coaCodeSet(coaList), [coaList])
  const nameByCode = useMemo(() => coaNameByCodeMap(coaList), [coaList])
  const coaSelectOptions = useMemo(
    () =>
      [...coaList]
        .filter(a => (a.code ?? '').trim())
        .sort((a, b) => a.code.localeCompare(b.code))
        .map(a => ({ code: a.code.trim(), label: coaOptionLabel(a), name: coaLocalizedName(a) })),
    [coaList],
  )

  const presetKey = `erp.txfilter.${module.id}`
  type BankPreset = {
    account?: string
    batch?: string
    description?: string
    status?: string
    dateFrom?: string
    dateTo?: string
    search?: string
  }
  const savedPreset = (() => {
    try {
      return JSON.parse(localStorage.getItem(presetKey) || '{}') as BankPreset
    } catch {
      return {}
    }
  })()

  const tx = useModuleTransactions(mode, companyId)
  const preview = useRowFilePreview(companyId)
  const [search, setSearch] = useState(savedPreset.search ?? '')
  const [statusFilter, setStatusFilter] = useState(
    () => (isBank ? initialBankStatus(savedPreset.status) : savedPreset.status ?? 'all'),
  )
  const [accountFilter, setAccountFilter] = useState(savedPreset.account ?? '')
  const [batchFilter, setBatchFilter] = useState(savedPreset.batch ?? '')
  const [descriptionFilter, setDescriptionFilter] = useState(savedPreset.description ?? '')
  const [dateFrom, setDateFrom] = useState(savedPreset.dateFrom ?? '')
  const [dateTo, setDateTo] = useState(savedPreset.dateTo ?? '')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  /** Add Row keys where user chose "Other (type manually)" for bank account. */
  const [customBankAccountKeys, setCustomBankAccountKeys] = useState<Set<string>>(new Set())

  useEffect(() => {
    try {
      if (isBank) {
        localStorage.setItem(
          presetKey,
          JSON.stringify({
            account: accountFilter,
            batch: batchFilter,
            description: descriptionFilter,
            status: statusFilter,
            dateFrom,
            dateTo,
          }),
        )
      } else {
        localStorage.setItem(presetKey, JSON.stringify({ search, status: statusFilter }))
      }
    } catch {
      /* storage may be unavailable */
    }
  }, [presetKey, isBank, search, statusFilter, accountFilter, batchFilter, descriptionFilter, dateFrom, dateTo])

  const bankAccountOptions = useMemo(
    () => (isBank ? deriveBankAccountOptions(tx.rows) : []),
    [isBank, tx.rows],
  )

  const bankBatchOptions = useMemo(
    () => (isBank ? deriveBankBatchOptions(tx.rows) : []),
    [isBank, tx.rows],
  )

  const bankAccountSelectOptions = useMemo(() => {
    if (!isBank) return []
    const set = new Set<string>([...BANK_ACCOUNT_TYPES_VALID, ...bankAccountOptions])
    return Array.from(set).sort()
  }, [isBank, bankAccountOptions])

  const bankAccountDatalistId = `${module.id}-bank-accounts`

  const bankFilters: BankModuleFilters = useMemo(
    () => ({
      account: accountFilter,
      batch: batchFilter,
      description: descriptionFilter,
      status: (statusFilter as BankModuleFilters['status']) || 'all',
      dateFrom,
      dateTo,
    }),
    [accountFilter, batchFilter, descriptionFilter, statusFilter, dateFrom, dateTo],
  )

  const filtered = useMemo(() => {
    if (isBank) return filterBankModuleRows(tx.rows, bankFilters)
    const q = search.trim().toLowerCase()
    return tx.rows
      .filter(r => (statusFilter === 'all' ? true : statusBadge(r.runStatus).label.toLowerCase() === statusFilter))
      .filter(r => {
        if (!q) return true
        const hay = [
          r.runTitle,
          r.tx.id_number,
          r.tx.invoice_number,
          r.tx.payer,
          r.tx.payee,
          r.tx.bank,
          r.tx.particulars,
          r.tx.description,
          r.tx.memo,
          r.tx.account_type,
          r.tx['賬戶類型'],
          r.tx.account_code,
          txSourceLabel(r.tx, r.filename),
        ]
          .map(v => String(v ?? '').toLowerCase())
          .join(' ')
        return hay.includes(q)
      })
  }, [isBank, tx.rows, bankFilters, search, statusFilter])

  const columns: Column<FlatRow>[] = useMemo(() => {
    const editable: Column<FlatRow>[] = fieldCols.map(col => ({
      key: col.key,
      header: col.header,
      numeric: col.numeric,
      value: row => {
        const raw = cellValue(row, col, coaCodes, nameByCode)
        if (col.type === 'num') {
          const n = Number(raw)
          return raw === '' ? null : Number.isNaN(n) ? raw : n
        }
        return raw
      },
      render: row => {
        const text = cellValue(row, col, coaCodes, nameByCode)
        const locked = isModuleTxnLocked(row.tx)
        if (col.field === 'account_code') {
          if (locked) {
            return (
              <span className="erp-cell-text" title="Reconciled — unlock by cancelling the match in Reconciliation">
                {text || '-'}
              </span>
            )
          }
          return (
            <select
              className="erp-cell-select"
              value={text}
              onChange={e => {
                const code = e.target.value
                tx.updateAccountCode(row.key, code, code ? nameByCode.get(code) || '' : '')
              }}
            >
              <option value="">Select account</option>
              {coaSelectOptions.map(a => (
                <option key={a.code} value={a.code}>
                  {a.label}
                </option>
              ))}
            </select>
          )
        }
        if (col.readOnly || locked) {
          return (
            <span
              className="erp-cell-text"
              title={locked ? 'Reconciled — unlock by cancelling the match in Reconciliation' : text || undefined}
            >
              {text || '-'}
            </span>
          )
        }
        if (isBank && col.field === 'account_type') {
          // OCR / imported rows: bank account is fixed. Only Add Row (manual_entry) can pick or type.
          if (row.tx?.manual_entry !== true) {
            return (
              <span className="erp-cell-text" title={text || undefined}>
                {text || '-'}
              </span>
            )
          }
          const inCustom =
            customBankAccountKeys.has(row.key) ||
            (Boolean(text) && !bankAccountSelectOptions.includes(text))

          if (inCustom) {
            return (
              <input
                className="erp-cell-select"
                list={bankAccountDatalistId}
                value={text}
                placeholder="Type account"
                onChange={e => tx.updateBankAccountType(row.key, e.target.value)}
              />
            )
          }

          return (
            <select
              className="erp-cell-select"
              value={text}
              onChange={e => {
                const v = e.target.value
                if (v === BANK_ACCOUNT_CUSTOM) {
                  setCustomBankAccountKeys(prev => new Set(prev).add(row.key))
                  tx.updateBankAccountType(row.key, '')
                  return
                }
                setCustomBankAccountKeys(prev => {
                  const next = new Set(prev)
                  next.delete(row.key)
                  return next
                })
                tx.updateBankAccountType(row.key, v)
              }}
            >
              <option value="">Select account</option>
              {bankAccountSelectOptions.map(a => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
              <option value={BANK_ACCOUNT_CUSTOM}>Other (type manually)</option>
            </select>
          )
        }
        const isDrCrCol = col.field === 'debit' || col.field === 'credit'
        return (
          <input
            className={`erp-cell-input${col.numeric ? ' num' : ''}`}
            type={col.type === 'date' ? 'date' : col.type === 'num' ? 'number' : 'text'}
            step={col.type === 'num' ? '0.01' : undefined}
            value={text}
            onChange={e => {
              if (isDrCrCol) {
                tx.updateDebitCredit(row.key, col.field as 'debit' | 'credit', parseNum(e.target.value))
                return
              }
              tx.updateCell(row.key, col.field, col.type === 'num' ? parseNum(e.target.value) : e.target.value)
            }}
          />
        )
      },
    }))
    const trailing: Column<FlatRow>[] = [
      {
        key: 'source_file',
        header: 'Source File',
        value: r => txSourceLabel(r.tx, r.filename),
        render: r => {
          const label = txSourceLabel(r.tx, r.filename)
          return (
            <span className="erp-cell-text" title={label || undefined}>
              {label || '-'}
            </span>
          )
        },
      },
      { key: 'batch', header: 'Batch Task Name', value: r => r.runTitle, render: r => r.runTitle },
      { key: 'vlm', header: 'VLM Process Time', value: r => r.vlmAt, render: r => fmtDate(r.vlmAt) },
      {
        key: 'status',
        header: 'Status',
        value: r => (isModuleTxnLocked(r.tx) ? 'Reconciled' : statusBadge(r.runStatus).label),
        render: r => {
          if (isModuleTxnLocked(r.tx)) {
            return <span className="erp-badge posted">Reconciled</span>
          }
          const b = r.tx.needs_review === true ? { cls: 'review', label: 'Review' } : statusBadge(r.runStatus)
          return <span className={`erp-badge ${b.cls}`}>{b.label}</span>
        },
      },
      {
        key: 'preview',
        header: 'Preview',
        render: r => (
          <button
            type="button"
            className="erp-preview-btn"
            disabled={!r.fileId}
            onClick={() => r.fileId && void preview.open(r.taskId, r.fileId, r.filename || 'document')}
          >
            {r.fileId ? 'Preview' : 'Manual'}
          </button>
        ),
      },
    ]
    return [...editable, ...trailing]
  }, [
    fieldCols,
    isBank,
    bankAccountSelectOptions,
    bankAccountDatalistId,
    customBankAccountKeys,
    coaCodes,
    nameByCode,
    coaSelectOptions,
    tx,
    preview,
  ])

  const toggleSelect = (id: string) =>
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const toggleAll = (ids: string[], select: boolean) =>
    setSelectedIds(prev => {
      const next = new Set(prev)
      ids.forEach(id => (select ? next.add(id) : next.delete(id)))
      return next
    })

  const onDelete = () => {
    if (selectedIds.size === 0) return
    const locked = filtered.filter(r => selectedIds.has(r.key) && isModuleTxnLocked(r.tx)).length
    const unlocked = selectedIds.size - locked
    if (unlocked === 0) {
      window.alert('Reconciled transactions are locked. Cancel the match in Reconciliation first.')
      return
    }
    const msg =
      locked > 0
        ? `Delete ${unlocked} transaction(s)? ${locked} reconciled row(s) will be kept. Save to persist.`
        : `Delete ${unlocked} selected transaction(s)? Save to persist.`
    if (!window.confirm(msg)) return
    tx.deleteRows(selectedIds)
    setSelectedIds(new Set())
  }

  const onExport = () => {
    const exportCols = [
      ...fieldCols.map(c => ({ header: c.header, get: (r: FlatRow) => cellValue(r, c, coaCodes, nameByCode) })),
      { header: 'Source File', get: (r: FlatRow) => txSourceLabel(r.tx, r.filename) },
      { header: 'Batch Task Name', get: (r: FlatRow) => r.runTitle },
      { header: 'VLM Process Time', get: (r: FlatRow) => fmtDate(r.vlmAt) },
      { header: 'Status', get: (r: FlatRow) => statusBadge(r.runStatus).label },
    ]
    const lines = [exportCols.map(c => c.header).join(',')]
    filtered.forEach(r => lines.push(exportCols.map(c => csvCell(c.get(r))).join(',')))
    downloadCsv(`${module.id}_transactions.csv`, lines.join('\n'))
  }

  const onImportCsv = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    const csvMode = (mode === 'AP' || mode === 'AR' || mode === 'BANK' ? mode : 'AR') as ModuleCsvMode
    try {
      const text = await file.text()
      const txs = parseModuleCsvTransactions(text, csvMode)
      const n = await tx.importRows(txs, selectedIds)
      if (n === 0) {
        window.alert('No rows were imported.')
        return
      }
      window.alert(`Imported ${n} row(s) from ${file.name}. Click Save to persist.`)
    } catch (err) {
      window.alert(err instanceof Error ? err.message : 'CSV import failed.')
    }
  }

  const totalAmount = filtered.reduce((s, r) => {
    const v = isBank
      ? (Number(r.tx.deposit ?? 0) || 0) - (Number(r.tx.withdrawal ?? 0) || 0)
      : Number(r.tx.amount ?? 0) || 0
    return s + v
  }, 0)

  return (
    <>
      <FilterBar
        actions={
          <button type="button" className="erp-btn primary" onClick={() => void tx.reload()}>
            Search
          </button>
        }
      >
        {isBank ? (
          <>
            <div className="erp-field">
              Batch
              <select value={batchFilter} onChange={e => setBatchFilter(e.target.value)}>
                <option value="">All</option>
                {bankBatchOptions.map(b => (
                  <option key={b.key} value={b.key}>
                    {b.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="erp-field">
              Account
              <select value={accountFilter} onChange={e => setAccountFilter(e.target.value)}>
                <option value="">All</option>
                {bankAccountOptions.map(a => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </div>
            <div className="erp-field">
              Description
              <input
                type="text"
                value={descriptionFilter}
                onChange={e => setDescriptionFilter(e.target.value)}
              />
            </div>
            <div className="erp-field">
              Status
              <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
                <option value="all">All</option>
                <option value="reconciled">Reconciled</option>
                <option value="unreconciled">Unreconciled</option>
                <option value="open">Open</option>
              </select>
            </div>
            <div className="erp-field">
              From
              <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
            </div>
            <div className="erp-field">
              To
              <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} />
            </div>
          </>
        ) : (
          <>
            <div className="erp-field">
              Search
              <input type="text" value={search} onChange={e => setSearch(e.target.value)} />
            </div>
            <div className="erp-field">
              Status
              <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
                <option value="all">All</option>
                <option value="done">Done</option>
                <option value="running">Running</option>
                <option value="review">Review</option>
              </select>
            </div>
          </>
        )}
      </FilterBar>

      <div className="erp-gridbar">
        <button
          type="button"
          className="erp-btn"
          onClick={() => void tx.addRow(selectedIds)}
          disabled={tx.loading || tx.saving || tx.deploying || tx.preparing}
          title="Add a blank transaction row"
        >
          {tx.preparing ? 'Preparing...' : 'Add Row'}
        </button>
        <button type="button" className="erp-btn danger" onClick={onDelete} disabled={selectedIds.size === 0}>
          Delete Selected
        </button>
        <button
          type="button"
          className="erp-btn"
          onClick={() => void tx.deployCodes(selectedIds.size > 0 ? selectedIds : undefined)}
          disabled={tx.rows.length === 0 || tx.saving || tx.deploying || tx.preparing}
          title="Use AI to assign Chart of Accounts codes"
        >
          {tx.deploying ? 'Deploying...' : 'Deploy Codes'}
        </button>
        <button
          type="button"
          className="erp-btn"
          onClick={() => importInputRef.current?.click()}
          disabled={tx.loading || tx.saving || tx.deploying || tx.preparing}
          title="Import transactions from CSV (no VLM). Save afterwards to persist."
        >
          Import CSV
        </button>
        {csvSample && (
          <a
            className="erp-btn"
            href={csvSample.href}
            download={csvSample.download}
            title="Download CSV import template"
            style={{ display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }}
          >
            Sample CSV
          </a>
        )}
        <button type="button" className="erp-btn" onClick={onExport} disabled={filtered.length === 0}>
          Export CSV
        </button>
        <input
          ref={importInputRef}
          type="file"
          accept=".csv,text/csv"
          style={{ display: 'none' }}
          onChange={e => void onImportCsv(e)}
        />
        <div className="erp-grow" />
        <button
          type="button"
          className="erp-btn primary"
          onClick={() => void tx.saveAll()}
          disabled={tx.dirty.size === 0 || tx.saving || tx.deploying}
        >
          {tx.saving ? 'Saving...' : `Save${tx.dirty.size ? ` (${tx.dirty.size})` : ''}`}
        </button>
      </div>

      {isBank && (
        <datalist id={bankAccountDatalistId}>
          {bankAccountSelectOptions.map(a => (
            <option key={a} value={a} />
          ))}
        </datalist>
      )}

      <DataGridShell
        columns={columns}
        rows={filtered}
        getRowId={r => r.key}
        groupKey={isBank ? bankSortGroupKey : undefined}
        sectionHeaders={isBank ? bankModuleSectionHeaders : undefined}
        rowFlag={r => r.tx.needs_review === true || statusBadge(r.runStatus).cls === 'review'}
        selectedIds={selectedIds}
        onToggleSelect={toggleSelect}
        onToggleAll={toggleAll}
        loading={tx.loading}
        error={tx.error}
        emptyText={`No ${module.label} transactions yet. Add a row, import CSV, or process files in Processing.`}
      />

      <GridFooter
        selectedCount={selectedIds.size}
        stats={[
          { label: 'Rows', value: String(filtered.length) },
          { label: isBank ? '\u03A3 Net' : '\u03A3 Amount', value: fmtNum(totalAmount) },
        ]}
      />

      <FilePreviewModal
        open={preview.state.open}
        onClose={preview.close}
        filename={preview.state.filename}
        mimeType={preview.state.mimeType}
        previewUrl={preview.state.previewUrl}
        loading={preview.state.loading}
        error={preview.state.error}
        onDownload={preview.download}
      />
    </>
  )
}
