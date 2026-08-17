/** AP chat composer "Insert option" values sent to OCR/VLM (English identifiers). */
export type ApVlmReceiptSignal = 'guess' | 'single_per_page' | 'multi_per_page' | 'single_span_pages'

export type ApVlmTablePreset = 'default' | 'ap_table'

export const AP_COMPOSER_LS_PREFIX = 'bookcomet_ap_composer_'

/** Shown on chip when a dimension is not chosen yet; opens hub to pick (distinct from slash lines). */
export const AP_COMPOSER_EMPTY_CHIP_LABEL = '+'

export const AP_RECEIPT_OPTIONS_ORDER: readonly ApVlmReceiptSignal[] = [
  'guess',
  'single_per_page',
  'multi_per_page',
  'single_span_pages',
]

export const AP_TABLE_OPTIONS_ORDER: readonly ApVlmTablePreset[] = ['default', 'ap_table']

/** Pills, chat prefix, and insert menu (Guess row) — keep in sync. */
export const AP_GUESS_MODE_SLASH = '/guess mode (auto-detect)'

/** Pills, chat prefix, and insert menu (Default table row). */
export const AP_TABLE_DEFAULT_SLASH = '/default columns'

export const AP_TABLE_AP_SLASH = '/AP table'

/** Slash labels shown as pills and prepended to outgoing chat text for AP mode. */
export const AP_RECEIPT_SLASH_LABEL: Record<Exclude<ApVlmReceiptSignal, 'guess'>, string> = {
  single_per_page: '/single receipt per page',
  multi_per_page: '/multi receipts per page',
  single_span_pages: '/single receipts at multi pages',
}

export function hasFullApComposerOptions(
  receipt: ApVlmReceiptSignal | null | undefined,
  table: ApVlmTablePreset | null | undefined,
): boolean {
  return receipt != null && table != null
}

export function receiptSlashOrDisplayLabel(signal: ApVlmReceiptSignal): string {
  return signal === 'guess' ? AP_GUESS_MODE_SLASH : AP_RECEIPT_SLASH_LABEL[signal]
}

export function tableSlashOrDisplayLabel(preset: ApVlmTablePreset): string {
  return preset === 'ap_table' ? AP_TABLE_AP_SLASH : AP_TABLE_DEFAULT_SLASH
}

/** Slash lines for OCR/VLM-bound user messages — only when both dimensions are picked. */
export function apComposerDisplayParts(
  receipt: ApVlmReceiptSignal,
  table: ApVlmTablePreset,
): { receiptLabel: string; tableLabel: string } {
  return {
    receiptLabel: receiptSlashOrDisplayLabel(receipt),
    tableLabel: tableSlashOrDisplayLabel(table),
  }
}

/** Label shown on the bundled chip slot (slash line if set, placeholder if not). */
export function apComposerChipReceiptUiLabel(receipt: ApVlmReceiptSignal | null): string {
  return receipt === null ? AP_COMPOSER_EMPTY_CHIP_LABEL : receiptSlashOrDisplayLabel(receipt)
}

export function apComposerChipTableUiLabel(table: ApVlmTablePreset | null): string {
  return table === null ? AP_COMPOSER_EMPTY_CHIP_LABEL : tableSlashOrDisplayLabel(table)
}

/** Chat prefix: both dimensions — empty string unless both are non-null. */
export function formatApComposerNotice(
  receipt: ApVlmReceiptSignal | null | undefined,
  table: ApVlmTablePreset | null | undefined,
): string {
  if (!hasFullApComposerOptions(receipt, table)) return ''
  const { receiptLabel, tableLabel } = apComposerDisplayParts(receipt, table)
  return `${receiptLabel} ${tableLabel}`
}

export function isApVlmReceiptSignal(value: unknown): value is ApVlmReceiptSignal {
  return (
    value === 'guess' ||
    value === 'single_per_page' ||
    value === 'multi_per_page' ||
    value === 'single_span_pages'
  )
}

export function isApVlmTablePreset(value: unknown): value is ApVlmTablePreset {
  return value === 'default' || value === 'ap_table'
}
