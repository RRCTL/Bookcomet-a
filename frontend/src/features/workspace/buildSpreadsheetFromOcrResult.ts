import type { SpreadsheetRow } from '../../components/EditableSpreadsheet'
import type { ARAPTransaction } from '../../components/ARAPReview'
import { formatBankSourceFile } from '../../utils/bankSourceFile'

/** Prefer each VLM row's `_page` over a batch fallback like `file.pdf P1`. */
function resolveSpreadsheetFilePosition(
  row: Record<string, unknown> | undefined,
  fileName: string,
  fallbackPosition?: string,
): string {
  const rec = row && typeof row === 'object' ? row : {}
  const page = rec._page ?? rec.page
  const existing = String(rec.file_position ?? rec.source_file ?? '').trim()
  if (page != null && Number.isFinite(Number(page)) && Number(page) >= 1) {
    const stem = existing.replace(/ P\d+\b/, '')
    return formatBankSourceFile(fileName, page, stem)
  }
  return formatBankSourceFile(fileName, page, existing || fallbackPosition)
}

function getFieldValue(fields: Record<string, any>, keys: string[]): string {
  for (const key of keys) {
    const value = fields?.[key]
    if (value !== undefined && value !== null && String(value).trim() !== '') return String(value)
  }
  return ''
}

/** Optional AP-table columns from a raw OCR row (merged into SpreadsheetRow for ARAPReview AP schema). */
function apTableSpreadsheetExtras(
  source: Record<string, any>,
  fieldsFallback?: Record<string, any>,
): {
  due_date: string
  invoice_number: string
  vendor_tax_id: string
  tax_amount: string
  payment_status: string
} {
  const fb = fieldsFallback && typeof fieldsFallback === 'object' ? fieldsFallback : undefined
  return {
    due_date: getFieldValue(source, ['due_date', '到期日']) || (fb ? getFieldValue(fb, ['due_date', '到期日']) : ''),
    invoice_number:
      getFieldValue(source, ['invoice_number', 'invoice_no', '發票號碼']) ||
      (fb ? getFieldValue(fb, ['invoice_number', 'invoice_no', '發票號碼']) : ''),
    vendor_tax_id:
      getFieldValue(source, ['vendor_tax_id', 'tax_id', '統一編號']) ||
      (fb ? getFieldValue(fb, ['vendor_tax_id', 'tax_id', '統一編號']) : ''),
    tax_amount: getFieldValue(source, ['tax_amount', '稅額']) || (fb ? getFieldValue(fb, ['tax_amount', '稅額']) : ''),
    payment_status:
      getFieldValue(source, ['payment_status', '付款狀態']) ||
      (fb ? getFieldValue(fb, ['payment_status', '付款狀態']) : ''),
  }
}

export type PageCropContext = {
  page?: unknown
  receipt_index?: unknown
  receipt_bbox?: unknown
  image_quality?: unknown
  receipt_instance_id?: unknown
  segmentation_mode?: unknown
  crop_status?: unknown
}

function asPlainObject(v: unknown): Record<string, unknown> | null {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null
}

function asPixelBox(v: unknown): { x: number; y: number; w: number; h: number } | null {
  const r = asPlainObject(v)
  if (!r) return null
  const x = Number(r.x)
  const y = Number(r.y)
  const w = Number(r.w)
  const h = Number(r.h)
  if (![x, y, w, h].every(n => Number.isFinite(n)) || w <= 0 || h <= 0) return null
  return { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) }
}

/** Merge M-VDU page crop box onto row provenance when the VLM row omitted it. */
export function mergePageCropIntoProvenance(
  existing: Record<string, unknown> | null | undefined,
  pageCtx?: PageCropContext | null,
): Record<string, unknown> {
  const next: Record<string, unknown> = { ...(existing ?? {}) }
  const page = Number(pageCtx?.page)
  if (Number.isFinite(page) && page >= 1 && next.source_pdf_page == null) {
    next.source_pdf_page = page
  }
  const idx = Number(pageCtx?.receipt_index)
  if (Number.isFinite(idx) && idx >= 1 && next.receipt_index == null) {
    next.receipt_index = idx
  }
  const instanceId = String(pageCtx?.receipt_instance_id ?? '').trim()
  if (instanceId && next.receipt_instance_id == null) {
    next.receipt_instance_id = instanceId
  }
  const segMode = String(pageCtx?.segmentation_mode ?? '').trim()
  if (segMode && next.segmentation_mode == null) {
    next.segmentation_mode = segMode
  }
  const cropStatus = String(pageCtx?.crop_status ?? '').trim()
  if (cropStatus && next.crop_status == null) {
    next.crop_status = cropStatus
  }
  const hasRegion =
    asPlainObject(next.receipt_region_norm) != null || asPixelBox(next.receipt_bbox_pixels) != null
  const bbox = asPixelBox(pageCtx?.receipt_bbox)
  if (!hasRegion && bbox) {
    next.receipt_bbox_pixels = bbox
  }
  if (!next.image_quality && asPlainObject(pageCtx?.image_quality)) {
    next.image_quality = pageCtx!.image_quality
  }
  return next
}

