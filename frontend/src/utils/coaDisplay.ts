import type { ChartOfAccountItem } from '../types/reconciliation'

/** Frontend chrome is English-only. Keep name_zh as stored data, not UI. */
export function preferZhCoaName(): boolean {
  return false
}

export function coaLocalizedName(account: { name_en?: string; name_zh?: string }): string {
  const en = (account.name_en ?? '').trim()
  const zh = (account.name_zh ?? '').trim()
  return en || zh
}

/** Dropdown label: `"code name"`. */
export function coaOptionLabel(account: ChartOfAccountItem): string {
  const code = (account.code ?? '').trim()
  if (!code) return ''
  const name = coaLocalizedName(account)
  return name ? `${code} ${name}` : code
}

export function coaNameByCodeMap(accounts: ChartOfAccountItem[]): Map<string, string> {
  const map = new Map<string, string>()
  for (const a of accounts) {
    const code = (a.code ?? '').trim()
    if (!code) continue
    const name = coaLocalizedName(a)
    if (name) map.set(code, name)
  }
  return map
}

export function coaCodeSet(accounts: ChartOfAccountItem[]): Set<string> {
  return new Set(accounts.map(a => (a.code ?? '').trim()).filter(Boolean))
}

/** Return code only if it exists in the CoA; otherwise blank. */
export function validCoaCode(code: string | null | undefined, codes: Set<string>): string {
  const c = String(code ?? '').trim()
  return c && codes.has(c) ? c : ''
}

/** Parse `"code name..."` option strings into a code → name map. */
export function coaNameMapFromOptionLabels(options: string[]): Map<string, string> {
  const map = new Map<string, string>()
  for (const opt of options) {
    const trimmed = String(opt ?? '').trim()
    if (!trimmed) continue
    const space = trimmed.indexOf(' ')
    if (space <= 0) continue
    const code = trimmed.slice(0, space).trim()
    const name = trimmed.slice(space + 1).trim()
    if (code && name) map.set(code, name)
  }
  return map
}
