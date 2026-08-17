/** English labels for spreadsheet chrome. Row keys stay Chinese for OCR/CSV compatibility. */
export const SHEET_COLUMN_LABELS: Record<string, string> = {
  憑證號: 'Voucher no.',
  類型: 'Type',
  存入: 'Deposit',
  提取: 'Withdrawal',
  原幣結餘: 'Balance',
  幣別: 'Currency',
  日期: 'Date',
  付款人: 'Payer',
  收款人: 'Payee',
  銀行: 'Bank',
  賬戶類型: 'Account type',
  備註: 'Memo',
  信心度: 'Confidence',
  檔案位置: 'File',
  分類: 'Category',
  金額: 'Amount',
  匹配狀態: 'Match status',
  配對ID: 'Match ID',
  'Bank Mode 憑證號': 'Bank voucher no.',
  'AR/AP Mode 憑證號': 'AR/AP voucher no.',
  'Bank Mode 類型': 'Bank type',
  'AR/AP Mode 類型': 'AR/AP type',
  'Bank Mode 備註': 'Bank memo',
  'AR/AP Mode 備註': 'AR/AP memo',
  'AI 配對原因': 'AI match reason',
  AR覆核: 'AR review',
}

export function sheetColumnLabel(field: string): string {
  return SHEET_COLUMN_LABELS[field] ?? field
}
