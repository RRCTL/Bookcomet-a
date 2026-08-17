import { createContext, useContext, useState, useEffect, useMemo, useRef } from 'react'
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
  ruleMemorySeenKey,
  RULE_MEMORY_MODE_LABELS,
} from './helpers'

type SettingsContextValue = Record<string, unknown>

const SettingsContext = createContext<SettingsContextValue | null>(null)

export function useSettings(): SettingsContextValue {
  const ctx = useContext(SettingsContext)
  if (!ctx) throw new Error('useSettings must be used within SettingsProvider')
  return ctx
}

export function SettingsProvider({
  children,
  enabled,
  allTransactions = [],
  onOpenWizard,
  onOpenChatWithMode,
}: SettingsProviderProps) {
  const { companies, activeCompany, switchCompany, refreshCompanies } = useAuth()
  const activeCompanyId = activeCompany?.id ?? 'default'
  const settingsLoadSeq = useRef(0)
  const [workspaceAddName, setWorkspaceAddName] = useState('')
  const [workspaceAddBusy, setWorkspaceAddBusy] = useState(false)
  const [workspaceSearch, setWorkspaceSearch] = useState('')
  const [deleteWorkspace, setDeleteWorkspace] = useState<{ id: string; name: string } | null>(null)
  const [deleteWorkspaceConfirm, setDeleteWorkspaceConfirm] = useState('')
  const [deleteWorkspaceBusy, setDeleteWorkspaceBusy] = useState(false)
  const [workspaceErr, setWorkspaceErr] = useState('')

  // ── Company Profile MD state ──────────────────────────────────────────────
  const [profileMd, setProfileMd] = useState('')
  const [profileMdEditMode, setProfileMdEditMode] = useState(false)
  const [profileMdDraft, setProfileMdDraft] = useState('')
  const [profileMdSaving, setProfileMdSaving] = useState(false)
  const [profileMdGenerating, setProfileMdGenerating] = useState(false)
  const [profileMdStatus, setProfileMdStatus] = useState<'idle' | 'saved' | 'error'>('idle')
  // ── Rule Memory state ──────────────────────────────────────────────────────
  const [memoryMode, setMemoryMode] = useState<RuleMemoryMode>('AR')
  const [memoryContent, setMemoryContent] = useState('')
  const [memoryVersion, setMemoryVersion] = useState(1)
  const [memoryLoading, setMemoryLoading] = useState(false)
  const [memorySaveStatus, setMemorySaveStatus] = useState<'idle' | 'saving' | 'success' | 'error'>('idle')
  const [memoryError, setMemoryError] = useState('')
  const [generateDesc, setGenerateDesc] = useState('')
  const [generating, setGenerating] = useState(false)
  const [memorySummaries, setMemorySummaries] = useState<Partial<Record<RuleMemoryMode, MemorySummary>>>({})
  const [allSummariesLoading, setAllSummariesLoading] = useState(false)
  const [isMemoryEditMode, setIsMemoryEditMode] = useState(false)
  const [memoryDraft, setMemoryDraft] = useState('')

  const [chartOfAccounts, setChartOfAccounts] = useState<{
    AR: ChartOfAccountItem[]
    AP: ChartOfAccountItem[]
    BANK: ChartOfAccountItem[]
  }>({
    AR: [],
    AP: [],
    BANK: [],
  })

  // CoA CRUD state
  const [coaActiveMode, setCoaActiveMode] = useState<'AR' | 'AP' | 'BANK'>('AR')
  const [coaEditingCode, setCoaEditingCode] = useState<string | null>(null)
  const [coaEditDraft, setCoaEditDraft] = useState<{ name_en: string; name_zh: string; category_type: string; allowed_modes: string[]; opening_balance: string; opening_balance_dr_cr: string }>({ name_en: '', name_zh: '', category_type: 'expense', allowed_modes: [], opening_balance: '', opening_balance_dr_cr: 'Dr' })
  const [coaNewDraft, setCoaNewDraft] = useState({ code: '', name_en: '', name_zh: '', category_type: 'expense', allowed_modes: ['AR'] as string[], opening_balance: '', opening_balance_dr_cr: 'Dr' })
  const [coaAddingNew, setCoaAddingNew] = useState(false)
  const [coaError, setCoaError] = useState('')
  const [coaTxnPanel, setCoaTxnPanel] = useState<string | null>(null) // code being inspected
  const [coaBFDrafts, setCoaBFDrafts] = useState<Record<string, { amount: string; drCr: string }>>({})
  const [coaBFSaving, setCoaBFSaving] = useState<Record<string, boolean>>({})

  // ── Company Manual state ────────────────────────────────────────────────────
  const [manualContent, setManualContent] = useState('')
  const [manualVersion, setManualVersion] = useState(1)
  const [manualLoading, setManualLoading] = useState(false)
  const [manualSaveStatus, setManualSaveStatus] = useState<'idle' | 'saving' | 'success' | 'error'>('idle')
  const [manualError, setManualError] = useState('')
  const [manualEditMode, setManualEditMode] = useState(false)
  const [manualDraft, setManualDraft] = useState('')
  const [manualUpdatedAt, setManualUpdatedAt] = useState<string | null>(null)
  const [manualHistory, setManualHistory] = useState<Array<{ version: number; saved_at: string | null; saved_by_type: string; content_preview: string }>>([])
  const [manualHistoryOpen, setManualHistoryOpen] = useState(false)
  const [wizardCompleted, setWizardCompleted] = useState(false)

  const MANUAL_SECTIONS = ['Key Clients', 'Key Vendors', 'Risk & Compliance Rules', 'Seasonal Patterns', 'Company Glossary']

  // ── Exclusion List state ────────────────────────────────────────────────────
  type ExclusionRule = {
    id: string; pattern: string; pattern_type: string; reason: string | null;
    modes: string | null; is_active: boolean; hit_count: number;
    last_hit_at: string | null;
  }
  const [exclusionRules, setExclusionRules] = useState<ExclusionRule[]>([])
  const [exclusionLoading, setExclusionLoading] = useState(false)
  const [exclusionError, setExclusionError] = useState('')
  const [newExclusion, setNewExclusion] = useState({ pattern: '', pattern_type: 'keyword', reason: '', modes: '' })
  const [addingExclusion, setAddingExclusion] = useState(false)
  const [savingExclusion, setSavingExclusion] = useState(false)

  const [mdModalOpen, setMdModalOpen] = useState(false)
  const [mdModalEditing, setMdModalEditing] = useState(false)
  const [classificationRules, setClassificationRules] = useState<ClassificationRuleApiRow[]>([])
  const [classificationLoading, setClassificationLoading] = useState(false)
  const [classificationError, setClassificationError] = useState('')
  const [knowledgeSearch, setKnowledgeSearch] = useState('')
  const [skillSearch, setSkillSearch] = useState('')
  const [skillGenerateOpen, setSkillGenerateOpen] = useState(false)
  const [knowledgeMenuId, setKnowledgeMenuId] = useState<string | null>(null)

  const [crModalOpen, setCrModalOpen] = useState(false)
  const [crModalKind, setCrModalKind] = useState<'classification' | 'knowledge'>('knowledge')
  const [crEditingId, setCrEditingId] = useState<string | null>(null)
  const [crForm, setCrForm] = useState({
    rule_name: '',
    pattern_type: 'keyword' as 'keyword' | 'vendor' | 'amount',
    pattern: '',
    notes: '',
    document_type: '',
    use_when: '',
    content: '',
  })
  const [contextModalOpen, setContextModalOpen] = useState(false)
  const [contextDraft, setContextDraft] = useState({ use_when: '', body: '' })
  const [contextSaving, setContextSaving] = useState(false)
  const [crSaving, setCrSaving] = useState(false)

  const [exclModalOpen, setExclModalOpen] = useState(false)
  const [exclEditingId, setExclEditingId] = useState<string | null>(null)
  const [exclForm, setExclForm] = useState({ pattern: '', pattern_type: 'keyword', reason: '', modes: '' })
  const [exclSaving, setExclSaving] = useState(false)

  const isCurrentSettingsLoad = (loadSeq: number) => loadSeq === settingsLoadSeq.current

  const resetCompanyScopedSettings = () => {
    setMemoryMode('AR')
    setMemoryContent('')
    setMemoryVersion(1)
    setMemoryError('')
    setMemorySaveStatus('idle')
    setGenerateDesc('')
    setMemorySummaries({})
    setIsMemoryEditMode(false)
    setMemoryDraft('')
    setMdModalEditing(false)
    setSkillGenerateOpen(false)
    setKnowledgeMenuId(null)

    setManualContent('')
    setManualVersion(1)
    setManualError('')
    setManualSaveStatus('idle')
    setManualEditMode(false)
    setManualDraft('')
    setManualUpdatedAt(null)
    setManualHistory([])
    setManualHistoryOpen(false)
    setWizardCompleted(false)

    setClassificationRules([])
    setClassificationError('')
    setExclusionRules([])
    setExclusionError('')
    setContextModalOpen(false)
    setCrModalOpen(false)
    setCrEditingId(null)
    setExclModalOpen(false)
    setExclEditingId(null)

    setChartOfAccounts({ AR: [], AP: [], BANK: [] })
    setCoaError('')
    setCoaEditingCode(null)
    setCoaAddingNew(false)
    setCoaTxnPanel(null)
    setCoaBFDrafts({})
    setCoaBFSaving({})
  }

  const loadClassificationRules = async (loadSeq = settingsLoadSeq.current) => {
    setClassificationLoading(true)
    setClassificationError('')
    try {
      const rules = await api.listClassificationRules()
      if (isCurrentSettingsLoad(loadSeq)) {
        setClassificationRules(rules)
      }
    } catch (e) {
      if (isCurrentSettingsLoad(loadSeq)) {
        setClassificationError(String(e))
      }
    } finally {
      if (isCurrentSettingsLoad(loadSeq)) {
        setClassificationLoading(false)
      }
    }
  }

  const loadExclusionRules = async (loadSeq = settingsLoadSeq.current) => {
    setExclusionLoading(true)
    setExclusionError('')
    try {
      const rules = await api.listExclusionRules()
      if (isCurrentSettingsLoad(loadSeq)) {
        setExclusionRules(rules)
      }
    } catch (e) {
      if (isCurrentSettingsLoad(loadSeq)) {
        setExclusionError(String(e))
      }
    } finally {
      if (isCurrentSettingsLoad(loadSeq)) {
        setExclusionLoading(false)
      }
    }
  }

  const handleCreateExclusion = async () => {
    if (!newExclusion.pattern.trim()) return
    setSavingExclusion(true)
    try {
      await api.createExclusionRule({
        pattern: newExclusion.pattern.trim(),
        pattern_type: newExclusion.pattern_type,
        reason: newExclusion.reason.trim() || undefined,
        modes: newExclusion.modes.trim() || undefined,
      })
      setNewExclusion({ pattern: '', pattern_type: 'keyword', reason: '', modes: '' })
      setAddingExclusion(false)
      await loadExclusionRules()
    } catch (e) {
      setExclusionError(String(e))
    } finally {
      setSavingExclusion(false)
    }
  }

  const handleToggleExclusion = async (rule: ExclusionRule) => {
    try {
      await api.updateExclusionRule(rule.id, { is_active: !rule.is_active })
      await loadExclusionRules()
    } catch (e) {
      setExclusionError(String(e))
    }
  }

  const handleDeleteExclusion = async (id: string) => {
    if (!confirm('Delete this exclusion rule?')) return
    try {
      await api.deleteExclusionRule(id)
      setExclusionRules(prev => prev.filter(r => r.id !== id))
    } catch (e) {
      setExclusionError(String(e))
    }
  }

  const openSkillModal = async (mode: RuleMemoryMode) => {
    setKnowledgeMenuId(null)
    setMemoryMode(mode)
    setMdModalOpen(true)
    setMdModalEditing(false)
    setMemoryError('')
    await loadRuleMemory(mode)
  }

  const toggleClassificationRule = async (r: ClassificationRuleApiRow) => {
    try {
      await api.patchClassificationRule(r.id, { is_active: !r.is_active })
      await loadClassificationRules()
    } catch (e) {
      setClassificationError(String(e))
    }
  }

  const deleteClassificationRule = async (id: string): Promise<boolean> => {
    if (!confirm('Delete this rule?')) return false
    try {
      await api.deleteClassificationRule(id)
      await loadClassificationRules()
      return true
    } catch (e) {
      setClassificationError(String(e))
      return false
    }
  }

  const saveCrModal = async () => {
    setCrSaving(true)
    setClassificationError('')
    try {
      if (crModalKind === 'knowledge') {
        if (!crForm.rule_name.trim() || !crForm.content.trim()) {
          setCrSaving(false)
          return
        }
        if (crForm.content.length > KNOWLEDGE_NOTE_MAX) {
          setClassificationError(`Content must be at most ${KNOWLEDGE_NOTE_MAX} characters.`)
          setCrSaving(false)
          return
        }
        if (crEditingId) {
          await api.patchClassificationRule(crEditingId, {
            rule_name: crForm.rule_name.trim(),
            use_when: crForm.use_when.trim() || undefined,
            content: crForm.content.trim(),
          })
        } else {
          await api.createClassificationRule({
            rule_type: 'knowledge_article',
            rule_name: crForm.rule_name.trim(),
            use_when: crForm.use_when.trim() || undefined,
            content: crForm.content.trim(),
          })
        }
      } else {
        if (!crForm.rule_name.trim() || !crForm.pattern.trim()) {
          setCrSaving(false)
          return
        }
        if (crEditingId) {
          await api.patchClassificationRule(crEditingId, {
            rule_name: crForm.rule_name.trim(),
            pattern_type: crForm.pattern_type,
            pattern: crForm.pattern.trim(),
            notes: crForm.notes.trim() || undefined,
            document_type: crForm.document_type.trim() || undefined,
          })
        } else {
          await api.createClassificationRule({
            rule_type: 'company_custom',
            rule_name: crForm.rule_name.trim(),
            pattern_type: crForm.pattern_type,
            pattern: crForm.pattern.trim(),
            notes: crForm.notes.trim() || undefined,
            document_type: crForm.document_type.trim() || undefined,
          })
        }
      }
      setCrModalOpen(false)
      setCrEditingId(null)
      await loadClassificationRules()
    } catch (e) {
      setClassificationError(String(e))
    } finally {
      setCrSaving(false)
    }
  }

  const saveContextModal = async () => {
    if (!contextDraft.body.trim()) return
    setContextSaving(true)
    setClassificationError('')
    try {
      await api.upsertCompanyKnowledgeContext(
        contextDraft.body.trim(),
        contextDraft.use_when.trim() || null,
      )
      setContextModalOpen(false)
      await loadClassificationRules()
    } catch (e) {
      setClassificationError(String(e))
    } finally {
      setContextSaving(false)
    }
  }

  const saveExclModal = async () => {
    if (!exclForm.pattern.trim()) return
    setExclSaving(true)
    setExclusionError('')
    try {
      if (exclEditingId) {
        await api.updateExclusionRule(exclEditingId, {
          pattern: exclForm.pattern.trim(),
          pattern_type: exclForm.pattern_type,
          reason: exclForm.reason.trim() || undefined,
          modes: exclForm.modes.trim() || undefined,
        })
      } else {
        await api.createExclusionRule({
          pattern: exclForm.pattern.trim(),
          pattern_type: exclForm.pattern_type,
          reason: exclForm.reason.trim() || undefined,
          modes: exclForm.modes.trim() || undefined,
        })
      }
      setExclModalOpen(false)
      setExclEditingId(null)
      await loadExclusionRules()
    } catch (e) {
      setExclusionError(String(e))
    } finally {
      setExclSaving(false)
    }
  }

  const loadCompanyManual = async (loadSeq = settingsLoadSeq.current) => {
    setManualLoading(true)
    setManualError('')
    setManualEditMode(false)
    try {
      const data = await api.getCompanyManual()
      if (isCurrentSettingsLoad(loadSeq)) {
        setManualContent(data.content || '')
        setManualVersion(data.version || 1)
        setManualUpdatedAt(data.updated_at)
      }
    } catch (e) {
      if (isCurrentSettingsLoad(loadSeq)) {
        setManualError(String(e))
      }
    } finally {
      if (isCurrentSettingsLoad(loadSeq)) {
        setManualLoading(false)
      }
    }
  }

  const loadWizardStatus = async (loadSeq = settingsLoadSeq.current) => {
    try {
      const status = await api.companyManualExists()
      if (isCurrentSettingsLoad(loadSeq)) {
        setWizardCompleted(status.wizardCompleted)
      }
    } catch {
      if (isCurrentSettingsLoad(loadSeq)) {
        setWizardCompleted(false)
      }
    }
  }

  const handleSaveManual = async () => {
    const body = manualDraft.trim()
    if (!body) return
    setManualSaveStatus('saving')
    setManualError('')
    try {
      const contextUseWhen =
        classificationRules.find(r => r.rule_type === 'company_context')?.use_when ?? null
      await api.upsertCompanyKnowledgeContext(body, contextUseWhen)
      const result = await api.saveCompanyManual(manualDraft, manualVersion)
      setManualContent(manualDraft)
      setManualVersion(result.version)
      setManualUpdatedAt(result.updated_at)
      setManualEditMode(false)
      setManualSaveStatus('success')
      await loadClassificationRules()
      setTimeout(() => setManualSaveStatus('idle'), 2000)
    } catch (e) {
      setManualError(String(e))
      setManualSaveStatus('error')
      setTimeout(() => setManualSaveStatus('idle'), 3000)
    }
  }

  const startEditCompanyKnowledge = (seed?: string) => {
    setManualError('')
    setManualDraft(seed ?? '')
    setManualEditMode(true)
  }

  const loadManualHistory = async () => {
    const history = await api.getCompanyManualHistory()
    setManualHistory(history)
    setManualHistoryOpen(true)
  }

  const handleRestoreManualVersion = async (version: number) => {
    try {
      const result = await api.restoreCompanyManualVersion(version)
      setManualHistoryOpen(false)
      setManualVersion(result.new_version)
      await loadCompanyManual()
    } catch (e) {
      setManualError(String(e))
    }
  }

  const loadRuleMemory = async (mode: RuleMemoryMode, loadSeq = settingsLoadSeq.current) => {
    setMemoryLoading(true)
    setMemoryError('')
    setIsMemoryEditMode(false)
    try {
      const data = await api.getRuleMemory(mode)
      const summary: MemorySummary = {
        content: data.content || '',
        version: data.version || 1,
        updated_at: data.updated_at,
        updated_by_type: data.updated_by_type,
        is_active: data.is_active !== false,
      }
      if (isCurrentSettingsLoad(loadSeq)) {
        setMemorySummaries(prev => ({ ...prev, [mode]: summary }))
        setMemoryContent(data.content || '')
        setMemoryVersion(data.version || 1)
        setMemoryMode(mode)
        localStorage.setItem(ruleMemorySeenKey(activeCompanyId, mode), new Date().toISOString())
      }
    } catch (e) {
      if (isCurrentSettingsLoad(loadSeq)) {
        setMemoryError(String(e))
      }
    } finally {
      if (isCurrentSettingsLoad(loadSeq)) {
        setMemoryLoading(false)
      }
    }
  }

  const loadAllSummaries = async (
    loadSeq = settingsLoadSeq.current,
    selectedMode: RuleMemoryMode = memoryMode,
  ) => {
    setAllSummariesLoading(true)
    try {
      const results = await Promise.all(
        RULE_MEMORY_MODES.map(m =>
          api.getRuleMemory(m)
            .then(data => ({ mode: m, data }))
            .catch(() => null)
        )
      )
      const summaries: Partial<Record<RuleMemoryMode, MemorySummary>> = {}
      for (const result of results) {
        if (result) {
          summaries[result.mode] = {
            content: result.data.content || '',
            version: result.data.version || 1,
            updated_at: result.data.updated_at,
            updated_by_type: result.data.updated_by_type,
            is_active: result.data.is_active !== false,
          }
        }
      }
      if (isCurrentSettingsLoad(loadSeq)) {
        setMemorySummaries(summaries)
        if (summaries[selectedMode]) {
          setMemoryContent(summaries[selectedMode]!.content)
          setMemoryVersion(summaries[selectedMode]!.version)
        }
      }
    } catch (e) {
      if (isCurrentSettingsLoad(loadSeq)) {
        console.error('Failed to load summaries:', e)
      }
    } finally {
      if (isCurrentSettingsLoad(loadSeq)) {
        setAllSummariesLoading(false)
      }
    }
  }

  const switchMemoryMode = (mode: RuleMemoryMode) => {
    if (mode === memoryMode && memorySummaries[mode]) {
      setMemoryContent(memorySummaries[mode]!.content)
      setMemoryVersion(memorySummaries[mode]!.version)
      setMemoryMode(mode)
      setIsMemoryEditMode(false)
      localStorage.setItem(ruleMemorySeenKey(activeCompanyId, mode), new Date().toISOString())
      setMemorySummaries(prev => ({
        ...prev,
        [mode]: { ...prev[mode]!, updated_by_type: prev[mode]?.updated_by_type === 'ai' ? 'user' : (prev[mode]?.updated_by_type ?? 'user') }
      }))
      return
    }
    loadRuleMemory(mode)
  }

  const handleSaveMemory = async () => {
    setMemorySaveStatus('saving')
    setMemoryError('')
    try {
      const data = await api.saveRuleMemory(memoryMode, memoryDraft, memoryVersion)
      setMemoryVersion(data.version)
      setMemoryContent(memoryDraft)
      setMemorySummaries(prev => ({
        ...prev,
        [memoryMode]: {
          content: memoryDraft,
          version: data.version,
          updated_at: data.updated_at,
          updated_by_type: 'user',
          is_active: memorySummaries[memoryMode]?.is_active !== false,
        },
      }))
      setMemorySaveStatus('success')
      setIsMemoryEditMode(false)
      setMdModalEditing(false)
      setTimeout(() => setMemorySaveStatus('idle'), 2000)
    } catch (e) {
      setMemoryError(String(e))
      setMemorySaveStatus('error')
      setTimeout(() => setMemorySaveStatus('idle'), 3000)
    }
  }

  const handleRestoreLastVersion = async () => {
    try {
      const hist = await api.getRuleMemoryHistory(memoryMode)
      if (hist.length === 0) {
        setMemoryError('No previous versions available.')
        return
      }
      const latest = hist[0]
      const dateStr = latest.saved_at ? new Date(latest.saved_at).toLocaleDateString() : 'unknown date'
      if (!window.confirm(`Restore to v${latest.version} (${dateStr})?`)) return
      await api.restoreRuleMemoryVersion(memoryMode, latest.version)
      await loadRuleMemory(memoryMode)
      await loadAllSummaries()
    } catch (e) {
      setMemoryError(String(e))
    }
  }

  const generateMemory = async () => {
    if (!generateDesc.trim()) return
    setGenerating(true)
    setMemoryError('')
    try {
      const data = await api.generateRuleMemory(memoryMode, generateDesc)
      setMemoryContent(data.content)
      setMemoryVersion(data.version)
      setMemorySummaries(prev => ({
        ...prev,
        [memoryMode]: {
          content: data.content,
          version: data.version,
          updated_at: new Date().toISOString(),
          updated_by_type: 'ai',
          is_active: memorySummaries[memoryMode]?.is_active !== false,
        },
      }))
      setGenerateDesc('')
      setIsMemoryEditMode(false)
      setMdModalEditing(false)
    } catch (e) {
      setMemoryError(String(e))
    } finally {
      setGenerating(false)
    }
  }


  const reloadCoA = async (loadSeq = settingsLoadSeq.current) => {
    const [arR, apR, bankR] = await Promise.all([
      apiFetch('/reconciliation/chart-of-accounts?mode=AR'),
      apiFetch('/reconciliation/chart-of-accounts?mode=AP'),
      apiFetch('/reconciliation/chart-of-accounts?mode=BANK'),
    ])
    const arCoa   = arR.ok   ? await arR.json()   : { accounts: [] }
    const apCoa   = apR.ok   ? await apR.json()   : { accounts: [] }
    const bankCoa = bankR.ok ? await bankR.json() : { accounts: [] }
    const allAccounts: ChartOfAccountItem[] = [
      ...(Array.isArray(arCoa?.accounts)   ? arCoa.accounts   : []),
      ...(Array.isArray(apCoa?.accounts)   ? apCoa.accounts   : []),
      ...(Array.isArray(bankCoa?.accounts) ? bankCoa.accounts : []),
    ]
    if (!isCurrentSettingsLoad(loadSeq)) return
    // Seed inline B/F drafts from fresh server data (don't overwrite codes being edited)
    setCoaBFDrafts(prev => {
      const next = { ...prev }
      for (const a of allAccounts) {
        if (/^[123]/.test(a.code) && !(a.code in next)) {
          next[a.code] = {
            amount: a.opening_balance != null ? String(a.opening_balance) : '',
            drCr: a.opening_balance_dr_cr || 'Dr',
          }
        }
      }
      return next
    })
    setChartOfAccounts({
      AR:   Array.isArray(arCoa?.accounts)   ? arCoa.accounts   : [],
      AP:   Array.isArray(apCoa?.accounts)   ? apCoa.accounts   : [],
      BANK: Array.isArray(bankCoa?.accounts) ? bankCoa.accounts : [],
    })
  }

  const conflictCount = useMemo(() => detectConflictCount(memoryContent), [memoryContent])

  const contextRule = useMemo(
    () => classificationRules.find(r => r.rule_type === 'company_context'),
    [classificationRules],
  )

  const contextMatchesSearch = useMemo(() => {
    const q = knowledgeSearch.trim().toLowerCase()
    if (!contextRule) return false
    if (!q) return true
    return (
      contextRule.rule_name.toLowerCase().includes(q) ||
      (contextRule.content || '').toLowerCase().includes(q) ||
      (contextRule.use_when || '').toLowerCase().includes(q)
    )
  }, [contextRule, knowledgeSearch])

  const formatKnowledgeDate = (iso: string | null | undefined) => {
    if (!iso) return ''
    try {
      return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
    } catch {
      return ''
    }
  }

  const knowledgeRows = useMemo(() => {
    const q = knowledgeSearch.trim().toLowerCase()
    const matches = (s: string | null | undefined) => !q || !!(s && s.toLowerCase().includes(q))

    const cls = classificationRules.filter(r => r.rule_type !== 'company_context')
    const clsFiltered = cls.filter(
      r =>
        matches(r.rule_name) ||
        matches(r.pattern) ||
        matches(r.notes) ||
        matches(r.document_type ?? '') ||
        matches(r.use_when ?? '') ||
        matches(r.content ?? ''),
    )
    const excl = exclusionRules.filter(r => matches(r.pattern) || matches(r.reason))
    return [
      ...clsFiltered.map(r => ({ kind: 'classification' as const, id: `c:${r.id}`, rule: r })),
      ...excl.map(r => ({ kind: 'exclusion' as const, id: `e:${r.id}`, rule: r })),
    ]
  }, [classificationRules, exclusionRules, knowledgeSearch])

  const visibleSkillModes = useMemo(() => {
    const q = skillSearch.trim().toLowerCase()
    if (!q) return RULE_MEMORY_MODES
    return RULE_MEMORY_MODES.filter(m => {
      const s = memorySummaries[m]
      const prev = (s?.content && extractBehaviourPreview(s.content, 200)) || ''
      return (
        m.toLowerCase().includes(q) ||
        SKILL_SLUG[m].includes(q) ||
        RULE_MEMORY_MODE_LABELS[m].toLowerCase().includes(q) ||
        prev.toLowerCase().includes(q)
      )
    })
  }, [skillSearch, memorySummaries])

  const toggleSkillActive = async (mode: RuleMemoryMode, e: React.MouseEvent) => {
    e.stopPropagation()
    const prev = memorySummaries[mode]
    const nextActive = !(prev?.is_active !== false)
    try {
      await api.patchRuleMemoryActive(mode, nextActive)
      await loadAllSummaries()
    } catch (err) {
      setMemoryError(String(err))
    }
  }

  const skillYamlBlock = (mode: RuleMemoryMode) => {
    const s = memorySummaries[mode]
    const desc = (s?.content && extractBehaviourPreview(s.content, 120)) || '—'
    return `name: ${SKILL_SLUG[mode]}\ndescription: "${desc.replace(/"/g, '\\"')}"\nmode: ${mode}\nversion: ${s?.version ?? 1}\nrules_count: ${s?.content ? countRules(s.content) : 0}\nupdated: ${s?.updated_at ?? '—'}\n`
  }

  // Load settings from backend when the modal opens or the active workspace changes.
  useEffect(() => {
    if (!enabled) return

    const loadSeq = settingsLoadSeq.current + 1
    settingsLoadSeq.current = loadSeq
    resetCompanyScopedSettings()

    const loadSettings = async () => {
      try {
        await reloadCoA(loadSeq)
        await loadRuleMemory('AR', loadSeq)
        // Background-load all mode summaries, manual, and exclusion rules
        loadAllSummaries(loadSeq, 'AR').catch(console.error)
        loadCompanyManual(loadSeq).catch(console.error)
        loadWizardStatus(loadSeq).catch(console.error)
        loadExclusionRules(loadSeq).catch(console.error)
        loadClassificationRules(loadSeq).catch(console.error)
      } catch (error) {
        if (isCurrentSettingsLoad(loadSeq)) {
          console.error('Failed to load settings from backend:', error)
        }
      }
    }

    loadSettings()
    // Company-scoped loaders are intentionally keyed by modal visibility and active workspace.
    // Including the inline loader functions would retrigger this reset/reload on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, activeCompanyId])

  // ── Company Profile MD handlers ──────────────────────────────────────────────

  const loadProfileMd = async () => {
    try {
      const resp = await apiFetch('/company/profile-md')
      if (resp.ok) {
        const data = await resp.json()
        const md = data.profile_md || ''
        setProfileMd(md)
        setProfileMdDraft(md)
      }
    } catch (e) {
      console.warn('[ProfileMD] load failed', e)
    }
  }

  const saveProfileMd = async () => {
    setProfileMdSaving(true)
    setProfileMdStatus('idle')
    try {
      const resp = await apiFetch('/company/profile-md', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile_md: profileMdDraft }),
      })
      if (!resp.ok) throw new Error(resp.statusText)
      setProfileMd(profileMdDraft)
      setProfileMdEditMode(false)
      setProfileMdStatus('saved')
      setTimeout(() => setProfileMdStatus('idle'), 3000)
    } catch (e) {
      setProfileMdStatus('error')
    } finally {
      setProfileMdSaving(false)
    }
  }

  const generateProfileMd = async () => {
    setProfileMdGenerating(true)
    setProfileMdStatus('idle')
    try {
      const resp = await apiFetch('/company/profile-md/generate', { method: 'POST' })
      if (!resp.ok) throw new Error(resp.statusText)
      const data = await resp.json()
      const md = data.profile_md || ''
      setProfileMd(md)
      setProfileMdDraft(md)
      setProfileMdStatus('saved')
      setTimeout(() => setProfileMdStatus('idle'), 3000)
    } catch (e) {
      setProfileMdStatus('error')
    } finally {
      setProfileMdGenerating(false)
    }
  }

  // ── CoA CRUD handlers ────────────────────────────────────────────────────────

  const handleCoaStartEdit = (item: ChartOfAccountItem) => {
    setCoaEditingCode(item.code)
    setCoaEditDraft({
      name_en: item.name_en,
      name_zh: item.name_zh,
      category_type: item.category_type,
      allowed_modes: [...item.allowed_modes],
      opening_balance: item.opening_balance != null ? String(item.opening_balance) : '',
      opening_balance_dr_cr: item.opening_balance_dr_cr || 'Dr',
    })
    setCoaError('')
  }

  const handleCoaSaveEdit = async () => {
    if (!coaEditingCode) return
    try {
      const isFinPos = /^[123]/.test(coaEditingCode)
      const obVal = coaEditDraft.opening_balance.trim()
      const body: Record<string, unknown> = {
        name_en: coaEditDraft.name_en,
        name_zh: coaEditDraft.name_zh,
        category_type: coaEditDraft.category_type,
        allowed_modes: coaEditDraft.allowed_modes,
      }
      if (isFinPos) {
        body.opening_balance = obVal !== '' ? parseFloat(obVal) : null
        body.opening_balance_dr_cr = obVal !== '' ? coaEditDraft.opening_balance_dr_cr : null
      }
      await apiFetch(`/reconciliation/chart-of-accounts/${encodeURIComponent(coaEditingCode)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      setCoaEditingCode(null)
      setCoaError('')
      await reloadCoA()
    } catch (e: any) {
      setCoaError(e?.message || 'Update failed')
    }
  }

  const handleCoaDelete = async (code: string) => {
    const referencedCodes = allTransactions.map(t => t.account_code ?? '').filter(Boolean)
    try {
      const resp = await apiFetch(`/reconciliation/chart-of-accounts/${encodeURIComponent(code)}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ referenced_codes: referencedCodes }),
      })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }))
        setCoaError((err as any).detail || 'Delete failed')
        return
      }
      setCoaError('')
      await reloadCoA()
    } catch (e: any) {
      setCoaError(e?.message || 'Delete failed')
    }
  }

  const handleCoaAddNew = async () => {
    const { code, name_en, name_zh, category_type, allowed_modes, opening_balance, opening_balance_dr_cr } = coaNewDraft
    if (!code.trim() || !name_en.trim()) {
      setCoaError('Account code and English name are required')
      return
    }
    try {
      const resp = await apiFetch('/reconciliation/chart-of-accounts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          code: code.trim(),
          name_en: name_en.trim(),
          name_zh: name_zh.trim(),
          category_type,
          allowed_modes,
          opening_balance: /^[123]/.test(code.trim()) && opening_balance.trim() !== '' ? parseFloat(opening_balance) : null,
          opening_balance_dr_cr: /^[123]/.test(code.trim()) && opening_balance.trim() !== '' ? opening_balance_dr_cr : null,
        }),
      })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }))
        setCoaError((err as any).detail || 'Create failed')
        return
      }
      setCoaNewDraft({ code: '', name_en: '', name_zh: '', category_type: 'expense', allowed_modes: [coaActiveMode], opening_balance: '', opening_balance_dr_cr: 'Dr' })
      setCoaAddingNew(false)
      setCoaError('')
      await reloadCoA()
    } catch (e: any) {
      setCoaError(e?.message || 'Create failed')
    }
  }

  /** Auto-save a B/F change directly from the display row (no full edit mode required). */
  const handleBFBlur = async (item: ChartOfAccountItem) => {
    const draft = coaBFDrafts[item.code]
    if (!draft) return
    const newAmount = draft.amount.trim() !== '' ? parseFloat(draft.amount) : null
    const newDrCr   = draft.drCr || 'Dr'
    const unchanged =
      (newAmount === item.opening_balance || (newAmount == null && item.opening_balance == null)) &&
      newDrCr === (item.opening_balance_dr_cr || 'Dr')
    if (unchanged) return
    setCoaBFSaving(s => ({ ...s, [item.code]: true }))
    try {
      const resp = await apiFetch(`/reconciliation/chart-of-accounts/${encodeURIComponent(item.code)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name_en: item.name_en,
          name_zh: item.name_zh ?? '',
          category_type: item.category_type,
          allowed_modes: item.allowed_modes,
          opening_balance: newAmount,
          opening_balance_dr_cr: newAmount != null ? newDrCr : null,
          clear_opening_balance: newAmount == null,
        }),
      })
      if (resp.ok) {
        await reloadCoA()
      } else {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }))
        setCoaError((err as any).detail || 'B/F save failed')
      }
    } catch (e: any) {
      setCoaError(e?.message || 'B/F save failed')
    } finally {
      setCoaBFSaving(s => ({ ...s, [item.code]: false }))
    }
  }

  const coaReferencedCodes = new Set(allTransactions.map(t => t.account_code ?? '').filter(Boolean))

  const activeIsOwner = companies.find(c => c.id === activeCompany?.id)?.role === 'owner'
  const filteredWorkspaces = useMemo(() => {
    const q = workspaceSearch.trim().toLowerCase()
    if (!q) return companies
    return companies.filter(c => c.name.toLowerCase().includes(q))
  }, [companies, workspaceSearch])

  const addWorkspace = async () => {
    const n = workspaceAddName.trim()
    if (!n || !activeIsOwner) return
    setWorkspaceErr('')
    setWorkspaceAddBusy(true)
    try {
      const row = await api.createCompany(n)
      await refreshCompanies()
      switchCompany(row.id)
      setWorkspaceAddName('')
    } catch (e) {
      setWorkspaceErr(e instanceof Error ? e.message : String(e))
    } finally {
      setWorkspaceAddBusy(false)
    }
  }

  const confirmDeleteWorkspace = async () => {
    if (!deleteWorkspace) return
    const typed = deleteWorkspaceConfirm.trim()
    if (typed !== deleteWorkspace.name.trim()) return
    setWorkspaceErr('')
    setDeleteWorkspaceBusy(true)
    try {
      const r = await api.deleteCompany(deleteWorkspace.id, typed)
      const deletedId = deleteWorkspace.id
      setDeleteWorkspace(null)
      setDeleteWorkspaceConfirm('')
      await refreshCompanies()
      if (activeCompany?.id === deletedId && r.suggested_company_id) {
        switchCompany(r.suggested_company_id)
      }
    } catch (e) {
      setWorkspaceErr(e instanceof Error ? e.message : String(e))
    } finally {
      setDeleteWorkspaceBusy(false)
    }
  }

  const value: SettingsContextValue = {
    companies,
    activeCompany,
    activeCompanyId,
    switchCompany,
    refreshCompanies,
    allTransactions,
    onOpenWizard,
    onOpenChatWithMode,
    workspaceAddName,
    setWorkspaceAddName,
    workspaceAddBusy,
    setWorkspaceAddBusy,
    workspaceSearch,
    setWorkspaceSearch,
    deleteWorkspace,
    setDeleteWorkspace,
    deleteWorkspaceConfirm,
    setDeleteWorkspaceConfirm,
    deleteWorkspaceBusy,
    setDeleteWorkspaceBusy,
    workspaceErr,
    setWorkspaceErr,
    profileMd,
    setProfileMd,
    profileMdEditMode,
    setProfileMdEditMode,
    profileMdDraft,
    setProfileMdDraft,
    profileMdSaving,
    setProfileMdSaving,
    profileMdGenerating,
    setProfileMdGenerating,
    profileMdStatus,
    setProfileMdStatus,
    memoryMode,
    setMemoryMode,
    memoryContent,
    setMemoryContent,
    memoryVersion,
    setMemoryVersion,
    memoryLoading,
    setMemoryLoading,
    memorySaveStatus,
    setMemorySaveStatus,
    memoryError,
    setMemoryError,
    generateDesc,
    setGenerateDesc,
    generating,
    setGenerating,
    memorySummaries,
    setMemorySummaries,
    allSummariesLoading,
    setAllSummariesLoading,
    isMemoryEditMode,
    setIsMemoryEditMode,
    memoryDraft,
    setMemoryDraft,
    chartOfAccounts,
    setChartOfAccounts,
    coaActiveMode,
    setCoaActiveMode,
    coaEditingCode,
    setCoaEditingCode,
    coaEditDraft,
    setCoaEditDraft,
    coaNewDraft,
    setCoaNewDraft,
    coaAddingNew,
    setCoaAddingNew,
    coaError,
    setCoaError,
    coaTxnPanel,
    setCoaTxnPanel,
    coaBFDrafts,
    setCoaBFDrafts,
    coaBFSaving,
    setCoaBFSaving,
    manualContent,
    setManualContent,
    manualVersion,
    setManualVersion,
    manualLoading,
    setManualLoading,
    manualSaveStatus,
    setManualSaveStatus,
    manualError,
    setManualError,
    manualEditMode,
    setManualEditMode,
    manualDraft,
    setManualDraft,
    manualUpdatedAt,
    setManualUpdatedAt,
    manualHistory,
    setManualHistory,
    manualHistoryOpen,
    setManualHistoryOpen,
    MANUAL_SECTIONS,
    exclusionRules,
    setExclusionRules,
    exclusionLoading,
    setExclusionLoading,
    exclusionError,
    setExclusionError,
    newExclusion,
    setNewExclusion,
    addingExclusion,
    setAddingExclusion,
    savingExclusion,
    setSavingExclusion,
    mdModalOpen,
    setMdModalOpen,
    mdModalEditing,
    setMdModalEditing,
    classificationRules,
    setClassificationRules,
    classificationLoading,
    setClassificationLoading,
    classificationError,
    setClassificationError,
    knowledgeSearch,
    setKnowledgeSearch,
    skillSearch,
    setSkillSearch,
    skillGenerateOpen,
    setSkillGenerateOpen,
    knowledgeMenuId,
    setKnowledgeMenuId,
    crModalOpen,
    setCrModalOpen,
    crModalKind,
    setCrModalKind,
    crEditingId,
    setCrEditingId,
    crForm,
    setCrForm,
    contextModalOpen,
    setContextModalOpen,
    contextDraft,
    setContextDraft,
    contextSaving,
    setContextSaving,
    crSaving,
    setCrSaving,
    exclModalOpen,
    setExclModalOpen,
    exclEditingId,
    setExclEditingId,
    exclForm,
    setExclForm,
    exclSaving,
    setExclSaving,
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
    loadCompanyManual,
    loadWizardStatus,
    wizardCompleted,
    setWizardCompleted,
    reloadCoA,
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
    handleSaveManual,
    startEditCompanyKnowledge,
    loadManualHistory,
    handleRestoreManualVersion,
    switchMemoryMode,
    handleSaveMemory,
    handleRestoreLastVersion,
    conflictCount,
    contextRule,
    contextMatchesSearch,
    formatKnowledgeDate,
    knowledgeRows,
    visibleSkillModes,
    toggleSkillActive,
  }

  return (
    <SettingsContext.Provider value={value}>
      {children}
    </SettingsContext.Provider>
  )
}