/**
 * Keep AQ / validation audit fields on SpreadsheetRow → ARAP Live output.
 * Without this, Image quality column stays empty (—) even when OCR attached provenance.
 * Optional pageCtx stamps M-VDU receipt_bbox / receipt_index onto every row from that crop.
 */
export function provenanceSpreadsheetExtras(
  source: Record<string, any>,
  pageCtx?: PageCropContext | null,
): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  const prov = source?.extraction_provenance
  let merged: Record<string, unknown> | null = null
  if (prov && typeof prov === 'object' && !Array.isArray(prov)) {
    merged = { ...prov }
  } else if (source?.image_quality && typeof source.image_quality === 'object') {
    merged = { image_quality: source.image_quality }
  }
  if (pageCtx) {
    merged = mergePageCropIntoProvenance(merged, pageCtx)
  }
  if (merged && Object.keys(merged).length > 0) {
    out.extraction_provenance = merged
  }
  if (typeof source?.needs_review === 'boolean') out.needs_review = source.needs_review
  if (Array.isArray(source?.validation_flags)) out.validation_flags = source.validation_flags
  return out
}

/** 0–1 fraction or 0–100 integer from API → display percent (one place for chat + table). */
export function formatConfidenceDisplay(raw: unknown): string {
  const parsed = Number(raw)
  if (!Number.isFinite(parsed)) return 'N/A'
  const percent = parsed <= 1 ? parsed * 100 : parsed
  return `${percent.toFixed(1)}%`
}

/** True when `extracted_fields` / `ai_enhanced` carries table rows or numeric totals. */
function extractedPayloadLooksNonempty(fields: Record<string, unknown> | null | undefined): boolean {
  if (!fields || typeof fields !== 'object') return false
  const tsv = Array.isArray(fields.tsv_rows)
    ? fields.tsv_rows.filter((r: unknown) => r && typeof r === 'object').length
    : 0
  const rec = Array.isArray(fields.receipts)
    ? fields.receipts.filter((r: unknown) => r && typeof r === 'object').length
    : 0
  const tx = Array.isArray(fields.transactions)
    ? fields.transactions.filter((r: unknown) => r && typeof r === 'object').length
    : 0
  if (tsv > 0 || rec > 0 || tx > 0) return true
  const textHints = [
    'merchant_name',
    'vendor_name',
    'transaction_date',
    'invoice_date',
    'due_date',
    'invoice_number',
    'receipt_id',
  ] as const
  if (textHints.some(k => fields[k] != null && String(fields[k]).trim() !== '')) return true
  const keys = [
    'amount_numeric',
    'amount',
    'total_amount',
    '總計',
    '总计',
    '金额',
    '金額',
  ] as const
  return keys.some(k => fields[k] != null && String(fields[k]).trim() !== '')
}

/** True when consolidated root fields carry no receipt/table payload (AP/VLM may only populate `pages[]`). */
function rootExtractedLooksEmpty(result: any): boolean {
  const root = result?.ai_enhanced ?? result?.extracted_fields
  return !extractedPayloadLooksNonempty(root as Record<string, unknown>)
}

function pageHasNonemptyExtractedPayload(pageData: any): boolean {
  if (!pageData || typeof pageData !== 'object' || pageData.status === 'error') return false
  const fields = (pageData.ai_enhanced ?? pageData.extracted_fields) as Record<string, unknown> | undefined
  return extractedPayloadLooksNonempty(fields)
}

/**
 * Build spreadsheet rows from one OCR/VLM result blob — same rules as finalizeTask in WorkspaceApp.
 */
