const SOURCE_PAGE_SUFFIX = / P\d+\b/

export function hasBankSourcePageSuffix(sourceFile: string | undefined | null): boolean {
  return SOURCE_PAGE_SUFFIX.test(String(sourceFile ?? '').trim())
}

/** Strip trailing page suffix for grouping multi-page rows from one PDF. */
export function bankSourceFileStem(sourceFile: string | undefined | null): string {
  return String(sourceFile ?? '').trim().replace(SOURCE_PAGE_SUFFIX, '')
}

/** Build or enrich bank row source label, e.g. `statement.pdf P3`. */
export function formatBankSourceFile(
  fileName: string,
  pageNum: unknown,
  existing?: string | null,
): string {
  const page = Number(pageNum)
  const hasPage = Number.isFinite(page) && page >= 1
  const existingTrim = String(existing ?? '').trim()
  const fileTrim = String(fileName ?? '').trim()

  if (existingTrim && hasBankSourcePageSuffix(existingTrim)) return existingTrim

  const base = existingTrim || fileTrim
  if (hasPage) {
    if (!base) return fileTrim ? `${fileTrim} P${page}` : `P${page}`
    return `${base} P${page}`
  }
  return base
}
