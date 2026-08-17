"""One-off script: extract Settings.tsx into settings/ layer."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "components" / "Settings.tsx"
OUT = ROOT / "src" / "components" / "settings"
lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)

def slice_lines(start: int, end: int) -> str:
    return "".join(lines[start - 1 : end])

OUT.mkdir(parents=True, exist_ok=True)

# helpers.tsx (lines 16-250, but types moved out; keep JSX helpers)
helpers = slice_lines(58, 250)
helpers_header = """import { type ReactNode } from 'react'
import type { MemorySummary } from './types'
import type { UserCompany } from '../../contexts/AuthContext'
import { ruleMemorySeenKey } from './helpers'

"""
# Fix - ruleMemorySeenKey should be in helpers.ts not helpers.tsx
# Split: pure helpers in helpers.ts, JSX in markdownComponents.tsx

helpers_ts = """import type { MemorySummary } from './types'

export const RULE_MEMORY_MODES = ['AR', 'AP', 'BANK', 'OTHER'] as const
export type RuleMemoryMode = (typeof RULE_MEMORY_MODES)[number]

export const RULE_MEMORY_MODE_LABELS: Record<RuleMemoryMode, string> = {
  AR: 'AR — Receivables',
  AP: 'AP — Payables',
  BANK: 'BANK — Bank Statements',
  OTHER: 'OTHER — Other',
}

export const SKILL_SLUG: Record<RuleMemoryMode, string> = {
  AR: 'ar-receivables',
  AP: 'ap-payables',
  BANK: 'bank-statements',
  OTHER: 'other',
}

export const KNOWLEDGE_NOTE_MAX = 2000

export function skillSkillFilename(mode: RuleMemoryMode): string {
  return `${SKILL_SLUG[mode]}.skill`
}

export function countRules(content: string): number {
  return content.split('\\n').filter(l => {
    const t = l.trim()
    return t.startsWith('- ') && t.includes('→') && !t.startsWith('*(')
  }).length
}

export function detectConflictCount(content: string): number {
  const lines = content.split('\\n').filter(l => {
    const t = l.trim()
    return t.startsWith('- ') && t.includes('→') && !t.startsWith('*(')
  })
  const vendors = new Map<string, number>()
  for (const line of lines) {
    const key = line.trim().slice(2).split('→')[0].trim().toLowerCase()
    vendors.set(key, (vendors.get(key) || 0) + 1)
  }
  return [...vendors.values()].filter(c => c > 1).length
}