export function buildSpreadsheetRowsFromOcrResult(args: {
  fileId: string
  fileName: string
  result: any
  processingMode: string
  /** First row uses this index for synthetic voucher numbers (yyMM-###). */
  rowIndexStart: number
  /** Background OCR job id when this row set came from /api/jobs/ocr (enables retry-page). */
  ocrBackgroundJobId?: string | null
}): { spreadsheetData: SpreadsheetRow[]; nextRowIndex: number } {
  const { fileId, fileName, result, processingMode, ocrBackgroundJobId } = args
  let rowIndex = args.rowIndexStart
  const spreadsheetData: SpreadsheetRow[] = []

  const buildVoucherNo = (txnDate?: string) => {
    const d = txnDate ? new Date(txnDate) : new Date()
    const dateObj = !isNaN(d.getTime()) ? d : new Date()
    const yy = String(dateObj.getFullYear()).slice(-2)
    const mm = String(dateObj.getMonth() + 1).padStart(2, '0')
    return `${yy}${mm}-${String(rowIndex).padStart(3, '0')}`
  }

  const appendRowsFromFields = (
    fields: Record<string, any>,
    rowIdPrefix: string,
    pageConfidence?: unknown,
    filePosition?: string,
    pageCtx?: PageCropContext | null,
  ) => {
    const tsvRows = Array.isArray(fields?.tsv_rows) ? fields.tsv_rows.filter((r: any) => r && typeof r === 'object') : []
    const cleanTsvRows = tsvRows.filter((row: any) => {
      const vno = String(row?.voucher_no ?? row?.['憑證號'] ?? '').trim()
      return !/^analysis[_\s]summary\s*:/i.test(vno)
    })
    /* If every row was filtered as bogus voucher_no but tsv_rows existed, keep raw rows so multi-line receipts are not collapsed to one fallback row. */
    const effectiveTsvRows = cleanTsvRows.length > 0 ? cleanTsvRows : tsvRows
    if (effectiveTsvRows.length > 0) {
      effectiveTsvRows.forEach((row: any, index: number) => {
        const voucherNo =
          getFieldValue(row, [
            'voucher_no',
            '憑證號',
            'invoice_number',
            'receipt_no',
            'receipt_id',
          ]) || buildVoucherNo(getFieldValue(row, ['date', '日期', 'transaction_date', 'invoice_date', 'due_date']))
        const amount = getFieldValue(row, ['amount', 'total_amount', 'amount_numeric', 'subtotal_amount'])
        const rowTransactionType = (processingMode || getFieldValue(row, ['transaction_type', '類型', '类型']) || 'AR').toUpperCase()
        const memo = getFieldValue(row, ['memo', '備註', '备注', 'description'])
        spreadsheetData.push({
          id: `${rowIdPrefix}-tsv${index + 1}`,
          voucher_no: voucherNo,
          transaction_type: rowTransactionType,
          amount,
          currency: getFieldValue(row, ['currency', '幣別', '币别']) || 'HKD',
          date: getFieldValue(row, ['date', '日期', 'transaction_date', 'invoice_date']),
          payer: getFieldValue(row, ['payer', '付款人']),
          payee: getFieldValue(row, [
            'payee',
            '收款人',
            'vendor',
            'vendor_name',
            'supplier',
            'merchant_name',
            'customer',
          ]),
          bank: getFieldValue(row, ['bank', '銀行', '银行', 'bank_name']),
          category: getFieldValue(row, ['category', 'categorise', '分類', 'account_category', 'account_code']),
          memo,
          confidence: formatConfidenceDisplay(getFieldValue(row, ['confidence', '信心度']) || pageConfidence),
          file_position: resolveSpreadsheetFilePosition(row, fileName, filePosition),
          ...apTableSpreadsheetExtras(row),
          ...provenanceSpreadsheetExtras(row, pageCtx),
        })
        rowIndex++
      })
      return
    }
    const txnRows = Array.isArray(fields?.transactions) ? fields.transactions.filter((r: any) => r && typeof r === 'object') : []
    if (txnRows.length > 0) {
      txnRows.forEach((receipt: any, receiptIndex: number) => {
        const voucherNo = buildVoucherNo(
          getFieldValue(receipt, ['date', 'invoice_date', 'transaction_date', 'due_date']) ||
            getFieldValue(fields, ['date', 'invoice_date', '日期', 'transaction_date', 'due_date']),
        )
        const transactionType = (
          processingMode ||
          getFieldValue(receipt, ['transaction_type', '類型', '类型']) ||
          getFieldValue(fields, ['transaction_type', '類型', '类型']) ||
          'AR'
        ).toUpperCase()
        const isDuplicate = Boolean(receipt?.is_duplicate)
        const duplicateOf = receipt?.duplicate_of
        const receiptMemoParts = [
          getFieldValue(receipt, ['memo', 'notes', '備註', '备注', 'description']),
          isDuplicate ? `[duplicate${duplicateOf ? ` of ${duplicateOf}` : ''}]` : '',
        ].filter(Boolean)
        spreadsheetData.push({
          id: `${rowIdPrefix}-txn${receiptIndex + 1}`,
          voucher_no: voucherNo,
          transaction_type: transactionType,
          amount: getFieldValue(receipt, ['total_amount', 'amount', 'amount_numeric', 'subtotal_amount']),
          currency: getFieldValue(receipt, ['currency']) || getFieldValue(fields, ['currency', '币别', '幣別']) || 'HKD',
          date:
            getFieldValue(receipt, ['date', 'invoice_date', 'transaction_date']) ||
            getFieldValue(fields, ['date', 'invoice_date', '日期', '發票日期', '发票日期', 'transaction_date']),
          payer:
            getFieldValue(receipt, ['payer', 'customer', 'client', '付款人']) ||
            getFieldValue(fields, ['payer', 'customer', 'client', '付款人', '买方', '買方']),
          payee:
            getFieldValue(receipt, [
              'payee',
              'vendor',
              'vendor_name',
              'supplier',
              'seller',
              'merchant_name',
              '收款人',
              '收款方',
            ]) ||
            getFieldValue(fields, [
              'payee',
              'vendor',
              'vendor_name',
              'supplier',
              'seller',
              'merchant_name',
              '收款人',
              '收款方',
              '開票方',
              '开票方',
            ]),
          bank:
            getFieldValue(receipt, ['bank_name', 'bank', '銀行', '银行']) ||
            getFieldValue(fields, ['bank_name', 'bank', '銀行', '银行']),
          category:
            getFieldValue(receipt, ['account_category', 'categorise', 'category', '科目']) ||
            getFieldValue(fields, ['account_category', 'categorise', 'category', '科目']),
          memo: receiptMemoParts.join(' '),
          confidence: formatConfidenceDisplay(
            getFieldValue(receipt, ['confidence']) || getFieldValue(fields, ['confidence']) || pageConfidence,
          ),
          file_position: resolveSpreadsheetFilePosition(receipt, fileName, filePosition),
          ...apTableSpreadsheetExtras(receipt, fields),
          ...provenanceSpreadsheetExtras(receipt, pageCtx),
        })
        rowIndex++
      })
      return
    }
    const receipts = Array.isArray(fields?.receipts) ? fields.receipts.filter((r: any) => r && typeof r === 'object') : []
    if (receipts.length > 0) {
      receipts.forEach((receipt: any, receiptIndex: number) => {
        const voucherNo = buildVoucherNo(
          getFieldValue(receipt, ['date', 'invoice_date', 'transaction_date', 'due_date']) ||
            getFieldValue(fields, ['date', 'invoice_date', '日期', 'transaction_date', 'due_date']),
        )
        const transactionType = (
          processingMode ||
          getFieldValue(receipt, ['transaction_type', '類型', '类型']) ||
          getFieldValue(fields, ['transaction_type', '類型', '类型']) ||
          'AR'
        ).toUpperCase()
        const isDuplicate = Boolean(receipt?.is_duplicate)
        const duplicateOf = receipt?.duplicate_of
        const receiptMemoParts = [
          getFieldValue(receipt, ['memo', 'notes', '備註', '备注', 'description']),
          isDuplicate ? `[duplicate${duplicateOf ? ` of ${duplicateOf}` : ''}]` : '',
        ].filter(Boolean)
        spreadsheetData.push({
          id: `${rowIdPrefix}-receipt${receiptIndex + 1}`,
          voucher_no: voucherNo,
          transaction_type: transactionType,
          amount: getFieldValue(receipt, ['total_amount', 'amount', 'amount_numeric', 'subtotal_amount']),
          currency: getFieldValue(receipt, ['currency']) || getFieldValue(fields, ['currency', '币别', '幣別']) || 'HKD',
          date:
            getFieldValue(receipt, ['date', 'invoice_date', 'transaction_date']) ||
            getFieldValue(fields, ['date', 'invoice_date', '日期', '發票日期', '发票日期', 'transaction_date']),
          payer:
            getFieldValue(receipt, ['payer', 'customer', 'client', '付款人']) ||
            getFieldValue(fields, ['payer', 'customer', 'client', '付款人', '买方', '買方']),
          payee:
            getFieldValue(receipt, [
              'payee',
              'vendor',
              'vendor_name',
              'supplier',
              'seller',
              'merchant_name',
              '收款人',
              '收款方',
            ]) ||
            getFieldValue(fields, [
              'payee',
              'vendor',
              'vendor_name',
              'supplier',
              'seller',
              'merchant_name',
              '收款人',
              '收款方',
              '開票方',
              '开票方',
            ]),
          bank:
            getFieldValue(receipt, ['bank_name', 'bank', '銀行', '银行']) ||
            getFieldValue(fields, ['bank_name', 'bank', '銀行', '银行']),
          category:
            getFieldValue(receipt, ['account_category', 'categorise', 'category', '科目']) ||
            getFieldValue(fields, ['account_category', 'categorise', 'category', '科目']),
          memo: receiptMemoParts.join(' '),
          confidence: formatConfidenceDisplay(
            getFieldValue(receipt, ['confidence']) || getFieldValue(fields, ['confidence']) || pageConfidence,
          ),
          file_position: resolveSpreadsheetFilePosition(receipt, fileName, filePosition),
          ...apTableSpreadsheetExtras(receipt, fields),
          ...provenanceSpreadsheetExtras(receipt, pageCtx),
        })
        rowIndex++
      })
      return
    }
    const voucherNo = buildVoucherNo(
      getFieldValue(fields, [
        'date',
        'invoice_date',
        '日期',
        '發票日期',
        '发票日期',
        'transaction_date',
        'due_date',
      ]),
    )
    const amount = getFieldValue(fields, [
      'amount_numeric',
      'amount',
      'total_amount',
      'subtotal_amount',
      'total',
      '總計',
      '总计',
      '金额',
      '金額',
    ])
    const payee = getFieldValue(fields, [
      'payee',
      '收款人',
      '收款方',
      'vendor',
      'vendor_name',
      'supplier',
      'seller',
      'merchant_name',
      '開票方',
      '开票方',
    ])
    const bank = getFieldValue(fields, ['bank_name', 'bank', '銀行', '银行'])
    const memo = getFieldValue(fields, ['memo', '備註', '备注', 'amount_words', '大寫', '大写', 'description'])
    const chequeNumber = getFieldValue(fields, ['cheque_number', '支票號碼', '支票号码', '支票號', '支票号'])
    const memoWithCheque = [memo, chequeNumber ? `支票號碼:${chequeNumber}` : ''].filter(Boolean).join(' ')
    const transactionType = (processingMode || getFieldValue(fields, ['transaction_type', '類型', '类型']) || 'AR').toUpperCase()
    spreadsheetData.push({
      id: rowIdPrefix,
      voucher_no: voucherNo,
      transaction_type: transactionType,
      amount,
      currency: getFieldValue(fields, ['currency', '币别', '幣別']) || 'HKD',
      date: getFieldValue(fields, ['date', 'invoice_date', '日期', '發票日期', '发票日期', 'transaction_date']),
      payer: getFieldValue(fields, ['payer', '付款人', 'customer', 'client', '买方', '買方']),
      payee,
      bank,
      category: getFieldValue(fields, ['account_category', 'categorise', 'category', '科目', 'account_code']),
      memo: memoWithCheque,
      confidence: formatConfidenceDisplay(getFieldValue(fields, ['confidence']) || pageConfidence),
      file_position: resolveSpreadsheetFilePosition(fields, fileName, filePosition),
      ...apTableSpreadsheetExtras(fields),
      ...provenanceSpreadsheetExtras(fields, pageCtx),
    })
    rowIndex++
  }

  const pages = Array.isArray(result?.pages) ? result.pages : []
  const pmUpper = String(processingMode || '').toUpperCase()
  const pagesCarryReceiptPayload =
    pmUpper === 'AP' &&
    pages.length >= 2 &&
    pages.some(pageHasNonemptyExtractedPayload)
  const usePerPageIteration =
    pages.length > 0 &&
    (result?.document_type === 'multi_page_pdf' ||
      (pmUpper === 'AP' && (rootExtractedLooksEmpty(result) || pagesCarryReceiptPayload)))
  if (usePerPageIteration) {
    for (const pageData of pages) {
      if (pageData?.status === 'error') {
        const pageLabel =
          pageData.receipt_index != null ? `P${pageData.page}-R${pageData.receipt_index}` : `P${pageData.page}`
        const errTail = String(pageData.error_detail || 'Unknown error').slice(0, 500)
        const errorPageCtx: PageCropContext = {
          page: pageData.page,
          receipt_index: pageData.receipt_index,
          receipt_bbox: pageData.receipt_bbox,
          image_quality: pageData.image_quality,
          receipt_instance_id: pageData.receipt_instance_id,
          segmentation_mode: pageData.segmentation_mode,
          crop_status: pageData.crop_status,
        }
        spreadsheetData.push({
          id: `${fileId}-page${pageData.page}-err${pageData.receipt_index ?? 0}`,
          voucher_no: '',
          transaction_type: (processingMode || 'AR').toUpperCase(),
          amount: '',
          currency: 'HKD',
          date: '',
          payer: '',
          payee: '',
          bank: '',
          category: '',
          memo: `[OCR failed] ${errTail}`,
          confidence: 'N/A',
          file_position: `${fileName} ${pageLabel}`,
          ocr_background_job_id: ocrBackgroundJobId || '',
          ocr_retry_page: pageData.page,
          due_date: '',
          invoice_number: '',
          vendor_tax_id: '',
          tax_amount: '',
          payment_status: '',
          ...provenanceSpreadsheetExtras({}, errorPageCtx),
        })
        rowIndex++
        continue
      }
      const fields = pageData?.ai_enhanced || pageData?.extracted_fields || {}
      const pageLabel =
        pageData.receipt_index != null ? `P${pageData.page}-R${pageData.receipt_index}` : `P${pageData.page}`
      appendRowsFromFields(
        fields,
        `${fileId}-page${pageData.page}-r${pageData.receipt_index ?? 0}`,
        pageData?.field_confidence || fields?.confidence,
        `${fileName} ${pageLabel}`,
        {
          page: pageData.page,
          receipt_index: pageData.receipt_index,
          receipt_bbox: pageData.receipt_bbox,
          image_quality: pageData.image_quality,
          receipt_instance_id: pageData.receipt_instance_id,
          segmentation_mode: pageData.segmentation_mode,
          crop_status: pageData.crop_status,
        },
      )
    }
  } else {
    const pageData = result
    const fields = pageData?.ai_enhanced || pageData?.extracted_fields || {}
    appendRowsFromFields(fields, fileId, pageData?.field_confidence || fields?.confidence, fileName)
  }

  return { spreadsheetData, nextRowIndex: rowIndex }
}

