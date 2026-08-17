import { useCallback, useEffect, useRef, useState, type SetStateAction } from 'react'
import type { MatchedGroupRow } from '../../components/ReconciliationTable'
import { readReconAiChatMessages, writeReconAiChatMessages } from './reconGridHelpers'
import type { ReconAiMessage, ReconFilters, ReconGlMeta, ReconGlPatchSeeds, ReconGlRefetchSignal, ReconRawTxn } from './reconTypes'

const defaultFilters = (): ReconFilters => ({
  dateFrom: '',
  dateTo: '',
  bankAccount: '',
  ledgerType: 'all',
})

export function useReconWorkspaceState(companyId: string | null) {
  const [filters, setFilters] = useState<ReconFilters>(defaultFilters)
  const [selectedBankIds, setSelectedBankIds] = useState<string[]>([])
  const [selectedLedgerIds, setSelectedLedgerIds] = useState<string[]>([])
  const [reconMatchedGroups, setReconMatchedGroups] = useState<MatchedGroupRow[]>([])
  const reconMatchedGroupsRef = useRef<MatchedGroupRow[]>([])
  reconMatchedGroupsRef.current = reconMatchedGroups
  const [reconUnmatchedTxns, setReconUnmatchedTxns] = useState<{ bank: ReconRawTxn[]; ledger: ReconRawTxn[] }>({
    bank: [],
    ledger: [],
  })
  const [reconPartialTxns, setReconPartialTxns] = useState<ReconRawTxn[]>([])
  const [statusText, setStatusText] = useState('')
  const [loading, setLoading] = useState(false)
  const [aiMessages, setAiMessagesInner] = useState<ReconAiMessage[]>(() => readReconAiChatMessages(companyId))
  const loadedCompanyIdRef = useRef<string | null>(companyId)

  useEffect(() => {
    if (loadedCompanyIdRef.current === companyId) return
    loadedCompanyIdRef.current = companyId
    setAiMessagesInner(readReconAiChatMessages(companyId))
  }, [companyId])

  const setAiMessages = useCallback((action: SetStateAction<ReconAiMessage[]>) => {
    setAiMessagesInner(prev => {
      const next = typeof action === 'function' ? action(prev) : action
      writeReconAiChatMessages(companyId, next)
      return next
    })
  }, [companyId])

  const [aiThinking, setAiThinking] = useState(false)
  const [glJournalRefetchSignal, setGlJournalRefetchSignal] = useState<ReconGlRefetchSignal>(null)
  const [glApplyPatchSeeds, setGlApplyPatchSeeds] = useState<ReconGlPatchSeeds>(null)
  const [glStatusByGroupId, setGlStatusByGroupId] = useState<Record<string, string>>({})
  const [glVoucherNoByGroupId, setGlVoucherNoByGroupId] = useState<Record<string, string>>({})
  const glVoucherNoByGroupIdRef = useRef<Record<string, string>>({})
  glVoucherNoByGroupIdRef.current = glVoucherNoByGroupId
  const [glJournalMetaByGroupId, setGlJournalMetaByGroupId] = useState<Record<string, ReconGlMeta>>({})
  const [reconScrollTargetGroupId, setReconScrollTargetGroupId] = useState<string | null>(null)

  return {
    filters,
    setFilters,
    selectedBankIds,
    setSelectedBankIds,
    selectedLedgerIds,
    setSelectedLedgerIds,
    reconMatchedGroups,
    setReconMatchedGroups,
    reconMatchedGroupsRef,
    reconUnmatchedTxns,
    setReconUnmatchedTxns,
    reconPartialTxns,
    setReconPartialTxns,
    statusText,
    setStatusText,
    loading,
    setLoading,
    aiMessages,
    setAiMessages,
    aiThinking,
    setAiThinking,
    glJournalRefetchSignal,
    setGlJournalRefetchSignal,
    glApplyPatchSeeds,
    setGlApplyPatchSeeds,
    glStatusByGroupId,
    setGlStatusByGroupId,
    glVoucherNoByGroupId,
    setGlVoucherNoByGroupId,
    glVoucherNoByGroupIdRef,
    glJournalMetaByGroupId,
    setGlJournalMetaByGroupId,
    reconScrollTargetGroupId,
    setReconScrollTargetGroupId,
  }
}
