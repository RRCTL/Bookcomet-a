import { reconciliationApi } from '../../services/reconciliation'
import type { ARAPTransaction } from '../../components/ARAPReview'
import type { BankTransaction } from '../../components/BankStatementReview'
import type { ChartOfAccountItem } from '../../types/reconciliation'
import { coaNameByCodeMap } from '../../utils/coaDisplay'

/** Map CoA code -> localized account name for Category column display. */
export function coaNameByCode(accounts: ChartOfAccountItem[]): Map<string, string> {
  return coaNameByCodeMap(accounts)
}

export async function loadCoaNameByCode(mode: string): Promise<Map<string, string>> {
  const { accounts } = await reconciliationApi.getChartOfAccounts(mode)
  return coaNameByCode(accounts ?? [])
}

export type DeployTxnInput = {
  id_number?: string
  date?: string
  amount?: number | null
  payer?: string
  payee?: string
  memo?: string
  transaction_type?: string
  category?: string
}

export async function runDeployAccountCodes(
  mode: string,
  arapTransactions?: ARAPTransaction[],
  bankTransactions?: BankTransaction[],
): Promise<Map<string, string>> {
  const isBank = mode === 'BANK'
  const txns: DeployTxnInput[] = isBank
    ? (bankTransactions ?? []).map(t => ({
        id_number: t.id_number,
        date: t.date,
        amount: t.deposit ?? t.withdrawal ?? null,
        payer: '',
        payee: t.particulars ?? '',
        memo: t.particulars ?? '',
        transaction_type: 'BANK',
        category: '',
      }))
    : (arapTransactions ?? []).map(t => ({
        id_number: t.id_number,
        date: t.date,
        amount: t.amount ?? null,
        payer: t.payer ?? '',
        payee: t.payee ?? '',
        memo: t.memo ?? '',
        transaction_type: t.transaction_type ?? mode,
        category: t.category ?? '',
      }))

  if (txns.length === 0) return new Map()

  const arTxns = txns.filter(t => t.transaction_type === 'AR')
  const apTxns = txns.filter(t => t.transaction_type === 'AP')
  const bankTxns = txns.filter(t => t.transaction_type === 'BANK')
  const hasMixed = !isBank && arTxns.length > 0 && apTxns.length > 0

  let results: Array<{ id_number: string; suggested_code: string | null; confidence: number }> = []

  if (isBank) {
    const res = await reconciliationApi.deployAccountCodes(bankTxns.length > 0 ? bankTxns : txns, 'BANK')
    results = res.results
  } else if (hasMixed) {
    const [arRes, apRes] = await Promise.all([
      arTxns.length > 0 ? reconciliationApi.deployAccountCodes(arTxns, 'AR') : Promise.resolve({ results: [] }),
      apTxns.length > 0 ? reconciliationApi.deployAccountCodes(apTxns, 'AP') : Promise.resolve({ results: [] }),
    ])
    results = [...arRes.results, ...apRes.results]
  } else {
    const effectiveMode = apTxns.length > 0 && arTxns.length === 0 ? 'AP' : mode
    const res = await reconciliationApi.deployAccountCodes(txns, effectiveMode)
    results = res.results
  }

  return new Map(results.map(r => [r.id_number ?? '', r.suggested_code ?? '']))
}

export function applyCodeMapToArap(
  rows: ARAPTransaction[],
  codeMap: Map<string, string>,
  nameByCode?: Map<string, string>,
): ARAPTransaction[] {
  return rows.map(t => {
    const code = codeMap.get(t.id_number ?? '')
    if (!code) return t
    return {
      ...t,
      account_code: code,
      category: nameByCode?.get(code) || t.category || '',
    }
  })
}

export function applyCodeMapToBank(
  rows: BankTransaction[],
  codeMap: Map<string, string>,
  nameByCode?: Map<string, string>,
): BankTransaction[] {
  return rows.map(t => {
    const code = codeMap.get(t.id_number ?? '')
    if (!code) return t
    return {
      ...t,
      account_code: code,
      category: nameByCode?.get(code) || t.category || '',
    }
  })
}
