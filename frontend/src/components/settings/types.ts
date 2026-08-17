import type { ReactNode } from 'react'
import type { ClassificationRuleApiRow } from '../../services/api'
import type { RuleMemoryMode } from './helpers'

export interface MemorySummary {
  content: string
  version: number
  updated_at: string | null
  updated_by_type: string
  is_active?: boolean
}

export interface ChartOfAccountItem {
  id?: string
  code: string
  name_en: string
  name_zh: string
  category_type: string
  allowed_modes: string[]
  is_default?: boolean
  opening_balance?: number | null
  opening_balance_dr_cr?: string | null
}

export type ExclusionRule = {
  id: string
  pattern: string
  pattern_type: string
  reason: string | null
  modes: string | null
  is_active: boolean
  hit_count: number
  last_hit_at: string | null
}

export interface SettingsProviderProps {
  children: ReactNode
  enabled: boolean
  allTransactions?: Array<{
    account_code?: string
    id_number?: string
    date?: string
    amount?: number | null
    transaction_type?: string
  }>
  onOpenWizard?: () => void
  onOpenChatWithMode?: (mode: string) => void
}

export type { ClassificationRuleApiRow, RuleMemoryMode }
