import { safeRandomUUID } from '../../utils/safeRandomUUID'

/** Stable client id for chat rows / synthetic records (spreadsheet, tasks, etc.). */
export function makeChatRecordId(): string {
  return `rec-${safeRandomUUID()}`
}
