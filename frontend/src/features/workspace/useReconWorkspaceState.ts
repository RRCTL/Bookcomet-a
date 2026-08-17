import { useRef, useState } from 'react'
import type { SpreadsheetRow } from '../../components/EditableSpreadsheet'
import type { MatchedGroupRow } from '../../components/ReconciliationTable'
import type { GlJournalLinePayload, GlJournalPayload } from '../../services/reconciliation'

export type ReconRawTxn = Record<string, any>

export function useReconWorkspaceState() {
  const [reconSelectedSourceTxnIds, setReconSelectedSourceTxnIds] = useState<string[]>([])
  const [reconSelectedBankTxnIds, setReconSelectedBankTxnIds] = useState<string[]>([])
  const [reconMatchedRows, setReconMatchedRows] = useState<SpreadsheetRow[]>([])
  const [reconMatchedColumns, setReconMatchedColumns] = useState<string[]>([])
  const [reconUnmatchedRows, setReconUnmatchedRows] = useState<{ bank: SpreadsheetRow[]; ledger: SpreadsheetRow[] }>({
    bank: [],
    ledger: [],
  })
  const [reconUnmatchedTxns, setReconUnmatchedTxns] = useState<{ bank: ReconRawTxn[]; ledger: ReconRawTxn[] }>({
    bank: [],
    ledger: [],
  })
  const [reconMatchedGroups, setReconMatchedGroups] = useState<MatchedGroupRow[]>([])
  const reconMatchedGroupsRef = useRef<MatchedGroupRow[]>([])
  reconMatchedGroupsRef.current = reconMatchedGroups
  const [reconPartialTxns, setReconPartialTxns] = useState<ReconRawTxn[]>([])
  const [glJournalRefetchSignal, setGlJournalRefetchSignal] = useState<{
    nonce: number
    groupIds: string[]
  } | null>(null)
  const [glApplyPatchSeeds, setGlApplyPatchSeeds] = useState<{
    nonce: number
    byGroupId: Record<string, GlJournalPayload>
  } | null>(null)
  const [glStatusByGroupId, setGlStatusByGroupId] = useState<Record<string, string>>({})
  const [glVoucherNoByGroupId, setGlVoucherNoByGroupId] = useState<Record<string, string>>({})
  const glVoucherNoByGroupIdRef = useRef<Record<string, string>>({})
  const [glJournalMetaByGroupId, setGlJournalMetaByGroupId] = useState<
    Record<
      string,
      {
        journal_id: string
        voucher_no: string
        status: string
        lines: GlJournalLinePayload[]
      }
    >
  >({})
  const [reconScrollTargetGroupId, setReconScrollTargetGroupId] = useState<string | null>(null)
  const [reconScrollPendingGlDisplay, setReconScrollPendingGlDisplay] = useState<string | null>(null)
  const [reconAiAccountCodeConfirm, setReconAiAccountCodeConfirm] = useState<{
    messageId: string
    changes: { groupId: string; lineId: string; oldCode: string; newCode: string }[]
  } | null>(null)

  return {
    reconSelectedSourceTxnIds,
    setReconSelectedSourceTxnIds,
    reconSelectedBankTxnIds,
    setReconSelectedBankTxnIds,
    reconMatchedRows,
    setReconMatchedRows,
    reconMatchedColumns,
    setReconMatchedColumns,
    reconUnmatchedRows,
    setReconUnmatchedRows,
    reconUnmatchedTxns,
    setReconUnmatchedTxns,
    reconMatchedGroups,
    setReconMatchedGroups,
    reconMatchedGroupsRef,
    reconPartialTxns,
    setReconPartialTxns,
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
    reconScrollPendingGlDisplay,
    setReconScrollPendingGlDisplay,
    reconAiAccountCodeConfirm,
    setReconAiAccountCodeConfirm,
  }
}