export function validArapType(raw: string | undefined, mode: string | undefined): 'AR' | 'AP' {
  const r = (raw || '').toUpperCase()
  if (r === 'AR' || r === 'AP') return r
  const t = (mode || 'AR').toUpperCase()
  return t === 'AP' ? 'AP' : 'AR'
}

export function buildArapId(baseId: string, txType: string): string {
  const type = (txType || 'AR').toUpperCase()
  const prefix = type === 'AP' ? 'AP-' : 'AR-'
  if (!baseId) {
    const now = new Date()
    const yy = String(now.getFullYear()).slice(-2)
    const mm = String(now.getMonth() + 1).padStart(2, '0')
    return prefix + `${yy}${mm}-001`
  }
  if (baseId.startsWith('AR-') || baseId.startsWith('AP-')) return baseId
  return prefix + baseId
}

export function spreadsheetRowsToArapTransactions(rows: SpreadsheetRow[], taskProcessingMode: string): ARAPTransaction[] {
  return rows.map((row) => {
    const txType = validArapType(String(row.transaction_type), taskProcessingMode)
    const r = row as Record<string, unknown>
    const taxRaw = r.tax_amount
    const taxNum =
      taxRaw != null && String(taxRaw).trim() !== ''
        ? parseFloat(String(taxRaw).replace(/,/g, '')) || null
        : null
    return {
      ...row,
      id_number: buildArapId(String(row.voucher_no || row.id || ''), txType),
      matched_id: '',
      source_file: row.file_position || '',
      transaction_type: txType,
      amount: row.amount != null ? parseFloat(String(row.amount).replace(/,/g, '')) || null : null,
      memo: row.memo || '',
      due_date: String(r.due_date ?? ''),
      invoice_number: String(r.invoice_number ?? ''),
      vendor_tax_id: String(r.vendor_tax_id ?? ''),
      tax_amount: taxNum,
      payment_status: String(r.payment_status ?? ''),
      ...(r.extraction_provenance && typeof r.extraction_provenance === 'object'
        ? { extraction_provenance: r.extraction_provenance as Record<string, unknown> }
        : {}),
    } as ARAPTransaction
  })
}
