import { Fragment, useState, useEffect, useRef } from 'react'
import { sheetColumnLabel } from '../utils/sheetColumnLabels'
import './EditableSpreadsheet.css'

export type SpreadsheetRow = {
  id: string
  voucher_no?: string
  transaction_type?: string
  amount?: string
  currency?: string
  date?: string
  payer?: string
  payee?: string
  bank?: string
  memo?: string
  category?: string
  confidence?: string
  file_position?: string  // Source file name + page, e.g. "receipt.pdf P2"
  // Bank statement specific fields
  description?: string
  reference?: string
  payment_ref?: string
  spent?: number | string
  received?: number | string
  source?: string
  status?: string
  [key: string]: any  // Allow additional fields
}

type Props = {
  data: SpreadsheetRow[]
  onDataChange?: (data: SpreadsheetRow[]) => void
  columnsOverride?: string[]
  headersOverride?: string[]
  readOnly?: boolean
  enableRowExpand?: boolean
  categoryOptions?: string[]
}

export function EditableSpreadsheet({
  data: initialData,
  onDataChange,
  columnsOverride,
  headersOverride,
  readOnly = false,
  enableRowExpand = false,
  categoryOptions = [],
}: Props) {
  const [data, setData] = useState<SpreadsheetRow[]>(initialData)
  // editingCell tracks by ROW INDEX (not rowId) to avoid group-edit when rows share duplicate IDs.
  const [editingCell, setEditingCell] = useState<{ rowIndex: number; field: string } | null>(null)
  const [expandedRowId, setExpandedRowId] = useState<string | null>(null)
  // Track whether a cell is actively being edited to block the sync effect
  const isEditingRef = useRef(false)
  // Ref to the active editing input — used to focus it reliably without autoFocus
  const editingInputRef = useRef<HTMLInputElement | null>(null)
  // Suppresses the blur-triggered closeCell when Tab/Enter navigation is in progress
  const navigatingRef = useRef(false)
  
  // Detect if this is bank statement data (has bank statement keys)
  const isBankStatement = data.length > 0 &&
    ('日期' in data[0] || '存入' in data[0] || '提取' in data[0] || '憑證號' in data[0])
  
  // Define columns based on data type
  const columns: string[] = columnsOverride || (isBankStatement
    ? ['No.', '憑證號', '類型', '存入', '提取', '原幣結餘', '幣別', '日期', '付款人', '收款人', '銀行', '賬戶類型', '備註', 'categorise', '信心度', '檔案位置']
    : ['voucher_no', 'transaction_type', 'amount', 'currency', 'date', 'payer', 'payee', 'bank', 'category', 'memo', 'confidence', 'file_position'])
  
  const headers = (headersOverride || columns).map(sheetColumnLabel)

  // Sync internal state when new rows are added/removed from the parent (accumulation).
  // We never reset data while the user is actively editing a cell.
  // If data is synced we close any open edit to avoid the rowIndex pointing at the wrong row.
  useEffect(() => {
    if (isEditingRef.current) return
    setData(prev => {
      if (prev.length !== initialData.length) {
        console.log('[Spreadsheet] Data prop changed (accumulation):', {
          oldLength: prev.length,
          newLength: initialData.length
        })
        return initialData
      }
      return prev
    })
    // Close any stale edit after a data sync (row indices may have shifted)
    setEditingCell(null)
  }, [initialData])

  // Focus the active input whenever editingCell is set (more reliable than autoFocus)
  useEffect(() => {
    if (editingCell && editingInputRef.current) {
      editingInputRef.current.focus()
    }
  }, [editingCell])

  // Update a single cell by its row INDEX (immune to duplicate row IDs).
  const handleCellEdit = (rowIndex: number, field: string, value: string) => {
    if (readOnly) return
    const newData = data.map((row, idx) =>
      idx === rowIndex ? { ...row, [field]: value } : row
    )
    setData(newData)
    if (onDataChange) {
      onDataChange(newData)
    }
  }

  const openCell = (rowIndex: number, field: string) => {
    if (readOnly) return
    if (field === '匹配狀態') return
    isEditingRef.current = true
    setEditingCell({ rowIndex, field })
  }

  const closeCell = () => {
    // Ignore the blur that fires when Tab/Enter navigation moves focus to the next cell
    if (navigatingRef.current) return
    isEditingRef.current = false
    setEditingCell(null)
  }

  // Navigate to adjacent cell: direction = 'next' | 'prev' | 'down'
  const navigateCell = (currentRowIndex: number, currentField: string, direction: 'next' | 'prev' | 'down') => {
    const colIndex = columns.indexOf(currentField)
    if (colIndex < 0) { closeCell(); return }

    let nextRow = currentRowIndex
    let nextCol = colIndex

    if (direction === 'next') {
      nextCol = colIndex + 1
      if (nextCol >= columns.length) { nextCol = 0; nextRow = currentRowIndex + 1 }
    } else if (direction === 'prev') {
      nextCol = colIndex - 1
      if (nextCol < 0) { nextCol = columns.length - 1; nextRow = currentRowIndex - 1 }
    } else {
      nextRow = currentRowIndex + 1
    }

    if (nextRow >= 0 && nextRow < data.length) {
      // Set navigating flag so the blur from the current input doesn't closeCell
      navigatingRef.current = true
      openCell(nextRow, columns[nextCol])
      // Clear the flag after a tick — the new input will have mounted by then
      setTimeout(() => { navigatingRef.current = false }, 0)
    } else {
      closeCell()
    }
  }

  // Create a per-cell keydown handler that knows its own position
  const makeKeyDownHandler = (rowIndex: number, field: string) =>
    (e: React.KeyboardEvent<HTMLInputElement | HTMLSelectElement>) => {
      if (readOnly) return
      if (e.key === 'Escape') {
        e.preventDefault()
        closeCell()
      } else if (e.key === 'Tab') {
        e.preventDefault()
        navigateCell(rowIndex, field, e.shiftKey ? 'prev' : 'next')
      } else if (e.key === 'Enter') {
        e.preventDefault()
        navigateCell(rowIndex, field, 'down')
      }
    }

  const isCategoryField = (field: string) =>
    field === 'category' ||
    field === 'categorise' ||
    field === '分類' ||
    field.toLowerCase().includes('category') ||
    field.toLowerCase().includes('categorise') ||
    field.includes('分類')

  const addEmptyRow = () => {
    if (readOnly) return
    const newRow: SpreadsheetRow = {
      id: `row-${Date.now()}-${Math.random().toString(16).slice(2)}`
    }

    if (isBankStatement) {
      newRow['No.'] = data.length + 1
      newRow['憑證號'] = ''
      newRow['類型'] = ''
      newRow['存入'] = ''
      newRow['提取'] = ''
      newRow['原幣結餘'] = ''
      newRow['幣別'] = ''
      newRow['日期'] = ''
      newRow['付款人'] = ''
      newRow['收款人'] = ''
      newRow['銀行'] = ''
      newRow['賬戶類型'] = ''
      newRow['備註'] = ''
      newRow['categorise'] = ''
      newRow['信心度'] = ''
    } else {
      newRow.voucher_no = ''
      newRow.transaction_type = ''
      newRow.amount = ''
      newRow.currency = ''
      newRow.date = ''
      newRow.payer = ''
      newRow.payee = ''
      newRow.bank = ''
      newRow.category = ''
      newRow.memo = ''
      newRow.confidence = ''
    }

    const newData = [...data, newRow]
    setData(newData)
    if (onDataChange) {
      onDataChange(newData)
    }
  }

  const removeRow = (rowId: string) => {
    if (readOnly) return
    const newData = data.filter(row => row.id !== rowId)
    setData(newData)
    if (onDataChange) {
      onDataChange(newData)
    }
  }


  const exportToCSV = () => {
    const csvHeaders = columns.map(sheetColumnLabel)
    
    const rows = data.map(row => 
      columns.map(col => row[col] || '')
    )
    
    const csvContent = [
      csvHeaders.join(','),
      ...rows.map(row => row.map(cell => `"${cell}"`).join(','))
    ].join('\n')
    
    const blob = new Blob(['\uFEFF' + csvContent], { type: 'text/csv;charset=utf-8;' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    const filename = isBankStatement 
      ? `bank_statement_${new Date().toISOString().split('T')[0]}.csv`
      : `cheques_${new Date().toISOString().split('T')[0]}.csv`
    link.download = filename
    link.click()
  }

  const copyToClipboard = () => {
    const text = data.map(row => 
      columns.map(col => row[col] || '').join('\t')
    ).join('\n')
    
    navigator.clipboard.writeText(text).then(() => {
      alert('Copied to clipboard')
    })
  }

  const getExpandedRowDetails = (row: SpreadsheetRow) => {
    const pinnedFields = [
      'id',
      'bank_txn_id',
      'ledger_txn_id',
      'import_batch_id',
      'source',
      'rule_hit',
      'edited_by',
      'edited_at',
      '備註',
      'AR/AP Mode 備註',
      'Bank Mode 備註',
      'memo',
      'reference',
      'bank_date',
      'book_date',
      'created_at',
      'updated_at'
    ]
    const detailKeys: string[] = []
    pinnedFields.forEach((key) => {
      if (row[key] !== undefined && row[key] !== null && String(row[key]).trim() !== '') {
        detailKeys.push(key)
      }
    })
    Object.keys(row).forEach((key) => {
      if (
        !detailKeys.includes(key) &&
        !columns.includes(key) &&
        row[key] !== undefined &&
        row[key] !== null &&
        String(row[key]).trim() !== ''
      ) {
        detailKeys.push(key)
      }
    })
    return detailKeys
  }

  return (
    <div className="spreadsheet-container">
      <div className="spreadsheet-actions">
        <button onClick={exportToCSV} className="btn-export">Export CSV</button>
        <button onClick={copyToClipboard} className="btn-copy">Copy</button>
        {!readOnly && <button onClick={addEmptyRow} className="btn-add-row">Add row</button>}
        {!readOnly && <span className="edit-hint">Double-click a cell to edit</span>}
      </div>
      
      <div className="spreadsheet-wrapper">
        <table className="spreadsheet">
          <thead>
            <tr>
              <th>No.</th>
              {!readOnly && <th>Actions</th>}
              {headers.map((header, idx) => (
                <th key={idx}>{header}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((row, index) => {
              const isExpanded = expandedRowId === row.id
              const detailKeys = getExpandedRowDetails(row)
              return (
                <Fragment key={`${row.id ?? 'row'}-${index}`}>
                  <tr
                    className={enableRowExpand ? 'expandable-row' : ''}
                    onClick={() => {
                      if (!enableRowExpand) return
                      if (editingCell) return
                      setExpandedRowId((prev) => (prev === row.id ? null : row.id))
                    }}
                  >
                    <td className="row-number">{index + 1}</td>
                    {!readOnly && (
                      <td className="row-actions">
                        <button
                          type="button"
                          className="btn-remove-row"
                          onClick={(e) => {
                            e.stopPropagation()
                            removeRow(row.id)
                          }}
                        >
                          Delete
                        </button>
                      </td>
                    )}
                    {columns.map(field => {
                      // Use row INDEX (not rowId) to detect editing — immune to duplicate IDs
                      const isEditing = editingCell?.rowIndex === index && editingCell?.field === field
                      const value = row[field]
                      
                      // Format numbers for bank statement
                      let displayValue = value || '-'
                      if (isBankStatement && (field === '存入' || field === '提取')) {
                        const num = typeof value === 'number' ? value : parseFloat(value as string || '0')
                        displayValue = num === 0 ? '-' : num.toFixed(2)
                      }
                      
                      return (
                        <td
                          key={field}
                          className={`cell ${isEditing ? 'editing' : ''} ${isBankStatement && field === '存入' ? 'deposit' : ''} ${isBankStatement && field === '提取' ? 'withdrawal' : ''} ${field === '匹配狀態' ? 'match-status-cell' : ''}`}
                          onDoubleClick={(e) => {
                            e.preventDefault()
                            openCell(index, field)
                          }}
                        >
                          {isEditing ? (
                            isCategoryField(field) && categoryOptions.length > 0 ? (
                              <select
                                value={value !== undefined && value !== null ? String(value) : ''}
                                onChange={(e) => {
                                  handleCellEdit(index, field, e.target.value)
                                  closeCell()
                                }}
                                onBlur={closeCell}
                                onKeyDown={makeKeyDownHandler(index, field)}
                                ref={(el) => { if (el) el.focus() }}
                                className="cell-input"
                              >
                                <option value="">(empty)</option>
                                {categoryOptions.map((option) => (
                                  <option key={option} value={option}>
                                    {option}
                                  </option>
                                ))}
                              </select>
                            ) : (
                              <input
                                ref={editingInputRef}
                                type="text"
                                value={value !== undefined && value !== null ? String(value) : ''}
                                onChange={(e) => handleCellEdit(index, field, e.target.value)}
                                onBlur={closeCell}
                                onKeyDown={makeKeyDownHandler(index, field)}
                                className="cell-input"
                              />
                            )
                          ) : (
                            field === '匹配狀態'
                              ? <span className="match-status-badge">{displayValue}</span>
                              : <span>{displayValue}</span>
                          )}
                        </td>
                      )
                    })}
                  </tr>
                  {enableRowExpand && isExpanded && (
                    <tr className="expanded-row-details">
                      <td colSpan={headers.length + (readOnly ? 1 : 2)}>
                        {detailKeys.length === 0 ? (
                          <div className="expanded-empty">No extra details</div>
                        ) : (
                          <div className="expanded-grid">
                            {detailKeys.map((key) => (
                              <div key={`${row.id}-${key}`} className="expanded-item">
                                <span className="expanded-key">{sheetColumnLabel(key)}</span>
                                <span className="expanded-value">{String(row[key])}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
      
      <div className="spreadsheet-footer">
        <span>Total: {data.length} record{data.length === 1 ? '' : 's'}</span>
      </div>
    </div>
  )
}