export function formatRelativeTime(isoStr: string | null): string {
  if (!isoStr) return ''
  const diff = Date.now() - new Date(isoStr).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

export function workspaceSubscriptionLine(c: { subscriptionStatus: string; trialEndsAt?: string | null }): string {
  if (c.subscriptionStatus === 'trial' && c.trialEndsAt) {
    return `Trial · ends ${formatRelativeTime(c.trialEndsAt)}`
  }
  const m: Record<string, string> = {
    trial: 'Trial',
    active: 'Active',
    past_due: 'Past due',
    cancelled: 'Cancelled',
    pending: 'Pending',
  }
  return m[c.subscriptionStatus] ?? c.subscriptionStatus
}

export function ruleMemorySeenKey(companyId: string | null | undefined, mode: string): string {
  return `rm_seen_${companyId || 'default'}_${mode}`
}

export function isAINew(companyId: string | null | undefined, mode: string, summary?: MemorySummary): boolean {
  if (!summary || summary.updated_by_type !== 'ai') return false
  const lastSeen = localStorage.getItem(ruleMemorySeenKey(companyId, mode))
  if (!lastSeen || !summary.updated_at) return true
  return new Date(summary.updated_at) > new Date(lastSeen)
}

export function extractBehaviourPreview(md: string, maxLen = 110): string {
  const parts = md.split(/^##\\s+AI Behaviour Instructions\\s*$/im)
  if (parts.length < 2) return ''
  const body = (parts[1].split(/^##\\s+/m)[0] || '').trim()
  const line = body.split('\\n').find(l => l.trim().startsWith('- '))
  let s = line ? line.trim().replace(/^-\\s+/, '').trim() : body.split('\\n').find(l => l.trim())?.trim() || ''
  if (s.length > maxLen) s = `${s.slice(0, maxLen - 1)}…`
  return s
}
"""

(OUT / "helpers.ts").write_text(helpers_ts, encoding="utf-8")

markdown_tsx = slice_lines(128, 250)
markdown_tsx = "import { type ReactNode } from 'react'\n\n" + markdown_tsx.replace(
    "function renderInlineText", "export function renderInlineText"
).replace("function renderRuleLine", "function renderRuleLine").replace(
    "function MarkdownRenderer", "export function MarkdownRenderer"
).replace("function ManualSectionedView", "export function ManualSectionedView")

(OUT / "markdownComponents.tsx").write_text(markdown_tsx, encoding="utf-8")

types_ts = """import type { ClassificationRuleApiRow } from '../../services/api'
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
  children: React.ReactNode
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
"""

(OUT / "types.ts").write_text(types_ts, encoding="utf-8")

# Provider: lines 255-1246 with modifications
provider_body = slice_lines(255, 1246)
provider_body = provider_body.replace(
    "export function Settings({ isOpen, onClose, allTransactions = [], onOpenChatWithMode, openToMemoryTab, onOpenWizard }: SettingsProps) {",
    """export function SettingsProvider({
  children,
  enabled,
  allTransactions = [],
  onOpenWizard,
  onOpenChatWithMode,
}: SettingsProviderProps) {""",
)
provider_body = provider_body.replace(
    "  const [activeTab, setActiveTab] = useState<'company' | 'accounts' | 'rules'>('company')\n",
    "",
)
provider_body = provider_body.replace("if (!isOpen) return", "if (!enabled) return")
provider_body = provider_body.replace("[isOpen, activeCompanyId]", "[enabled, activeCompanyId]")
provider_body = provider_body.replace(
    """  useEffect(() => {
    if (isOpen && openToMemoryTab) {
      setActiveTab('rules')
      const loadSeq = settingsLoadSeq.current
      loadAllSummaries(loadSeq)
      loadExclusionRules(loadSeq)
      loadClassificationRules(loadSeq)
    }
    // This refresh is scoped to opening the rules tab for the current workspace.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, openToMemoryTab, activeCompanyId])

""",
    "",
)
provider_body = provider_body.replace(
    "  if (!isOpen) return null\n\n  return (",
    """  const value = {
    companies,
    activeCompany,
    activeCompanyId,
    switchCompany,
    refreshCompanies,
    allTransactions,
    onOpenWizard,
    onOpenChatWithMode,
    workspaceAddName, setWorkspaceAddName,
    workspaceAddBusy, setWorkspaceAddBusy,
    workspaceSearch, setWorkspaceSearch,
    deleteWorkspace, setDeleteWorkspace,
    deleteWorkspaceConfirm, setDeleteWorkspaceConfirm,
    deleteWorkspaceBusy, setDeleteWorkspaceBusy,
    workspaceErr, setWorkspaceErr,
    profileMd, setProfileMd,
    profileMdEditMode, setProfileMdEditMode,
    profileMdDraft, setProfileMdDraft,
    profileMdSaving, setProfileMdSaving,
    profileMdGenerating, setProfileMdGenerating,
    profileMdStatus, setProfileMdStatus,
    memoryMode, setMemoryMode,
    memoryContent, setMemoryContent,
    memoryVersion, setMemoryVersion,
    memoryLoading, setMemoryLoading,
    memorySaveStatus, setMemorySaveStatus,
    memoryError, setMemoryError,
    generateDesc, setGenerateDesc,
    generating, setGenerating,
    memorySummaries, setMemorySummaries,
    allSummariesLoading, setAllSummariesLoading,
    isMemoryEditMode, setIsMemoryEditMode,
    memoryDraft, setMemoryDraft,
    chartOfAccounts, setChartOfAccounts,
    coaActiveMode, setCoaActiveMode,
    coaEditingCode, setCoaEditingCode,
    coaEditDraft, setCoaEditDraft,
    coaNewDraft, setCoaNewDraft,
    coaAddingNew, setCoaAddingNew,
    coaError, setCoaError,
    coaTxnPanel, setCoaTxnPanel,
    coaBFDrafts, setCoaBFDrafts,
    coaBFSaving, setCoaBFSaving,
    manualContent, setManualContent,
    manualVersion, setManualVersion,
    manualLoading, setManualLoading,
    manualSaveStatus, setManualSaveStatus,
    manualError, setManualError,
    manualEditMode, setManualEditMode,
    manualDraft, setManualDraft,
    manualUpdatedAt, setManualUpdatedAt,
    manualHistory, setManualHistory,
    manualHistoryOpen, setManualHistoryOpen,
    MANUAL_SECTIONS,
    exclusionRules, setExclusionRules,
    exclusionLoading, setExclusionLoading,
    exclusionError, setExclusionError,
    newExclusion, setNewExclusion,
    addingExclusion, setAddingExclusion,
    savingExclusion, setSavingExclusion,
    mdModalOpen, setMdModalOpen,
    mdModalEditing, setMdModalEditing,
    classificationRules, setClassificationRules,
    classificationLoading, setClassificationLoading,
    classificationError, setClassificationError,
    knowledgeSearch, setKnowledgeSearch,
    skillSearch, setSkillSearch,
    skillGenerateOpen, setSkillGenerateOpen,
    knowledgeMenuId, setKnowledgeMenuId,
    crModalOpen, setCrModalOpen,
    crModalKind, setCrModalKind,
    crEditingId, setCrEditingId,
    crForm, setCrForm,
    contextModalOpen, setContextModalOpen,
    contextDraft, setContextDraft,
    contextSaving, setContextSaving,
    crSaving, setCrSaving,
    exclModalOpen, setExclModalOpen,
    exclEditingId, setExclEditingId,
    exclForm, setExclForm,
    exclSaving, setExclSaving,
    loadClassificationRules,
    loadExclusionRules,
    handleCreateExclusion,
    handleToggleExclusion,
    handleDeleteExclusion,
    openSkillModal,
    toggleClassificationRule,
    deleteClassificationRule,
    saveCrModal,
    saveContextModal,
    saveExclModal,
    loadRuleMemory,
    loadAllSummaries,
    loadMemorySummariesForAllModes,
    loadCompanyManual,
    reloadCoA,
    saveMemory,
    generateMemory,
    skillYamlBlock,
    loadProfileMd,
    saveProfileMd,
    generateProfileMd,
    handleCoaStartEdit,
    handleCoaSaveEdit,
    handleCoaDelete,
    handleCoaAddNew,
    handleBFBlur,
    coaReferencedCodes,
    activeIsOwner,
    filteredWorkspaces,
    addWorkspace,
    confirmDeleteWorkspace,
  }

  return (
    <SettingsContext.Provider value={value}>
      {children}
    </SettingsContext.Provider>
  )
}

// PLACEHOLDER_REMOVE_OLD_RETURN
""",
)

provider_header = """import { createContext, useContext, useState, useEffect, useMemo, useRef } from 'react'
import { apiFetch, api, type ClassificationRuleApiRow } from '../../services/api'
import { useAuth } from '../../contexts/AuthContext'
import type { SettingsProviderProps, MemorySummary, ChartOfAccountItem, ExclusionRule } from './types'
import {
  RULE_MEMORY_MODES,
  type RuleMemoryMode,
  SKILL_SLUG,
  KNOWLEDGE_NOTE_MAX,
  countRules,
  detectConflictCount,
  extractBehaviourPreview,
  isAINew,
  skillSkillFilename,
} from './helpers'

type SettingsContextValue = Record<string, unknown>

const SettingsContext = createContext<SettingsContextValue | null>(null)

export function useSettings(): SettingsContextValue {
  const ctx = useContext(SettingsContext)
  if (!ctx) throw new Error('useSettings must be used within SettingsProvider')
  return ctx
}

"""

# Remove old return JSX from provider - everything after PLACEHOLDER
if "// PLACEHOLDER_REMOVE_OLD_RETURN" in provider_body:
    provider_body = provider_body.split("// PLACEHOLDER_REMOVE_OLD_RETURN")[0]

(OUT / "SettingsProvider.tsx").write_text(provider_header + provider_body, encoding="utf-8")

# Panel JSX extractions
company_jsx = slice_lines(1284, 1455)
accounts_jsx = slice_lines(1459, 1763)
skills_jsx = slice_lines(1770, 1861) + "\n" + slice_lines(2167, 2258)
knowledge_jsx = slice_lines(1863, 2165) + "\n" + slice_lines(2260, 2443)
delete_modal = slice_lines(2448, 2499)

def wrap_panel(name: str, jsx: str, extra_imports: str = "", props_type: str = "") -> str:
    header = f"""import {{ useSettings }} from './SettingsProvider'
{extra_imports}

"""
    if props_type:
        header += f"export function {name}({props_type}) {{\n  const s = useSettings() as any\n"
    else:
        header += f"export function {name}() {{\n  const s = useSettings() as any\n"
    # Destructure commonly used - panels use `s.xxx` via replace
    body = jsx
    # Replace direct references with s. - this is fragile; panels will use destructuring at top
    return header + "  return (\n" + body + "\n  )\n}\n"

# Company panel with variant
company_panel = """import { useSettings } from './SettingsProvider'
import { BillingUsageSection } from '../BillingUsageSection'
import { ManualSectionedView } from './markdownComponents'
import { formatRelativeTime, workspaceSubscriptionLine } from './helpers'

export type CompanyProfilePanelProps = {
  variant?: 'modal' | 'setup'
  onClose?: () => void
  onOpenKnowledge?: () => void
}

export function CompanyProfilePanel({ variant = 'modal', onClose, onOpenKnowledge }: CompanyProfilePanelProps) {
  const s = useSettings() as Record<string, any>
  const {
    companies, activeCompany, switchCompany,
    workspaceErr, setWorkspaceErr,
    workspaceSearch, setWorkspaceSearch,
    workspaceAddName, setWorkspaceAddName,
    workspaceAddBusy, addWorkspace,
    activeIsOwner, filteredWorkspaces,
    deleteWorkspace, setDeleteWorkspace,
    deleteWorkspaceConfirm, setDeleteWorkspaceConfirm,
    deleteWorkspaceBusy, confirmDeleteWorkspace,
    manualLoading, manualError, manualContent, manualVersion, manualUpdatedAt,
    onOpenWizard,
  } = s

  return (
""" + company_jsx.replace("setActiveTab('rules')", "onOpenKnowledge?.()").replace(
    """                <button
                  type="button"
                  className="manus-btn manus-btn-outline"
                  onClick={() => {
                    onOpenKnowledge?.()
                    loadClassificationRules().catch(console.error)
                    loadExclusionRules().catch(console.error)
                  }}
                >
                  Open Knowledge
                </button>""",
    "{variant !== 'setup' && (\n                <button\n                  type=\"button\"\n                  className=\"manus-btn manus-btn-outline\"\n                  onClick={() => onOpenKnowledge?.()}\n                >\n                  Open Knowledge\n                </button>\n              )}"
) + """
""" + delete_modal + """
  )
}
"""

(OUT / "CompanyProfilePanel.tsx").write_text(company_panel, encoding="utf-8")

# Fix company panel - the replace for Open Knowledge button needs manual fix
# Read accounts jsx and create CoAPanel
coa_panel = """import { useSettings } from './SettingsProvider'

export function CoAPanel() {
  const s = useSettings() as Record<string, any>
  const {
    coaActiveMode, setCoaActiveMode, setCoaEditingCode, setCoaError,
    chartOfAccounts, coaEditingCode, coaEditDraft, setCoaEditDraft,
    coaNewDraft, setCoaNewDraft, coaAddingNew, setCoaAddingNew,
    coaError, coaTxnPanel, setCoaTxnPanel, coaBFDrafts, setCoaBFDrafts,
    coaBFSaving, coaReferencedCodes, allTransactions,
    handleCoaStartEdit, handleCoaSaveEdit, handleCoaDelete, handleCoaAddNew, handleBFBlur,
    reloadCoA,
  } = s

  return (
""" + accounts_jsx + """
  )
}
"""
(OUT / "CoAPanel.tsx").write_text(coa_panel, encoding="utf-8")

skills_panel = """import { useSettings } from './SettingsProvider'
import {
  RULE_MEMORY_MODES,
  RULE_MEMORY_MODE_LABELS,
  countRules,
  detectConflictCount,
  extractBehaviourPreview,
  isAINew,
  skillSkillFilename,
  formatRelativeTime,
} from './helpers'
import { MarkdownRenderer } from './markdownComponents'

export function SkillsMemoryPanel() {
  const s = useSettings() as Record<string, any>
  const {
    activeCompanyId, memorySummaries, allSummariesLoading, skillSearch, setSkillSearch,
    skillGenerateOpen, setSkillGenerateOpen, generateDesc, setGenerateDesc, generating,
    generateMemory, openSkillModal, mdModalOpen, setMdModalOpen, memoryMode,
    mdModalEditing, setMdModalEditing, memoryLoading, memoryContent, memoryVersion,
    memoryDraft, setMemoryDraft, isMemoryEditMode, setIsMemoryEditMode,
    memorySaveStatus, saveMemory, memoryError, setMemoryError, skillYamlBlock,
    onOpenChatWithMode,
  } = s

  return (
""" + skills_jsx + """
  )
}
"""
(OUT / "SkillsMemoryPanel.tsx").write_text(skills_panel, encoding="utf-8")

knowledge_panel = """import { useSettings } from './SettingsProvider'
import { KNOWLEDGE_NOTE_MAX, formatRelativeTime } from './helpers'
import type { ClassificationRuleApiRow } from './types'

export function KnowledgePanel() {
  const s = useSettings() as Record<string, any>

  return (
""" + knowledge_jsx + """
  )
}
"""
(OUT / "KnowledgePanel.tsx").write_text(knowledge_panel, encoding="utf-8")

print("Extracted to", OUT)
