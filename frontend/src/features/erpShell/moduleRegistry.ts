import type { ProcessingMode } from '../../components/ModeSelector'

export type ModuleKind = 'processing' | 'grid' | 'recon' | 'journal' | 'setup'

export interface ModuleDef {
  /** Stable internal id - never changes even if the displayed number does. */
  id: string
  /** Displayed tree number (may have gaps when modules are disabled). */
  number: number
  label: string
  crumb: string
  kind: ModuleKind
  /** Processing mode this grid module is scoped to (folder typing + merge guard). */
  mode?: ProcessingMode
  /** Phase 1 visibility. Disabled modules stay in code for the next phase. */
  enabled: boolean
}

/**
 * Module registry. Modules map to the existing processing modes
 * (see components/ModeSelector). Reports stay disabled until that module ships.
 */
export const MODULES: ModuleDef[] = [
  { id: 'processing', number: 1, label: 'Processing', crumb: 'Automation / OCR & Extraction', kind: 'processing', enabled: true },
  { id: 'ap', number: 2, label: 'Accounts Payable', crumb: 'Books / Payables', kind: 'grid', mode: 'AP', enabled: true },
  { id: 'ar', number: 3, label: 'Accounts Receivable', crumb: 'Books / Receivables', kind: 'grid', mode: 'AR', enabled: true },
  { id: 'bank', number: 4, label: 'Bank', crumb: 'Books / Bank', kind: 'grid', mode: 'BANK', enabled: true },
  { id: 'recon', number: 5, label: 'Reconciliation', crumb: 'Books / Bank / Reconcile', kind: 'recon', mode: 'RECON', enabled: true },
  { id: 'gl', number: 6, label: 'General Ledger', crumb: 'Books / GL / Journals', kind: 'journal', enabled: true },
  { id: 'other', number: 7, label: 'Other', crumb: 'Books / Register', kind: 'grid', mode: 'OTHER', enabled: false },
  { id: 'reports', number: 8, label: 'Reports', crumb: 'Books / Reports', kind: 'grid', mode: 'REPORT', enabled: false },
  { id: 'setup', number: 10, label: 'Setup', crumb: 'Configuration', kind: 'setup', enabled: true },
]

export const DEFAULT_MODULE_ID = 'processing'

export function enabledModules(): ModuleDef[] {
  return MODULES.filter(m => m.enabled)
}

export function getModule(id: string): ModuleDef | undefined {
  return MODULES.find(m => m.id === id)
}
