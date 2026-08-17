export function formatFilePageCount(pageCount?: number | null): string | null {
  if (pageCount == null || pageCount < 1) return null
  return pageCount === 1 ? '1 page' : `${pageCount} pages`
}
