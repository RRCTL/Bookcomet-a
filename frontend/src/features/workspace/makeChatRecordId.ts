/** Stable client id for chat rows / synthetic records (spreadsheet, tasks, etc.). */
export function makeChatRecordId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `rec-${crypto.randomUUID()}`
  }
  return `rec-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}
